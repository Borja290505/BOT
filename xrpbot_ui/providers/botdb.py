"""Proveedor conectado al bot REAL, en SOLO LECTURA.

Usa únicamente lo que el bot ya guarda hoy en state/xrpbot-{modo}.sqlite
(tablas trades, events, kv y orders) y los Parquet de data/. La base de datos
se abre con el modo de solo lectura de SQLite: la interfaz no puede
modificarla aunque quisiera.

Lo que depende del contrato del Paso 4 (latido, estado en vivo, circuit
breakers, salud y comandos) aparece como «pendiente de integración» hasta que
el bot lo publique. No se inventan campos.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..contract import SCHEMA_VERSION, Command, CommandError, REDUCIBLE_PARAMS
from .base import filter_trades, level_at_least
from .demo import read_file_params

PENDING = "Pendiente de integración: el bot todavía no publica este dato (contrato del Paso 4)."


def _ts(v) -> str | None:
    if v is None:
        return None
    return datetime.fromtimestamp(float(v), tz=timezone.utc).isoformat()


class BotDBProvider:
    name = "bot"

    def __init__(self, state_db: Path, mode: str, settings_path: str | Path, data_dir: str | Path = "data",
                 symbol: str = "PF_XRPUSD") -> None:
        self.state_db = Path(state_db)
        self.mode = mode
        self.symbol = symbol
        self.data_dir = Path(data_dir)
        self.file_params, self.strategy = read_file_params(settings_path)

    # ------------------------------------------------------------ utilidades
    def _conn(self) -> sqlite3.Connection | None:
        if not self.state_db.exists():
            return None
        conn = sqlite3.connect(f"file:{self.state_db.as_posix()}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        return conn

    def _kv(self, conn, key: str) -> str | None:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------ API
    def capabilities(self) -> dict:
        return {"source": "bot", "control": False, "live_status": False, "scenarios": {}, "scenario": None,
                "backtest": (self.data_dir / f"{self.symbol}_trade_1h.parquet").exists(),
                "note": PENDING, "db_exists": self.state_db.exists(), "db_path": str(self.state_db)}

    def snapshot(self) -> dict:
        now = datetime.now(timezone.utc)
        snap = {
            "schema": SCHEMA_VERSION, "generated_at": now.isoformat(), "source": "bot", "symbol": self.symbol,
            "tick_size": 0.0001, "mode": self.mode,
            "pilot": {"active": self.strategy.get("max_capital_usd") is not None,
                      "max_capital_usd": self.strategy.get("max_capital_usd")},
            "bot": {"state": "unknown", "reason": None, "heartbeat_at": None, "version": None, "commit": None,
                    "started_at": None, "strategy": f"Donchian {self.strategy['entry_lookback']} + ATR · "
                                                    f"{self.strategy['timeframe']}"},
            "market": None,
            "account": {"equity_usd": None, "equity_at": None, "day_start_equity_usd": None, "pnl_day_usd": None,
                        "pnl_total_usd": None, "initial_capital_usd": None, "account_max_leverage": None},
            "position": None, "orders": [],
            "risk": {"daily_loss_used_usd": None, "daily_loss_limit_usd": None, "daily_loss_fraction_of_limit": None,
                     "reset_at": (now.replace(hour=0, minute=0, second=0, microsecond=0)
                                  + pd.Timedelta(days=1)).isoformat(),
                     "effective": {n.split(".")[1]: self.file_params[n]
                                   for n in ("risk.risk_per_trade", "risk.max_daily_loss", "risk.max_leverage")},
                     "entries_blocked": False, "block_reasons": []},
            "breakers": [], "health": None, "last_event": None, "pending_note": PENDING,
        }
        conn = self._conn()
        if conn is None:
            snap["pending_note"] = f"No existe {self.state_db}: arranca el bot en modo {self.mode} al menos una vez."
            return snap
        with conn:
            halted = self._kv(conn, "dl.halted") == "1"
            frozen = self._kv(conn, "bot.frozen")
            if halted:
                snap["bot"]["state"], snap["bot"]["reason"] = "halted", self._kv(conn, "dl.reason")
            elif frozen:
                snap["bot"]["state"], snap["bot"]["reason"] = "frozen", frozen
            start = self._kv(conn, "dl.start_equity")
            if start:
                start_f = float(start)
                snap["account"]["day_start_equity_usd"] = start_f
                snap["risk"]["daily_loss_limit_usd"] = start_f * self.file_params["risk.max_daily_loss"]
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            row = conn.execute("SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE status='closed'").fetchone()
            snap["account"]["pnl_total_usd"] = float(row[0])
            row = conn.execute("SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE status='closed' AND closed_ts>=?",
                               (midnight,)).fetchone()
            snap["account"]["pnl_day_realized_usd"] = float(row[0])
            tid = self._kv(conn, "trade.active_id")
            if tid:
                r = conn.execute("SELECT state_json FROM trades WHERE trade_id=?", (tid,)).fetchone()
                if r:
                    st = json.loads(r[0])
                    size = float(st.get("filled_size", 0)) - float(st.get("exited_size", 0))
                    snap["position"] = {
                        "trade_id": tid, "side": st["side"], "size": size, "entry_price": st.get("entry_avg"),
                        "mark_price": None, "notional_usd": None, "effective_leverage": None,
                        "unrealized_pnl_usd": None, "r_multiple": None, "risk_usd": st.get("risk_usd"),
                        "stop_price": st.get("stop_price"), "tp_price": st.get("tp_price"), "liquidation_price": None,
                        "dist_stop_pct": None, "dist_tp_pct": None, "dist_liq_pct": None,
                        "funding_accrued_usd": st.get("funding"), "opened_at": _ts(st.get("opened_ts")),
                        "status": st.get("status")}
                    for o in conn.execute("SELECT cli_ord_id, role, side, order_type, size, limit_price, stop_price, "
                                          "reduce_only, status FROM orders WHERE trade_id=? AND role IN ('sl','tp') "
                                          "ORDER BY ts DESC", (tid,)):
                        if o["status"] in ("placed", "sending"):
                            snap["orders"].append({
                                "role": o["role"], "type": o["order_type"], "side": o["side"], "size": o["size"],
                                "price": o["stop_price"] if o["role"] == "sl" else o["limit_price"],
                                "trigger": "mark" if o["role"] == "sl" else None, "reduce_only": bool(o["reduce_only"]),
                                "cli_ord_id": o["cli_ord_id"], "status": "último estado registrado: " + o["status"]})
            ev = conn.execute("SELECT ts, level, kind, message FROM events WHERE level IN ('warning','error','critical') "
                              "OR kind IN ('entry','trade_closed') ORDER BY id DESC LIMIT 1").fetchone()
            if ev:
                snap["last_event"] = {"ts": _ts(ev["ts"]), "level": ev["level"], "kind": ev["kind"], "message": ev["message"]}
        return snap

    def candles(self, limit: int = 500) -> dict:
        from xrpbot.strategy.donchian_atr import StrategyParams, compute_indicators
        path = self.data_dir / f"{self.symbol}_trade_1h.parquet"
        out = {"symbol": self.symbol, "timeframe": "1h", "tick_size": 0.0001, "candles": [], "indicators": [],
               "markers": [], "levels": [], "note": None}
        if not path.exists():
            out["note"] = "Sin datos locales: ejecuta «python -m xrpbot.cli download»."
            return out
        df = pd.read_parquet(path).sort_index()
        p = StrategyParams(entry_lookback=int(self.strategy["entry_lookback"]),
                           exit_lookback=int(self.strategy["exit_lookback"]),
                           atr_period=int(self.strategy["atr_period"]))
        ind = compute_indicators(df, p).iloc[-limit:]
        df = df.iloc[-limit:]
        out["candles"] = [{"time": int(ts.timestamp()), "open": r.open, "high": r.high, "low": r.low,
                           "close": r.close} for ts, r in df.iterrows()]

        def series(col):
            return [{"time": int(ts.timestamp()), "value": round(float(v), 5)} for ts, v in ind[col].items() if np.isfinite(v)]
        out["indicators"] = [
            {"id": "dc_upper", "name": f"Donchian {p.entry_lookback} sup.", "pane": "price", "style": "dashed", "color": "ind-1", "points": series("dc_upper")},
            {"id": "dc_lower", "name": f"Donchian {p.entry_lookback} inf.", "pane": "price", "style": "dashed", "color": "ind-1", "points": series("dc_lower")},
            {"id": "exit_low", "name": f"Canal de salida {p.exit_lookback}", "pane": "price", "style": "dotted", "color": "ind-1", "points": series("exit_low")},
            {"id": "atr", "name": f"ATR {p.atr_period}", "pane": "separate", "style": "solid", "color": "line", "points": series("atr")},
        ]
        start = df.index[0].timestamp()
        for t in self.trades():
            for key, kind, txt in (("opened_at", "entry", "Entrada"), ("closed_at", "exit", "Salida")):
                if t.get(key):
                    ts = pd.Timestamp(t[key]).floor("1h").timestamp()
                    if ts >= start:
                        out["markers"].append({"time": int(ts), "kind": kind, "side": t["side"], "text": txt})
        out["markers"].sort(key=lambda m: m["time"])
        snap_pos = self.snapshot().get("position")
        if snap_pos:
            out["levels"] = [{"id": k, "label": lab, "price": snap_pos[f]} for k, lab, f in
                             (("entry", "ENTRADA", "entry_price"), ("tp", "TP", "tp_price"), ("stop", "STOP", "stop_price"))
                             if snap_pos.get(f)]
        out["note"] = "Velas del último «download» (no en vivo hasta completar la integración)."
        return out

    def equity(self) -> dict:
        rows = self.trades()
        rows = sorted(rows, key=lambda t: t["closed_at"] or "")
        cum, pts = 0.0, []
        for t in rows:
            cum += t["net_pnl"] or 0.0
            pts.append({"time": int(pd.Timestamp(t["closed_at"]).timestamp()), "value": round(cum, 4)})
        return {"points": pts, "drawdown": [], "initial_capital_usd": None,
                "note": "P&L realizado acumulado (la equity de la cuenta llegará con la integración)."}

    def trades(self, **filters) -> list[dict]:
        conn = self._conn()
        if conn is None:
            return []
        with conn:
            rows = []
            for r in conn.execute("SELECT * FROM trades WHERE status='closed' ORDER BY opened_ts DESC"):
                gross = r["realized_pnl"] or 0.0
                risk = r["risk_usd"] or 0.0
                rows.append({
                    "trade_id": r["trade_id"], "side": r["side"], "opened_at": _ts(r["opened_ts"]),
                    "closed_at": _ts(r["closed_ts"]), "size": r["size"], "entry_price": r["entry_avg"],
                    "exit_price": r["exit_avg"], "stop_price": r["stop_price"], "tp_price": r["tp_price"],
                    "gross_pnl": gross, "fees": r["fees"] or 0.0, "funding": r["funding"] or 0.0,
                    "net_pnl": r["net_pnl"] or 0.0, "r_multiple": (r["net_pnl"] or 0.0) / risk if risk else None,
                    "risk_usd": risk, "exit_reason": r["exit_reason"] or "", "bars_held": None, "signal": ""})
        return filter_trades(rows, **filters)

    def trade(self, trade_id: str) -> dict | None:
        t = next((t for t in self.trades() if t["trade_id"] == trade_id), None)
        if not t:
            return None
        conn = self._conn()
        with conn:
            t["timeline"] = [{"ts": _ts(o["ts"]), "what": f"Orden {o['role']} {o['order_type']}",
                              "detail": f"{o['side']} {o['size']} → {o['status']}"}
                             for o in conn.execute("SELECT * FROM orders WHERE trade_id=? ORDER BY ts", (trade_id,))]
            t["timeline"] += [{"ts": _ts(f["ts"]), "what": "Ejecución", "detail": f"{f['side']} {f['size']} @ {f['price']}"}
                              for f in conn.execute("SELECT * FROM fills WHERE trade_id=? ORDER BY ts", (trade_id,))]
            t["timeline"].sort(key=lambda x: x["ts"] or "")
        return t

    def events(self, *, level=None, kind=None, q=None, limit: int = 300) -> list[dict]:
        conn = self._conn()
        if conn is None:
            return []
        with conn:
            out = []
            for r in conn.execute("SELECT ts, level, kind, message FROM events ORDER BY id DESC LIMIT 5000"):
                if level_at_least(r["level"], level) and (not kind or r["kind"] == kind) and \
                        (not q or q.lower() in (r["message"] or "").lower()):
                    out.append({"ts": _ts(r["ts"]), "level": r["level"], "kind": r["kind"], "message": r["message"]})
                    if len(out) >= limit:
                        break
            return out

    def alerts(self, limit: int = 200) -> list[dict]:
        # El bot no registra hoy qué alertas llegaron a Telegram: se muestran los eventos que las disparan.
        return [dict(e, channel="telegram", delivered=None) for e in self.events(level="warning", limit=limit)]

    def config_view(self) -> dict:
        params = [{"name": n, "label": r["label"], "unit": r["unit"], "dir": r["dir"], "file": self.file_params[n],
                   "override": None, "effective": self.file_params[n]} for n, r in REDUCIBLE_PARAMS.items()]
        return {"params": params, "strategy": self.strategy, "mode": self.mode, "symbol": self.symbol}

    def submit(self, cmd: Command) -> Command:
        raise CommandError("Control no disponible: el bot todavía no acepta comandos de la interfaz "
                           "(contrato del Paso 4 pendiente de confirmar). Usa la CLI: python -m xrpbot.cli kill/rearm.")

    def command(self, cmd_id: str) -> Command | None:
        return None

    def backtest_data(self):
        path = self.data_dir / f"{self.symbol}_trade_1h.parquet"
        if not path.exists():
            return None, None, None, "Sin datos: ejecuta «python -m xrpbot.cli download»."
        candles = pd.read_parquet(path).sort_index()
        mark = funding = None
        mp = self.data_dir / f"{self.symbol}_mark_1h.parquet"
        fp = self.data_dir / f"{self.symbol}_funding.parquet"
        if mp.exists():
            mark = pd.read_parquet(mp).sort_index()
        if fp.exists():
            funding = pd.read_parquet(fp).sort_index()
        return candles, mark, funding, "Datos reales descargados de Kraken"
