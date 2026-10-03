"""Persistencia en SQLite: estado, señales, órdenes, ejecuciones, operaciones y eventos.

- Modo WAL: la CLI (kill switch, rearme, exportar) puede leer y escribir
  mientras el bot está en marcha.
- La tabla kv guarda el estado que debe sobrevivir a reinicios: parada diaria,
  congelación, operación activa.
"""
from __future__ import annotations

import csv
import json
import sqlite3
import time
from pathlib import Path

from ..execution.oco import TradeState
from ..models import Fill, OrderRequest, Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, bar_time TEXT, side TEXT,
    ref_price REAL, stop_price REAL, tp_price REAL, atr REAL, reason TEXT, decision TEXT);
CREATE TABLE IF NOT EXISTS orders (
    cli_ord_id TEXT PRIMARY KEY, order_id TEXT, ts REAL, trade_id TEXT, role TEXT, side TEXT,
    order_type TEXT, size REAL, limit_price REAL, stop_price REAL, reduce_only INTEGER,
    status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY, order_id TEXT, cli_ord_id TEXT, trade_id TEXT, ts REAL,
    side TEXT, size REAL, price REAL, fill_type TEXT, fee REAL);
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY, side TEXT, status TEXT, opened_ts REAL, closed_ts REAL,
    size REAL, entry_avg REAL, exit_avg REAL, stop_price REAL, tp_price REAL, risk_usd REAL,
    realized_pnl REAL, fees REAL, funding REAL, net_pnl REAL, exit_reason TEXT, state_json TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, kind TEXT, message TEXT);
"""

K_ACTIVE_TRADE = "trade.active_id"
K_FROZEN = "bot.frozen"
K_LAST_FILL_TIME = "fills.last_time"


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --------------------------------------------------------------- kv
    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str | None) -> None:
        if value is None:
            self.conn.execute("DELETE FROM kv WHERE key=?", (key,))
        else:
            self.conn.execute("INSERT INTO kv(key, value) VALUES(?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ---------------------------------------------------------- registros
    def log_event(self, level: str, kind: str, message: str) -> None:
        self.conn.execute("INSERT INTO events(ts, level, kind, message) VALUES(?,?,?,?)",
                          (time.time(), level, kind, message))

    def log_signal(self, sig: Signal, decision: str) -> None:
        self.conn.execute(
            "INSERT INTO signals(ts, bar_time, side, ref_price, stop_price, tp_price, atr, reason, decision) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (time.time(), str(sig.time), sig.side.value, sig.ref_price, sig.stop_price,
             sig.take_profit_price, sig.atr, sig.reason, decision))

    def log_order(self, req: OrderRequest, trade_id: str | None, status: str,
                  order_id: str | None = None, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO orders(cli_ord_id, order_id, ts, trade_id, role, side, order_type, size, "
            "limit_price, stop_price, reduce_only, status, detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(cli_ord_id) DO UPDATE SET order_id=COALESCE(excluded.order_id, order_id), "
            "status=excluded.status, detail=excluded.detail, size=excluded.size, "
            "stop_price=excluded.stop_price",
            (req.cli_ord_id, order_id, time.time(), trade_id, req.role, req.side, req.order_type,
             req.size, req.limit_price, req.stop_price, int(req.reduce_only), status, detail))

    def update_order_status(self, cli_ord_id: str, status: str, detail: str = "") -> None:
        self.conn.execute("UPDATE orders SET status=?, detail=? WHERE cli_ord_id=?",
                          (status, detail, cli_ord_id))

    def order_known(self, cli_ord_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM orders WHERE cli_ord_id=?", (cli_ord_id,)).fetchone() is not None

    def log_fill(self, fill: Fill, trade_id: str | None, fee: float) -> bool:
        """Devuelve False si la ejecución ya estaba registrada (idempotente)."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO fills(fill_id, order_id, cli_ord_id, trade_id, ts, side, size, price, "
            "fill_type, fee) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (fill.fill_id, fill.order_id, fill.cli_ord_id, trade_id, fill.time, fill.side, fill.size,
             fill.price, fill.fill_type, fee))
        return cur.rowcount == 1

    # ------------------------------------------------------- operaciones
    def save_trade(self, st: TradeState) -> None:
        funding = st.funding
        net = st.realized_pnl - st.fees + funding
        self.conn.execute(
            "INSERT INTO trades(trade_id, side, status, opened_ts, closed_ts, size, entry_avg, exit_avg, "
            "stop_price, tp_price, risk_usd, realized_pnl, fees, funding, net_pnl, exit_reason, state_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(trade_id) DO UPDATE SET "
            "status=excluded.status, opened_ts=excluded.opened_ts, closed_ts=excluded.closed_ts, "
            "size=excluded.size, entry_avg=excluded.entry_avg, exit_avg=excluded.exit_avg, "
            "stop_price=excluded.stop_price, tp_price=excluded.tp_price, realized_pnl=excluded.realized_pnl, "
            "fees=excluded.fees, funding=excluded.funding, net_pnl=excluded.net_pnl, "
            "exit_reason=excluded.exit_reason, state_json=excluded.state_json",
            (st.trade_id, st.side.value, st.status, st.opened_ts, st.closed_ts, st.filled_size,
             st.entry_avg, st.exit_avg, st.stop_price, st.tp_price, st.risk_usd, st.realized_pnl,
             st.fees, funding, net, st.exit_reason, json.dumps(st.to_dict())))

    def load_trade(self, trade_id: str) -> TradeState | None:
        row = self.conn.execute("SELECT state_json FROM trades WHERE trade_id=?", (trade_id,)).fetchone()
        return TradeState.from_dict(json.loads(row[0])) if row else None

    def active_trade(self) -> TradeState | None:
        tid = self.get(K_ACTIVE_TRADE)
        return self.load_trade(tid) if tid else None

    def set_active_trade(self, st: TradeState | None) -> None:
        if st is None:
            self.set(K_ACTIVE_TRADE, None)
        else:
            self.save_trade(st)
            self.set(K_ACTIVE_TRADE, st.trade_id)

    def realized_pnl_since(self, ts: float) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(net_pnl), 0) FROM trades WHERE closed_ts >= ?",
                                (ts,)).fetchone()
        return float(row[0])

    # ------------------------------------------------------------ export
    def export_csv(self, out_dir: str | Path) -> list[Path]:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        written = []
        for table, order in (("trades", "opened_ts"), ("fills", "ts"), ("orders", "ts"),
                             ("signals", "ts"), ("events", "ts")):
            cur = self.conn.execute(f"SELECT * FROM {table} ORDER BY {order}")  # noqa: S608 (nombres fijos)
            cols = [d[0] for d in cur.description]
            path = out / f"{table}.csv"
            with path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow([c for c in cols if c != "state_json"])
                idx = [i for i, c in enumerate(cols) if c != "state_json"]
                for row in cur:
                    w.writerow([row[i] for i in idx])
            written.append(path)
        return written
