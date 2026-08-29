"""macro_events 单元测试: 事件降杠杆 + regime 判定 + 市场特征."""

import pandas as pd
import numpy as np
from datetime import date

from src.strategy.macro_events import (
    events_on,
    event_leverage_multiplier,
    get_regime,
    market_state_features,
)


def test_events_on_aug19():
    evs = events_on(date(2026, 8, 19))
    assert len(evs) == 2
    assert any(e["level"] == "A" for e in evs)


def test_event_leverage():
    assert event_leverage_multiplier(date(2026, 8, 19)) == 0.5  # A 级
    assert event_leverage_multiplier(date(2026, 8, 20)) == 1.0  # 无事件


def test_regime_risk_on():
    idx = pd.date_range("2026-07-01", periods=60, freq="D")
    up = pd.Series(np.linspace(100, 130, 60), index=idx)
    r = get_regime(pd.DataFrame({"BTC-USDT": up, "ETH-USDT": up * 1.1}))
    assert r["regime"] == "risk_on"
    assert r["long_factor"] == 1.0 and r["short_factor"] == 1.0


def test_regime_risk_off_short_discount():
    idx = pd.date_range("2026-07-01", periods=60, freq="D")
    dn = pd.Series(np.linspace(100, 70, 60), index=idx)
    r = get_regime(pd.DataFrame({"BTC-USDT": dn, "ETH-USDT": dn * 0.9}))
    assert r["regime"] == "risk_off"
    assert r["long_factor"] == 0.7 and r["short_factor"] == 0.5  # 空头腿打折


def test_regime_neutral_without_data():
    r = get_regime(None)
    assert r["regime"] == "neutral"
    assert r["long_factor"] == 1.0


def test_market_state_features():
    idx = pd.date_range("2026-07-01", periods=60, freq="D")
    up = pd.Series(np.linspace(100, 130, 60), index=idx)
    f = market_state_features(pd.DataFrame({"BTC-USDT": up, "ETH-USDT": up * 1.1}))
    assert f["btc_mom_20d"] > 0
    assert 0 <= f["breadth_20d"] <= 1
