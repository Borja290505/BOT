"""Investigación: agregación de velas sin sesgo, filtro de tendencia y ejecución completa del informe."""
import json

import numpy as np
import pandas as pd

from conftest import path_candles
from xrpbot.data.store import candles_path, save_frame
from xrpbot.research.__main__ import main, resample
from xrpbot.strategy.donchian_atr import StrategyParams, compute_indicators


def test_resample_ohlc_and_drops_incomplete_bars():
    c = path_candles(n=4 * 24 + 3)                       # 4 días y 3 horas
    d = resample(c, 24)
    assert len(d) == 4                                  # el 5.º día está incompleto
    day1 = c.iloc[:24]
    assert d.iloc[0].open == day1.open.iloc[0] and d.iloc[0].close == day1.close.iloc[-1]
    assert d.iloc[0].high == day1.high.max() and d.iloc[0].low == day1.low.min()
    h4 = resample(c, 4)
    assert h4.index[0] == c.index[0] and (h4.index.hour % 4 == 0).all()


def test_trend_filter_only_removes_signals():
    c = path_candles(n=2000, seed=3)
    a = compute_indicators(c, StrategyParams())
    b = compute_indicators(c, StrategyParams(trend_ema=200))
    assert (b.long_signal <= a.long_signal).all() and (b.short_signal <= a.short_signal).all()
    assert not b.long_signal.iloc[:200].any()           # sin EMA válida no hay señal


def test_research_report_end_to_end(tmp_path):
    n = 24 * 30 * 30                                    # ~30 meses de velas de 1 h
    drift = np.zeros(n)
    drift[n // 5: n // 3] = 0.0015
    drift[2 * n // 3:] = -0.0015
    c = path_candles(n=n, seed=7, vol=0.006, drift=drift)
    c.index = pd.date_range("2022-01-01", periods=n, freq="1h", tz="UTC")
    data = tmp_path / "data"
    save_frame(c, candles_path(data, "PF_XRPUSD", "trade", "1h"))
    cfg = tmp_path / "s.yaml"
    cfg.write_text(f"backtest:\n  data_dir: {data}\n  stop_trigger_source: trade\n")
    out = tmp_path / "out"
    assert main(["--config", str(cfg), "--out", str(out)]) == 0
    text = (out / "informe.md").read_text()
    for part in ("Fase A", "Fase B", "Conclusión", "No garantizan"):
        assert part in text
    v = json.loads((out / "veredicto.json").read_text())
    assert set(v) == {"candidata", "fase_c", "holdout"}
    assert v["holdout"] is None or v["fase_c"] is True   # el holdout solo se toca si pasa la fase C
    assert len(pd.read_csv(out / "B_cribado.csv")) == 12


def test_basket_report_end_to_end(tmp_path):
    from xrpbot.research.basket import main as basket_main
    data = tmp_path / "data"
    n = 24 * 30 * 40                                    # ~40 meses
    instruments = []
    for k, sym in enumerate(["PF_XBTUSD", "PF_ETHUSD", "PF_XRPUSD", "PF_SOLUSD"]):
        drift = np.zeros(n)
        drift[n // 4: n // 2] = 0.0004 * (1 if k % 2 else -1)
        c = path_candles(n=n, seed=11 + k, vol=0.006, drift=drift, start_price=[60000, 3000, 0.5, 150][k])
        c.index = pd.date_range("2022-01-01", periods=n, freq="1h", tz="UTC")
        save_frame(c, candles_path(data, sym, "trade", "1h"))
        f = pd.DataFrame({"funding_rate": 0.0, "relative_rate": 0.00001},
                         index=pd.date_range("2022-01-01", periods=n, freq="1h", tz="UTC", name="time"))
        save_frame(f, data / f"{sym}_funding.parquet")
        instruments.append({"symbol": sym, "tradeable": True, "tickSize": 0.0001, "contractSize": 1,
                            "contractValuePrecision": 4, "marginLevels": [{"initialMargin": 0.02,
                                                                          "maintenanceMargin": 0.01}]})
    (data / "instruments.json").write_text(json.dumps(instruments))
    cfg = tmp_path / "s.yaml"
    cfg.write_text(f"backtest:\n  data_dir: {data}\n  stop_trigger_source: trade\n")
    out = tmp_path / "out"
    assert basket_main(["--config", str(cfg), "--out", str(out)]) == 0
    v = json.loads((out / "veredicto.json").read_text())
    assert v["contratos"] == ["PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD", "PF_XRPUSD"]
    assert v["holdout"] is None or v["fase_c"] is True
    text = (out / "informe.md").read_text()
    assert "PF_ADAUSD: sin datos descargados" in text and "Fase B" in text
