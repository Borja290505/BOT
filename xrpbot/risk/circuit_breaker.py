"""Circuit breaker: condiciones de mercado en las que NO se abren posiciones.

No cierra posiciones abiertas (ya están protegidas por su stop en el
exchange). Solo bloquea entradas nuevas y devuelve los motivos para el log.

Son condiciones de mercado, no franjas horarias: en cripto los momentos de
libro fino (fines de semana, madrugada, noticias) se detectan por el spread, la
profundidad y la volatilidad, no por el reloj.
"""
from __future__ import annotations

from ..config import CircuitBreakerCfg
from ..models import MarketSnapshot


def entry_block_reasons(*, snapshot: MarketSnapshot | None, now_s: float, cfg: CircuitBreakerCfg,
                        atr_ratio: float | None, candles_stale: bool,
                        intended_size: float | None = None) -> list[str]:
    reasons: list[str] = []
    if candles_stale:
        reasons.append("velas desfasadas")
    if snapshot is None:
        reasons.append("sin datos de ticker")
        return reasons
    age = now_s - snapshot.ts
    if age > cfg.max_data_age_s:
        reasons.append(f"ticker desfasado {age:.0f}s > {cfg.max_data_age_s}s")
    if snapshot.bid <= 0 or snapshot.ask <= 0 or snapshot.ask < snapshot.bid:
        reasons.append(f"libro incoherente bid={snapshot.bid} ask={snapshot.ask}")
    elif snapshot.spread_bps > cfg.max_spread_bps:
        reasons.append(f"spread {snapshot.spread_bps:.1f}bps > {cfg.max_spread_bps}bps")
    if snapshot.mark > 0 and snapshot.last > 0:
        div = abs(snapshot.mark - snapshot.last) / snapshot.mark * 1e4
        if div > cfg.max_mark_last_divergence_bps:
            reasons.append(f"divergencia mark/last {div:.1f}bps > {cfg.max_mark_last_divergence_bps}bps")
    if atr_ratio is not None and atr_ratio > cfg.max_atr_ratio:
        reasons.append(f"volatilidad anómala: ATR {atr_ratio:.1f}x su mediana")
    if intended_size is not None and snapshot.bid_depth is not None and snapshot.ask_depth is not None:
        depth = min(snapshot.bid_depth, snapshot.ask_depth)
        if depth < cfg.min_book_depth_multiple * intended_size:
            reasons.append(f"libro fino: profundidad {depth:.0f} < {cfg.min_book_depth_multiple}x "
                           f"tamaño {intended_size:.0f}")
    return reasons


def book_depth_within(book: dict, mid: float, pct: float = 0.005) -> tuple[float, float]:
    """Suma de tamaño en el libro dentro de +-pct del mid. book = {"bids": [[p, q]], "asks": [[p, q]]}."""
    bids = sum(float(q) for p, q in book.get("bids", []) if float(p) >= mid * (1 - pct))
    asks = sum(float(q) for p, q in book.get("asks", []) if float(p) <= mid * (1 + pct))
    return bids, asks
