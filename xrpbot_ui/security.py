"""Seguridad de la interfaz: usuarios, sesiones, CSRF, límite de intentos y auditoría.

Todo vive en state/ui.sqlite (separado de las bases de datos del bot).
- Contraseñas con Argon2id (argon2-cffi).
- Sesión: token aleatorio en cookie HttpOnly + SameSite=Strict; en la base de
  datos solo se guarda su hash SHA-256. Caduca por inactividad y por edad máxima.
- CSRF: token por sesión, obligatorio en la cabecera X-CSRF-Token de todo POST.
- Límite de intentos: N fallos por usuario o por IP bloquean el login durante M minutos.
- Modo control: hay que volver a introducir la contraseña; dura unos minutos.
- Auditoría: cada login, desbloqueo y comando queda registrado (no se borra desde la interfaz).
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, csrf TEXT NOT NULL,
    created_at TEXT NOT NULL, last_seen TEXT NOT NULL, control_until TEXT, ip TEXT);
CREATE TABLE IF NOT EXISTS login_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
    username TEXT, ip TEXT, ok INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, username TEXT,
    ip TEXT, action TEXT NOT NULL, detail TEXT, result TEXT);
"""

MIN_PASSWORD_LEN = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(d: datetime) -> str:
    return d.isoformat()


def _parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginError(Exception):
    def __init__(self, message: str, locked: bool = False):
        super().__init__(message)
        self.locked = locked


@dataclass
class Session:
    token_hash: str
    username: str
    csrf: str
    created_at: datetime
    last_seen: datetime
    control_until: datetime | None
    clock: object = field(default=_now, repr=False, compare=False)

    def control_active(self, now: datetime | None = None) -> bool:
        return bool(self.control_until and (now or self.clock()) < self.control_until)

    def control_seconds_left(self, now: datetime | None = None) -> int:
        now = now or self.clock()
        if not self.control_active(now):
            return 0
        return round((self.control_until - now).total_seconds())


