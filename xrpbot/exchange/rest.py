"""Cliente REST asíncrono de Kraken Futures (API v3) + API de charts.

Decisiones:
- Cliente propio con aiohttp: control total de cliOrdId, reduceOnly,
  triggerSignal, reintentos e idempotencia. Sin capas intermedias.
- GET (idempotentes): reintento automático con backoff exponencial + jitter.
- POST de órdenes: NO se reintentan aquí. Un timeout tras enviar es un
  resultado AMBIGUO (la orden pudo llegar). Se lanza AmbiguousOrderResult y el
  OrderManager decide consultando por cliOrdId antes de reenviar.
- Se mide el desfase con la hora del servidor (campo serverTime) y se avisa si
  supera 1 s: los controles de datos desfasados usan la hora del servidor.

Los nombres de campos siguen la documentación de Kraken Futures v3. Los que no
se han podido verificar desde el entorno de desarrollo están marcados con
"VERIFICAR" y se parsean de forma defensiva.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode, urlparse

import aiohttp

from ..models import Fill, OpenOrder, OrderAck, OrderRequest, Position, Side
from .auth import NonceGenerator, sign_rest
from .rate_limiter import CostRateLimiter, cost_of

log = logging.getLogger(__name__)

RETRYABLE_ERRORS = {"apiLimitExceeded", "Server Error", "nonceBelowThreshold", "nonceDuplicate"}


class KrakenAPIError(Exception):
    def __init__(self, endpoint: str, error: str, payload: Any = None):
        super().__init__(f"{endpoint}: {error}")
        self.endpoint, self.error, self.payload = endpoint, error, payload


class AmbiguousOrderResult(Exception):
    """El envío de una orden falló sin saber si llegó al exchange."""


def _iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _f(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt(x: float) -> str:
    """Número sin notación científica ni ceros sobrantes para enviar a la API."""
    s = format(Decimal(str(x)), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


class KrakenFuturesRest:
    def __init__(self, base_url: str, charts_url: str, api_key: str | None = None,
                 api_secret: str | None = None, timeout_s: float = 10.0, max_retries: int = 4,
                 limiter: CostRateLimiter | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.charts_url = charts_url.rstrip("/")
        self._base_path = urlparse(self.base_url).path      # "/derivatives/api/v3"
        self._key, self._secret = api_key, api_secret
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._max_retries = max_retries
        self._limiter = limiter or CostRateLimiter()
        self._nonce = NonceGenerator()
        self._session: aiohttp.ClientSession | None = None
        self.clock_offset_s: float = 0.0   # hora_servidor - hora_local

    # ------------------------------------------------------------------ infra
    async def __aenter__(self) -> "KrakenFuturesRest":
        await self.open()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def open(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def server_time(self) -> float:
        return time.time() + self.clock_offset_s

    @property
    def has_credentials(self) -> bool:
        return bool(self._key and self._secret)

    def _update_clock(self, payload: dict) -> None:
        ts = _iso_to_epoch(payload.get("serverTime")) if isinstance(payload, dict) else None
        if ts is None:
            return
        offset = ts - time.time()
        # suavizado para no saltar por la latencia de una sola petición
        self.clock_offset_s = offset if self.clock_offset_s == 0 else 0.8 * self.clock_offset_s + 0.2 * offset
        if abs(self.clock_offset_s) > 1.0:
            log.warning("Desfase de reloj con Kraken de %.2f s: sincroniza NTP en la máquina",
                        self.clock_offset_s)

    async def _request(self, method: str, endpoint: str, params: dict | None = None,
                       private: bool = False, idempotent: bool = True) -> dict:
        assert self._session is not None, "llama a open() antes"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        query = urlencode(params)
        url = f"{self.base_url}/{endpoint}"
        attempts = self._max_retries if idempotent else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            await self._limiter.acquire(cost_of(endpoint))
            headers = {"Accept": "application/json"}
            if private:
                if not self.has_credentials:
                    raise KrakenAPIError(endpoint, "faltan credenciales")
                nonce = self._nonce.next()
                headers.update({
                    "APIKey": self._key,
                    "Nonce": nonce,
                    "Authent": sign_rest(self._secret, f"{self._base_path}/{endpoint}", query, nonce),
                })
            try:
                if method == "GET":
                    req = self._session.get(url + (f"?{query}" if query else ""), headers=headers)
                else:
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                    req = self._session.request(method, url, data=query, headers=headers)
                async with req as resp:
                    if resp.status >= 500 or resp.status == 429:
                        raise KrakenAPIError(endpoint, f"HTTP {resp.status}")
                    payload = await resp.json(content_type=None)
                self._update_clock(payload)
                if payload.get("result") == "error":
                    raise KrakenAPIError(endpoint, str(payload.get("error", "unknown")), payload)
                return payload
            except (aiohttp.ClientError, asyncio.TimeoutError, KrakenAPIError) as exc:
                last_exc = exc
                retryable = not isinstance(exc, KrakenAPIError) or exc.error in RETRYABLE_ERRORS \
                    or exc.error.startswith("HTTP")
                if not idempotent:
                    if isinstance(exc, KrakenAPIError) and not exc.error.startswith("HTTP"):
                        raise  # rechazo explícito: no hay ambigüedad
                    raise AmbiguousOrderResult(f"{endpoint}: {exc!r}") from exc
                if not retryable or attempt == attempts - 1:
                    raise
                delay = min(30.0, 2 ** attempt) + random.uniform(0, 0.5)
                log.warning("Error en %s (%r), reintento %d en %.1fs", endpoint, exc, attempt + 1, delay)
                await asyncio.sleep(delay)
        raise last_exc  # pragma: no cover

    # ----------------------------------------------------------- públicos
    async def get_instruments(self) -> list[dict]:
        return (await self._request("GET", "instruments")).get("instruments", [])

    async def get_tickers(self) -> list[dict]:
        return (await self._request("GET", "tickers")).get("tickers", [])

    async def get_ticker(self, symbol: str) -> dict | None:
        for t in await self.get_tickers():
            if str(t.get("symbol", "")).upper() == symbol.upper():
                return t
        return None

    async def get_orderbook(self, symbol: str) -> dict:
        return (await self._request("GET", "orderbook", {"symbol": symbol})).get("orderBook", {})

    async def get_fee_schedules(self) -> list[dict]:
        return (await self._request("GET", "feeschedules")).get("feeSchedules", [])

    async def get_historical_funding(self, symbol: str) -> list[dict]:
        return (await self._request("GET", "historicalfundingrates", {"symbol": symbol})).get("rates", [])

    async def get_candles(self, tick_type: str, symbol: str, resolution: str,
                          start_s: int | None = None, end_s: int | None = None) -> dict:
        """API de charts: /api/charts/v1/{tick_type}/{symbol}/{resolution}?from=&to= (segundos).

        Devuelve {"candles": [{time(ms), open, high, low, close, volume}], "more_candles": bool}.
        El máximo de velas por petición no está garantizado: el descargador pagina.
        """
        assert self._session is not None
        params = {k: v for k, v in {"from": start_s, "to": end_s}.items() if v is not None}
        url = f"{self.charts_url}/{tick_type}/{symbol}/{resolution}"
        for attempt in range(self._max_retries):
            try:
                await self._limiter.acquire(1)
                async with self._session.get(url, params=params) as resp:
                    if resp.status >= 500 or resp.status == 429:
                        raise aiohttp.ClientError(f"HTTP {resp.status}")
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt == self._max_retries - 1:
                    raise
                delay = min(30.0, 2 ** attempt) + random.uniform(0, 0.5)
                log.warning("Error en charts (%r), reintento en %.1fs", exc, delay)
                await asyncio.sleep(delay)
        return {}

    # ----------------------------------------------------------- privados
    async def get_accounts(self) -> dict:
        return (await self._request("GET", "accounts", private=True)).get("accounts", {})

    async def get_equity_usd(self) -> float:
        """Equity de la cuenta multicolateral (flex), incluye P&L no realizado.

        VERIFICAR en demo: se usa portfolioValue; si no existe, marginEquity / balanceValue.
        """
        flex = (await self.get_accounts()).get("flex", {})
        for key in ("portfolioValue", "marginEquity", "balanceValue"):
            v = _f(flex.get(key))
            if v is not None:
                return v
        raise KrakenAPIError("accounts", "no se encontró la equity de la cuenta flex", flex)

    async def get_fee_volumes(self) -> dict[str, float]:
        payload = await self._request("GET", "feeschedules/volumes", private=True)
        return {k: float(v) for k, v in payload.get("volumesByFeeSchedule", {}).items()}

    async def get_open_positions(self) -> list[Position]:
        payload = await self._request("GET", "openpositions", private=True)
        out = []
        for p in payload.get("openPositions", []):
            size = abs(_f(p.get("size"), 0.0))
            if size == 0:
                continue
            out.append(Position(
                symbol=p["symbol"].upper(),
                side=Side.LONG if p.get("side") == "long" else Side.SHORT,
                size=size,
                entry_price=_f(p.get("price"), 0.0),
                unrealized_funding=_f(p.get("unrealizedFunding"), 0.0),
            ))
        return out

    async def get_open_orders(self) -> list[OpenOrder]:
        payload = await self._request("GET", "openorders", private=True)
        out = []
        for o in payload.get("openOrders", []):
            otype = o.get("orderType", "")
            otype = "stp" if otype == "stop" else otype
            out.append(OpenOrder(
                order_id=o["order_id"],
                cli_ord_id=o.get("cliOrdId"),
                symbol=o["symbol"].upper(),
                side=o.get("side", ""),
                order_type=otype,
                size=_f(o.get("unfilledSize"), 0.0),
                filled=_f(o.get("filledSize"), 0.0),
                limit_price=_f(o.get("limitPrice")),
                stop_price=_f(o.get("stopPrice")),
                reduce_only=bool(o.get("reduceOnly", False)),
            ))
        return out

    async def get_fills(self, last_fill_time: str | None = None) -> list[Fill]:
        params = {"lastFillTime": last_fill_time} if last_fill_time else None
        payload = await self._request("GET", "fills", params, private=True)
        out = []
        for f in payload.get("fills", []):
            out.append(Fill(
                fill_id=f["fill_id"], order_id=f.get("order_id", ""), cli_ord_id=f.get("cliOrdId"),
                symbol=f["symbol"].upper(), side=f.get("side", ""), size=_f(f.get("size"), 0.0),
                price=_f(f.get("price"), 0.0), time=_iso_to_epoch(f.get("fillTime")) or 0.0,
                fill_type=f.get("fillType", ""),
            ))
        return out

    async def get_leverage_preferences(self) -> list[dict]:
        # VERIFICAR: en Kraken Futures fijar maxLeverage para un símbolo lo pone en margen aislado.
        return (await self._request("GET", "leveragepreferences", private=True)).get("leveragePreferences", [])

    async def set_leverage_preference(self, symbol: str, max_leverage: float | None) -> dict:
        params = {"symbol": symbol}
        if max_leverage is not None:
            params["maxLeverage"] = _fmt(max_leverage)
        return await self._request("PUT", "leveragepreferences", params, private=True)

    async def send_order(self, req: OrderRequest) -> OrderAck:
        params: dict[str, Any] = {
            "orderType": req.order_type,
            "symbol": req.symbol,
            "side": req.side,
            "size": _fmt(req.size),
            "cliOrdId": req.cli_ord_id,
            "limitPrice": _fmt(req.limit_price) if req.limit_price is not None else None,
            "stopPrice": _fmt(req.stop_price) if req.stop_price is not None else None,
            "reduceOnly": "true" if req.reduce_only else None,
            "triggerSignal": req.trigger_signal,
        }
        payload = await self._request("POST", "sendorder", params, private=True, idempotent=False)
        return _parse_send_status(req.cli_ord_id, payload.get("sendStatus", {}))

    async def edit_order(self, *, order_id: str | None = None, cli_ord_id: str | None = None,
                         size: float | None = None, limit_price: float | None = None,
                         stop_price: float | None = None) -> dict:
        params = {
            "orderId": order_id, "cliOrdId": cli_ord_id if not order_id else None,
            "size": _fmt(size) if size is not None else None,
            "limitPrice": _fmt(limit_price) if limit_price is not None else None,
            "stopPrice": _fmt(stop_price) if stop_price is not None else None,
        }
        payload = await self._request("POST", "editorder", params, private=True, idempotent=False)
        status = payload.get("editStatus", {}).get("status", "")
        if status != "edited":
            raise KrakenAPIError("editorder", status or "unknown", payload)
        return payload

    async def cancel_order(self, *, order_id: str | None = None, cli_ord_id: str | None = None) -> str:
        """Cancelar es idempotente (cancelar algo ya cancelado da notFound): se puede reintentar."""
        params = {"order_id": order_id} if order_id else {"cliOrdId": cli_ord_id}
        payload = await self._request("POST", "cancelorder", params, private=True, idempotent=True)
        return payload.get("cancelStatus", {}).get("status", "")

    async def cancel_all_orders(self, symbol: str | None = None) -> dict:
        return await self._request("POST", "cancelallorders", {"symbol": symbol}, private=True,
                                   idempotent=True)


def _parse_send_status(cli_ord_id: str, st: dict) -> OrderAck:
    filled, notional = 0.0, 0.0
    for ev in st.get("orderEvents", []) or []:
        if ev.get("type") == "EXECUTION":
            amt = _f(ev.get("amount"), 0.0)
            filled += amt
            notional += amt * _f(ev.get("price"), 0.0)
    return OrderAck(
        order_id=st.get("order_id"),
        cli_ord_id=st.get("cliOrdId") or cli_ord_id,
        status=st.get("status", "unknown"),
        filled_size=filled,
        avg_fill_price=notional / filled if filled else None,
        raw=st,
    )
