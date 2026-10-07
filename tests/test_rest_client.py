"""Cliente REST contra un servidor HTTP local (sin red externa)."""
import pytest
from aiohttp import web

from xrpbot.exchange.rest import KrakenAPIError, KrakenFuturesRest


async def start(handler):
    app = web.Application()
    app.router.add_get("/derivatives/api/v3/instruments", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def test_non_json_response_gives_clear_error():
    async def html(request):
        return web.Response(text="<html>Access denied</html>", status=403, content_type="text/html")

    runner, base = await start(html)
    try:
        async with KrakenFuturesRest(f"{base}/derivatives/api/v3", f"{base}/api/charts/v1") as rest:
            with pytest.raises(KrakenAPIError) as exc:
                await rest.get_instruments()
        assert "no JSON" in str(exc.value) and "HTTP 403" in str(exc.value) and "Access denied" in str(exc.value)
    finally:
        await runner.cleanup()


async def test_json_response_and_user_agent():
    seen = {}

    async def ok(request):
        seen["ua"] = request.headers.get("User-Agent")
        return web.json_response({"result": "success", "instruments": [{"symbol": "PF_XRPUSD"}],
                                  "serverTime": "2026-01-01T00:00:00.000Z"})

    runner, base = await start(ok)
    try:
        async with KrakenFuturesRest(f"{base}/derivatives/api/v3", f"{base}/api/charts/v1") as rest:
            assert (await rest.get_instruments())[0]["symbol"] == "PF_XRPUSD"
        assert seen["ua"].startswith("xrpbot/")
    finally:
        await runner.cleanup()