class SecurityStore:
    def __init__(self, path: str | Path, *, idle_minutes: int = 30, max_hours: int = 12,
                 max_attempts: int = 5, lockout_minutes: int = 15, clock=_now) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.ph = PasswordHasher()
        self.idle = timedelta(minutes=idle_minutes)
        self.max_age = timedelta(hours=max_hours)
        self.max_attempts = max_attempts
        self.lockout = timedelta(minutes=lockout_minutes)
        self.clock = clock

    # ------------------------------------------------------------ usuarios
    def has_users(self) -> bool:
        return self.conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def set_password(self, username: str, password: str) -> None:
        if len(password) < MIN_PASSWORD_LEN:
            raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres")
        with self.lock:
            self.conn.execute(
                "INSERT INTO users(username, pw_hash, created_at) VALUES(?,?,?) "
                "ON CONFLICT(username) DO UPDATE SET pw_hash=excluded.pw_hash",
                (username, self.ph.hash(password), _iso(self.clock())))
            # cambiar la contraseña invalida todas las sesiones
            self.conn.execute("DELETE FROM sessions WHERE username=?", (username,))
        self.audit(username, "", "password_set", "", "ok")

    def _verify(self, username: str, password: str) -> bool:
        row = self.conn.execute("SELECT pw_hash FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            # mismo coste que un usuario existente: no revela qué usuarios existen
            try:
                self.ph.verify(self.ph.hash("x" * MIN_PASSWORD_LEN), password + "!")
            except VerificationError:
                pass
            return False
        try:
            return self.ph.verify(row[0], password)
        except (VerifyMismatchError, VerificationError):
            return False

    # ------------------------------------------------------------ login
    def _recent_failures(self, column: str, value: str) -> int:
        since = _iso(self.clock() - self.lockout)
        row = self.conn.execute(
            f"SELECT COUNT(*) FROM login_attempts WHERE {column}=? AND ok=0 AND ts>=? "  # noqa: S608
            f"AND id > COALESCE((SELECT MAX(id) FROM login_attempts WHERE {column}=? AND ok=1), 0)",
            (value, since, value)).fetchone()
        return int(row[0])

    def login(self, username: str, password: str, ip: str) -> tuple[str, Session]:
        with self.lock:
            if (self._recent_failures("username", username) >= self.max_attempts
                    or self._recent_failures("ip", ip) >= self.max_attempts):
                self.conn.execute("INSERT INTO login_attempts(ts, username, ip, ok) VALUES(?,?,?,0)",
                                  (_iso(self.clock()), username, ip))
                self.audit(username, ip, "login", "bloqueado por intentos", "locked")
                raise LoginError(f"Demasiados intentos. Espera {int(self.lockout.total_seconds() // 60)} "
                                 "minutos.", locked=True)
            ok = self._verify(username, password)
            self.conn.execute("INSERT INTO login_attempts(ts, username, ip, ok) VALUES(?,?,?,?)",
                              (_iso(self.clock()), username, ip, int(ok)))
            if not ok:
                self.audit(username, ip, "login", "", "fail")
                raise LoginError("Usuario o contraseña incorrectos")
            token = secrets.token_urlsafe(32)
            now = self.clock()
            sess = Session(_h(token), username, secrets.token_urlsafe(32), now, now, None, self.clock)
            self.conn.execute(
                "INSERT INTO sessions(token_hash, username, csrf, created_at, last_seen, ip) VALUES(?,?,?,?,?,?)",
                (sess.token_hash, username, sess.csrf, _iso(now), _iso(now), ip))
        self.audit(username, ip, "login", "", "ok")
        return token, sess

    def get_session(self, token: str | None, touch: bool = True) -> Session | None:
        if not token:
            return None
        th = _h(token)
        row = self.conn.execute(
            "SELECT token_hash, username, csrf, created_at, last_seen, control_until FROM sessions "
            "WHERE token_hash=?", (th,)).fetchone()
        if not row:
            return None
        sess = Session(row[0], row[1], row[2], _parse(row[3]), _parse(row[4]), _parse(row[5]), self.clock)
        now = self.clock()
        if now - sess.last_seen > self.idle or now - sess.created_at > self.max_age:
            self.logout(token)
            return None
        if touch:
            self.conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (_iso(now), th))
            sess.last_seen = now
        return sess

    def logout(self, token: str) -> None:
        self.conn.execute("DELETE FROM sessions WHERE token_hash=?", (_h(token),))

    # ------------------------------------------------------------ control
    def unlock_control(self, sess: Session, password: str, minutes: int, ip: str) -> datetime:
        if not self._verify(sess.username, password):
            self.audit(sess.username, ip, "control_unlock", "contraseña incorrecta", "fail")
            raise LoginError("Contraseña incorrecta")
        until = self.clock() + timedelta(minutes=minutes)
        self.conn.execute("UPDATE sessions SET control_until=? WHERE token_hash=?", (_iso(until), sess.token_hash))
        sess.control_until = until
        self.audit(sess.username, ip, "control_unlock", f"{minutes} min", "ok")
        return until

    def lock_control(self, sess: Session, ip: str) -> None:
        self.conn.execute("UPDATE sessions SET control_until=NULL WHERE token_hash=?", (sess.token_hash,))
        sess.control_until = None
        self.audit(sess.username, ip, "control_lock", "", "ok")

    # ------------------------------------------------------------ auditoría
    def audit(self, username: str, ip: str, action: str, detail: str, result: str) -> None:
        self.conn.execute("INSERT INTO audit(ts, username, ip, action, detail, result) VALUES(?,?,?,?,?,?)",
                          (_iso(self.clock()), username, ip, action, detail, result))

    def audit_log(self, limit: int = 200) -> list[dict]:
        cur = self.conn.execute("SELECT ts, username, ip, action, detail, result FROM audit "
                                "ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(zip(("ts", "username", "ip", "action", "detail", "result"), r)) for r in cur]
