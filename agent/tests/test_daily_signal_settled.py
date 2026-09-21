"""未结算蜡烛裁剪测试 — 修「模拟盘每天只统计 7 小时行情」的护栏.

背景 (2026-09-21): 信号 cron 07:00 运行, 日线面板最后一根是当日进行中的蜡烛
(北京 00:00 起只走了 7 小时)。原实现拿它当已结算收盘价打分、记账又只覆盖这 7 小时,
导致模拟盘收益/波动被压缩到 19~25%、与回测(完整日收益)口径差 4~5 倍。
`daily_signal._settled_panel` 负责剔除它 — 本文件守这条语义。
"""
from datetime import datetime

import numpy as np
import pandas as pd

import daily_signal
from daily_signal import SIGNAL_LOGIC_VERSION, _settled_panel

N = 400  # > 300 的最小面板门槛


def _mk_panel(days: int = N, end: str = "2026-09-21") -> dict:
    idx = pd.date_range(end=end, periods=days, freq="D")
    rng = np.random.default_rng(3)
    close = pd.DataFrame(
        100 + np.cumsum(rng.standard_normal((days, 3)), axis=0),
        index=idx, columns=["A-USDT", "B-USDT", "C-USDT"],
    )
    return {
        "close": close,
        "volume": close * 10,
        "high": close * 1.01,
        "low": close * 0.99,
        "open": close * 0.995,
    }


def _trim(panel: dict, now: datetime):
    return _settled_panel(
        panel["close"], panel["volume"], panel["high"], panel["low"], panel["open"], now=now
    )


class TestSettledPanel:
    def test_drops_in_progress_candle(self) -> None:
        """面板最后一根 = 今天(进行中) → 必须剔除. 07:00 跑信号即为该场景."""
        panel = _mk_panel(end="2026-09-21")
        now = datetime(2026, 9, 21, 7, 0)  # 北京 07:00, 当日蜡烛只走了 7 小时
        close, volume, high, low, open_ = _trim(panel, now)

        assert close.index[-1] == pd.Timestamp("2026-09-20"), "最后一根未结算蜡烛没被剔除"
        assert len(close) == N - 1
        # 兄弟帧必须同步裁剪, 否则因子 compute 因索引错位 KeyError
        for df in (volume, high, low, open_):
            assert list(df.index) == list(close.index)

    def test_keeps_panel_ending_yesterday(self) -> None:
        """面板本就止于昨日 (已结算) → 原样返回."""
        panel = _mk_panel(end="2026-09-20")
        now = datetime(2026, 9, 21, 7, 0)
        close, *_ = _trim(panel, now)

        assert len(close) == N
        assert close.index[-1] == pd.Timestamp("2026-09-20")

    def test_guard_never_shrinks_below_min_rows(self) -> None:
        """裁剪后不足 300 行 → 放弃裁剪 (不因修复把面板做废)."""
        panel = _mk_panel(days=300, end="2026-09-21")
        now = datetime(2026, 9, 21, 7, 0)
        close, *_ = _trim(panel, now)

        assert len(close) == 300, "裁剪后 <300 行时应保持原样"

    def test_drops_every_row_from_today_on(self) -> None:
        """今天及之后的行全部剔除 (极端: 数据源给出未来日期)."""
        panel = _mk_panel(end="2026-09-21")
        now = datetime(2026, 9, 18, 7, 0)
        close, *_ = _trim(panel, now)

        assert close.index[-1] == pd.Timestamp("2026-09-17")
        assert len(close) == N - 4

    def test_logic_version_bumped(self) -> None:
        """记账口径变更必须 bump 版本 — 老样本按 logic_version 分组隔离."""
        assert SIGNAL_LOGIC_VERSION >= 5

    def test_build_signal_actually_uses_settled_panel(self) -> None:
        """护栏: 信号主流程必须真的走裁剪 (防以后被摘掉、退回 7 小时口径)."""
        import inspect

        assert "_settled_panel(" in inspect.getsource(daily_signal.build_signal)

    def test_settled_semantics_each_candle_booked_once(self) -> None:
        """修后语义: 用已结算蜡烛出信号、赚后一根完整蜡烛 (对齐回测 w.shift(1)).

        模拟 07:00 连续三天: 每次取已结算面板的最后一根 → 三天分别记账 09-19/09-20/09-21
        三根完整蜡烛, 每根恰好一次 (老实现是每天只记 0-7 点切片)。
        """
        panel = _mk_panel(end="2026-09-23")
        booked = []
        for day in ("2026-09-22", "2026-09-23", "2026-09-24"):
            close, *_ = _trim(panel, datetime.fromisoformat(day + "T07:00"))
            booked.append(close.index[-1])
        assert booked == [pd.Timestamp(d) for d in ("2026-09-21", "2026-09-22", "2026-09-23")]


__all__ = []
