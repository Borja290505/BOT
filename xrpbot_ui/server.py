"""Servidor de la interfaz (FastAPI).

Reglas de seguridad que se aplican aquí y no en el navegador:
- Todas las páginas y la API exigen sesión.
- Todo POST exige el token CSRF de la sesión (cabecera X-CSRF-Token) y, si el
  navegador envía Origin, que coincida con el host.
- Los comandos exigen: interfaz NO en solo lectura, modo control desbloqueado
  (contraseña reintroducida hace menos de N minutos), comando de la lista
  cerrada, palabra de confirmación correcta y bot vivo. Después decide el bot.
- Cada acción de control queda en la auditoría, se acepte o no.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .backtest_jobs import BacktestParamError, BacktestRunner, parse_params
from .contract import (CONFIRM_WORDS, REDUCIBLE_PARAMS, Command, CommandError, check_confirm_word,
                       derive_state, validate_command_shape, validate_reduction)
from .glossary import GLOSSARY
from .push import PushError, PushService
from .security import LoginError, SecurityStore, Session
from .settings import UISettings

HERE = Path(__file__).parent
COOKIE = "xrpbot_session"
SSE_INTERVAL_S = 2.0
PUSH_POLL_S = 10.0
log = logging.getLogger("xrpbot_ui")

PAGES = [
    ("resumen", "/", "Resumen", "◉"),
    ("mercado", "/mercado", "Mercado", "▤"),
    ("posicion", "/posicion", "Posición y órdenes", "◧"),
    ("riesgo", "/riesgo", "Riesgo", "◔"),
    ("historial", "/historial", "Historial", "☰"),
    ("backtest", "/backtest", "Backtest", "↺"),
    ("configuracion", "/configuracion", "Configuración", "⚙"),
    ("salud", "/salud", "Salud del sistema", "♥"),
    ("registro", "/registro", "Registro y alertas", "≡"),
]

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
       "object-src 'none'; manifest-src 'self'; worker-src 'self'")
KIND_TITLE = {"entry": "Entrada", "trade_closed": "Operación cerrada"}
LEVEL_TITLE = {"warning": "Aviso", "error": "Error", "critical": "CRÍTICO"}


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


async def push_bot_events(provider, push: PushService, settings: UISettings, once: bool = False) -> int:
    """Reenvía como push los eventos importantes que el bot escribe en su base de datos.

    Solo lee (la base del bot se abre en modo lectura) y recuerda el último id avisado
    en ui-state/push.sqlite, así un reinicio de la interfaz no repite avisos.
    """
    key = f"last_event_id:{settings.mode}"
    sent = 0
    while True:
        try:
            if push.count():
                raw = push.get(key)
                top, events = await asyncio.to_thread(provider.events_after, int(raw) if raw else None,
                                                      settings.push_min_level, settings.push_trades)
                for ev in events:
                    title = f"[{settings.mode.upper()}] " + (KIND_TITLE.get(ev["kind"]) or LEVEL_TITLE.get(ev["level"], "Evento"))
                    sent += await asyncio.to_thread(push.send_all, title, ev["message"] or "", ev["level"],
                                                    "/registro", ev["kind"])
                push.set(key, str(top))
        except Exception as exc:  # noqa: BLE001 - un fallo de push nunca debe tumbar la interfaz
            log.warning("Push de eventos del bot falló: %r", exc)
        if once:
            return sent
        await asyncio.sleep(PUSH_POLL_S)


def create_app(settings: UISettings, provider, store: SecurityStore,
               runner: BacktestRunner | None = None, push: PushService | None = None) -> FastAPI:
    runner = runner or BacktestRunner()
    push = push or PushService(Path(settings.ui_db_path).parent, settings.push_contact)

    @asynccontextmanager
    async def lifespan(_app):
        task = None
        if hasattr(provider, "events_after"):
            task = asyncio.create_task(push_bot_events(provider, push, settings))
        yield
        if task:
            task.cancel()
        runner.shutdown()

    app = FastAPI(title="xrpbot · interfaz", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.state.runner = runner
    app.state.push = push
    static = HERE / "static"

    # ------------------------------------------------------------ middleware
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host", ""):
                return JSONResponse({"error": "Origen no permitido"}, status_code=403)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        # same-origin (no "no-referrer"): con no-referrer Chrome manda "Origin: null" en los formularios
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api") or request.url.path in ("/login", "/healthz"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------ sesión
    def session_or_none(request: Request) -> Session | None:
        return store.get_session(request.cookies.get(COOKIE))

    def require_session(request: Request) -> Session:
        sess = session_or_none(request)
        if not sess:
            raise HTTPException(401, "Sesión caducada o no iniciada")
        return sess

    def require_csrf(request: Request, sess: Session) -> None:
        if request.headers.get("x-csrf-token") != sess.csrf:
            store.audit(sess.username, client_ip(request), "csrf", request.url.path, "rejected")
            raise HTTPException(403, "Token CSRF no válido")

    def ui_state(sess: Session) -> dict:
        return {"read_only": settings.read_only, "control_seconds_left": sess.control_seconds_left(),
                "control_minutes": settings.control_unlock_minutes, "user": sess.username,
                "source": provider.name, "display_timezone": settings.display_timezone}

    def full_state(sess: Session) -> dict:
        snap = provider.snapshot()
        caps = provider.capabilities()
        snap["bot"]["effective_state"] = derive_state(snap["bot"]) if caps.get("live_status") else "unknown"
        return {"snapshot": snap, "capabilities": caps, "ui": ui_state(sess)}

    # ------------------------------------------------------------ PWA (públicos: no contienen datos del bot)
    @app.get("/healthz")
    async def healthz():
        """Healthcheck de Docker/systemd: solo dice que la interfaz responde."""
        return {"ok": True}

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(static / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    async def service_worker():
        # en la raíz para que su ámbito sea toda la aplicación; sin caché para que se actualice
        return FileResponse(static / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    async def apple_icon():
        return FileResponse(static / "icons" / "apple-touch-icon.png", media_type="image/png")

    @app.get("/favicon.ico")
    async def favicon():
        return FileResponse(static / "icons" / "favicon-32.png", media_type="image/png")

    # ------------------------------------------------------------ login
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None, "no_users": not store.has_users(),
                                                                   "version": __version__})

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        try:
            token, _ = store.login(str(form.get("username", "")), str(form.get("password", "")), client_ip(request))
        except LoginError as exc:
            return templates.TemplateResponse(request, "login.html",
                                              {"error": str(exc), "no_users": not store.has_users(),
                                               "version": __version__}, status_code=429 if exc.locked else 401)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=settings.secure_cookies,
                        max_age=settings.session_max_hours * 3600, path="/")
        return resp

    @app.post("/logout")
    async def logout(request: Request):
        sess = require_session(request)
        require_csrf(request, sess)
        store.logout(request.cookies.get(COOKIE))
        store.audit(sess.username, client_ip(request), "logout", "", "ok")
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ------------------------------------------------------------ páginas
    def page(name: str):
        async def handler(request: Request, trade_id: str | None = None):
            sess = session_or_none(request)
            if not sess:
                return RedirectResponse("/login", status_code=303)
            caps = provider.capabilities()
            state = full_state(sess)
            snap = state["snapshot"]
            ctx = {"mode": snap["mode"], "pilot": snap["pilot"], "page": name, "pages": PAGES, "csrf": sess.csrf, "user": sess.username,
                   "read_only": settings.read_only, "control_minutes": settings.control_unlock_minutes,
                   "caps": caps, "glossary": GLOSSARY, "version": __version__, "trade_id": trade_id,
                   "confirm_words": CONFIRM_WORDS, "reducible": REDUCIBLE_PARAMS,
                   # "</" escapado: un mensaje del bot nunca puede cerrar el <script> que lo contiene
                   "initial_state": json.dumps(state, default=str).replace("</", "<\\/")}
            return templates.TemplateResponse(request, f"{name}.html", ctx)
        return handler

    for name, path, _, _ in PAGES:
        app.add_api_route(path, page(name), methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/historial/{trade_id}", page("operacion"), methods=["GET"], response_class=HTMLResponse)

    # ------------------------------------------------------------ API de lectura
    @app.get("/api/state")
    async def api_state(request: Request):
        sess = require_session(request)
        return await asyncio.to_thread(full_state, sess)

    @app.get("/api/stream")
    async def api_stream(request: Request):
        token = request.cookies.get(COOKIE)
        if not store.get_session(token):
            raise HTTPException(401, "Sesión caducada")

        async def gen():
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                sess = store.get_session(token, touch=False)
                if not sess:
                    yield "event: session\ndata: {\"expired\": true}\n\n"
                    break
                data = await asyncio.to_thread(full_state, sess)
                yield f"event: state\ndata: {json.dumps(data, default=str)}\n\n"
                await asyncio.sleep(SSE_INTERVAL_S)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/candles")
    async def api_candles(request: Request, limit: int = 500):
        require_session(request)
        return await asyncio.to_thread(provider.candles, max(50, min(limit, 2000)))

    @app.get("/api/equity")
    async def api_equity(request: Request):
        require_session(request)
        return await asyncio.to_thread(provider.equity)

    def _trade_filters(request: Request) -> dict:
        q = request.query_params
        return {k: (q.get(k) or None) for k in ("side", "result", "reason", "since", "until")}

    @app.get("/api/trades")
    async def api_trades(request: Request):
        require_session(request)
        return await asyncio.to_thread(provider.trades, **_trade_filters(request))

    @app.get("/api/trades.csv")
    async def api_trades_csv(request: Request):
        require_session(request)
        rows = await asyncio.to_thread(provider.trades, **_trade_filters(request))
        cols = ["trade_id", "side", "opened_at", "closed_at", "size", "entry_price", "exit_price", "stop_price",
                "tp_price", "gross_pnl", "fees", "funding", "net_pnl", "r_multiple", "exit_reason"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        fname = f"operaciones-{settings.mode}-{datetime.now(timezone.utc):%Y%m%d-%H%M}.csv"
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    @app.get("/api/trades/{trade_id}")
    async def api_trade(request: Request, trade_id: str):
        require_session(request)
        t = await asyncio.to_thread(provider.trade, trade_id)
        if not t:
            raise HTTPException(404, "Operación no encontrada")
        return t

    @app.get("/api/events")
    async def api_events(request: Request, level: str | None = None, kind: str | None = None, q: str | None = None):
        require_session(request)
        return await asyncio.to_thread(provider.events, level=level, kind=kind, q=q)

    @app.get("/api/alerts")
    async def api_alerts(request: Request):
        require_session(request)
        return await asyncio.to_thread(provider.alerts)

    @app.get("/api/audit")
    async def api_audit(request: Request):
        require_session(request)
        return store.audit_log()

    @app.get("/api/config")
    async def api_config(request: Request):
        require_session(request)
        return await asyncio.to_thread(provider.config_view)

    # ------------------------------------------------------------ control
    @app.post("/api/control/unlock")
    async def api_unlock(request: Request):
        sess = require_session(request)
        require_csrf(request, sess)
        if settings.read_only:
            store.audit(sess.username, client_ip(request), "control_unlock", "interfaz en solo lectura", "rejected")
            raise HTTPException(403, "La interfaz está en SOLO LECTURA (config/ui.yaml: read_only)")
        body = await request.json()
        try:
            until = store.unlock_control(sess, str(body.get("password", "")), settings.control_unlock_minutes,
                                         client_ip(request))
        except LoginError as exc:
            raise HTTPException(403, str(exc)) from None
        return {"control_until": until.isoformat(), "control_seconds_left": sess.control_seconds_left()}

    @app.post("/api/control/lock")
    async def api_lock(request: Request):
        sess = require_session(request)
        require_csrf(request, sess)
        store.lock_control(sess, client_ip(request))
        return {"ok": True}

    @app.post("/api/commands", status_code=202)
    async def api_command(request: Request):
        sess = require_session(request)
        require_csrf(request, sess)
        ip = client_ip(request)
        body = await request.json()
        ctype = str(body.get("type", ""))
        params = body.get("params") or {}
        detail = json.dumps({"type": ctype, "params": params})

        def reject(status: int, msg: str):
            store.audit(sess.username, ip, "command", detail, f"rejected: {msg}")
            raise HTTPException(status, msg)

        if settings.read_only:
            reject(403, "La interfaz está en SOLO LECTURA")
        if not sess.control_active():
            reject(403, "Modo control bloqueado: desbloquéalo con tu contraseña")
        caps = provider.capabilities()
        if not caps.get("control"):
            reject(409, caps.get("note") or "Control no disponible")
        try:
            validate_command_shape(ctype, params)
            check_confirm_word(ctype, body.get("confirm_word"))
        except CommandError as exc:
            reject(422, str(exc))
        state = await asyncio.to_thread(full_state, sess)
        eff_state = state["snapshot"]["bot"]["effective_state"]
        if eff_state == "down":
            reject(409, "El bot no responde (sin latido): el comando no llegaría. Usa la CLI en la máquina del bot.")
        if ctype == "reduce_param":
            cfg = await asyncio.to_thread(provider.config_view)
            cur = next(p for p in cfg["params"] if p["name"] == params["name"])
            try:
                validate_reduction(params["name"], float(params["value"]), float(cur["effective"]))
            except CommandError as exc:
                reject(422, str(exc))
        cmd_id = str(body.get("id") or uuid.uuid4())
        try:
            uuid.UUID(cmd_id)
        except ValueError:
            reject(422, "id de comando no válido")
        existing = provider.command(cmd_id)
        if existing:                          # idempotente: reenviar el mismo id no crea otro comando
            return existing.to_dict()
        cmd = Command(type=ctype, params=params, id=cmd_id, requested_by=sess.username, source_ip=ip)
        try:
            cmd = provider.submit(cmd)
        except CommandError as exc:
            reject(409, str(exc))
        store.audit(sess.username, ip, "command", detail + f" id={cmd.id}", "submitted")
        return cmd.to_dict()

    @app.get("/api/commands/{cmd_id}")
    async def api_command_status(request: Request, cmd_id: str):
        sess = require_session(request)
        cmd = await asyncio.to_thread(provider.command, cmd_id)
        if not cmd:
            raise HTTPException(404, "Comando no encontrado")
        if cmd.status != "pending" and not getattr(cmd, "_audited", False):
            store.audit(sess.username, client_ip(request), "command_result",
                        f"{cmd.type} id={cmd.id}", f"{cmd.status}: {cmd.result}")
            cmd._audited = True
        return cmd.to_dict()

    # ------------------------------------------------------------ notificaciones push
    @app.get("/api/push/key")
    async def api_push_key(request: Request):
        require_session(request)
        key = await asyncio.to_thread(push.public_key)
        return {"public_key": key, "subscriptions": push.count()}

    @app.post("/api/push/subscribe")
    async def api_push_subscribe(request: Request):
        sess = require_session(request)
        require_csrf(request, sess)
        sub = await request.json()
        try:
            push.subscribe(sub, request.headers.get("user-agent", ""))
        except PushError as exc:
            store.audit(sess.username, client_ip(request), "push_subscribe", str(exc), "rejected")
            raise HTTPException(422, str(exc)) from None
        store.audit(sess.username, client_ip(request), "push_subscribe",
                    urlparse(str(sub.get("endpoint", ""))).hostname or "", "ok")
        return {"ok": True, "subscriptions": push.count()}

    @app.post("/api/push/unsubscribe")
    async def api_push_unsubscribe(request: Request):
        sess = require_session(request)
        require_csrf(request, sess)
        push.unsubscribe(str((await request.json()).get("endpoint", "")))
        store.audit(sess.username, client_ip(request), "push_unsubscribe", "", "ok")
        return {"ok": True, "subscriptions": push.count()}

    @app.post("/api/push/test")
    async def api_push_test(request: Request):
        sess = require_session(request)
        require_csrf(request, sess)
        n = await asyncio.to_thread(push.send_all, "xrpbot · prueba",
                                    "Si ves esto, las notificaciones de este dispositivo funcionan.", "info", "/", "test")
        store.audit(sess.username, client_ip(request), "push_test", f"entregadas={n}", "ok")
        return {"delivered": n, "subscriptions": push.count()}

    # ------------------------------------------------------------ demo
    @app.post("/api/demo/scenario")
    async def api_scenario(request: Request):
        sess = require_session(request)
        require_csrf(request, sess)
        if provider.name != "demo":
            raise HTTPException(404, "Solo en modo demostración")
        name = str((await request.json()).get("name", ""))
        try:
            await asyncio.to_thread(provider.set_scenario, name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        store.audit(sess.username, client_ip(request), "demo_scenario", name, "ok")
        return {"ok": True}

    # ------------------------------------------------------------ backtest
    @app.post("/api/backtest", status_code=202)
    async def api_backtest(request: Request):
        sess = require_session(request)
        require_csrf(request, sess)
        body = await request.json()
        try:
            params = parse_params(body)
        except BacktestParamError as exc:
            raise HTTPException(422, str(exc)) from None
        candles, mark, funding, note = await asyncio.to_thread(provider.backtest_data)
        if candles is None:
            raise HTTPException(409, note)
        cfg = provider.config_view()
        eff = {p["name"].split(".")[1]: p["effective"] for p in cfg["params"] if p["name"].startswith("risk.")}
        capital = 10_000.0 if provider.name == "bot" else 1000.0
        try:
            job = runner.submit(params, candles, mark, funding, eff, capital, (0.0002, 0.0005), note)
        except BacktestParamError as exc:
            raise HTTPException(409, str(exc)) from None
        store.audit(sess.username, client_ip(request), "backtest", json.dumps(params), "submitted")
        return {"job_id": job}

    @app.get("/api/backtest/{job_id}")
    async def api_backtest_status(request: Request, job_id: str):
        require_session(request)
        job = runner.get(job_id)
        if not job:
            raise HTTPException(404, "Trabajo no encontrado")
        return job

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 401 and not request.url.path.startswith("/api"):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
