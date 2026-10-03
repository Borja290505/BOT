import pytest
import yaml

from xrpbot.config import ConfigError, Mode, load_settings


def write(tmp_path, data):
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_default_mode_is_demo(tmp_path):
    s = load_settings(write(tmp_path, {}), env_file=None)
    assert s.mode is Mode.DEMO
    assert "demo-futures" in s.urls["rest"]
    assert s.storage.db_path.endswith("xrpbot-demo.sqlite")


def test_demo_and_live_keys_are_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAKEN_DEMO_API_KEY", "demo-key")
    monkeypatch.setenv("KRAKEN_DEMO_API_SECRET", "ZGVtbw==")
    monkeypatch.setenv("KRAKEN_LIVE_API_KEY", "live-key")
    monkeypatch.setenv("KRAKEN_LIVE_API_SECRET", "bGl2ZQ==")
    demo = load_settings(write(tmp_path, {"mode": "demo"}), env_file=None)
    live = load_settings(write(tmp_path, {"mode": "live"}), env_file=None)
    sim = load_settings(write(tmp_path, {"mode": "sim"}), env_file=None)
    assert demo.secrets.api_key == "demo-key" and live.secrets.api_key == "live-key"
    assert sim.secrets.api_key is None
    assert "live-key" not in repr(live.secrets)
    assert "demo-futures" not in live.urls["rest"]


def test_secrets_never_from_yaml(tmp_path):
    s = load_settings(write(tmp_path, {"secrets": {"api_key": "x"}}), env_file=None)
    assert s.secrets.api_key is None


@pytest.mark.parametrize("bad", [
    {"risk": {"risk_per_trade": 0.1}},
    {"risk": {"max_leverage": 20}},
    {"risk": {"max_daily_loss": 0.005}},
    {"risk": {"max_open_positions": 2}},
    {"timeframe": "5m"},
    {"strategy": {"entry_lookback": 10, "exit_lookback": 20}},
    {"risk": {"typo_key": 1}},
    {"mode": "yolo"},
])
def test_invalid_config_rejected(tmp_path, bad):
    with pytest.raises(ConfigError):
        load_settings(write(tmp_path, bad), env_file=None)


def test_leverage_up_to_10_allowed(tmp_path):
    s = load_settings(write(tmp_path, {"risk": {"max_leverage": 10}}), env_file=None)
    assert s.risk.max_leverage == 10
    with pytest.raises(ConfigError):
        load_settings(write(tmp_path, {"risk": {"max_leverage": 10.5}}), env_file=None)
