"""combo 执行器测试 — 订单计算/风控门/币种映射 (2026-08-30).

核心可测逻辑: build_orders (目标持仓 vs 实际持仓 → 开/平订单),
_perp_inst 映射, allocation_per_leg 兜底.
"""

from __future__ import annotations

from src.strategy.combo_executor import (
    _perp_inst,
    allocation_per_leg,
    build_orders,
)


class TestPerpInst:
    def test_spot_to_swap(self) -> None:
        assert _perp_inst("OKB-USDT") == "OKB-USDT-SWAP"
        assert _perp_inst("btc-usdt") == "BTC-USDT-SWAP"

    def test_already_swap(self) -> None:
        assert _perp_inst("BTC-USDT-SWAP") == "BTC-USDT-SWAP"


class TestAllocation:
    def test_default_fallback(self, monkeypatch) -> None:
        # mandate 读不到 → 兜底 250 (1500/6)
        monkeypatch.setattr(
            "src.live.mandate.store.load_mandate", lambda broker: None
        )
        assert allocation_per_leg() == 250.0


class TestBuildOrders:
    def test_open_new_positions(self) -> None:
        orders = build_orders(
            target_longs=["BTC-USDT", "OKB-USDT"],
            target_shorts=["LAB-USDT"],
            current={},
            per_leg_long=250.0,
        )
        assert len(orders) == 3
        by_sym = {o["symbol"]: o for o in orders}
        assert by_sym["BTC-USDT-SWAP"]["side"] == "buy"
        assert by_sym["BTC-USDT-SWAP"]["action"] == "open"
        assert by_sym["OKB-USDT-SWAP"]["side"] == "buy"
        assert by_sym["LAB-USDT-SWAP"]["side"] == "sell"  # 空头 = sell 开空
        assert all(o["notional"] == 250.0 for o in orders)

    def test_close_removed_positions(self) -> None:
        current = {
            "DOGE-USDT-SWAP": {"side": "long", "size": 1.0, "notional": 250.0},
        }
        orders = build_orders(
            target_longs=["BTC-USDT"], target_shorts=[],
            current=current, per_leg_long=250.0,
        )
        closes = [o for o in orders if o["action"] == "close"]
        assert len(closes) == 1
        assert closes[0]["symbol"] == "DOGE-USDT-SWAP"
        assert closes[0]["side"] == "sell"  # 平多 = sell
        assert closes[0]["target"] == "flat"

    def test_matching_positions_untouched(self) -> None:
        current = {
            "BTC-USDT-SWAP": {"side": "long", "size": 1.0, "notional": 250.0},
        }
        orders = build_orders(
            target_longs=["BTC-USDT"], target_shorts=[],
            current=current, per_leg_long=250.0,
        )
        assert orders == []  # 已持有且方向一致 → 不动

    def test_flip_short_position(self) -> None:
        """目标从多翻空: 实际多头 BTC, 目标列表无 BTC → 平多."""
        current = {
            "BTC-USDT-SWAP": {"side": "long", "size": 1.0, "notional": 250.0},
        }
        orders = build_orders(
            target_longs=[], target_shorts=["ETH-USDT"],
            current=current, per_leg_long=250.0,
        )
        closes = [o for o in orders if o["action"] == "close"]
        opens = [o for o in orders if o["action"] == "open"]
        assert len(closes) == 1 and closes[0]["symbol"] == "BTC-USDT-SWAP"
        assert len(opens) == 1 and opens[0]["symbol"] == "ETH-USDT-SWAP"
        assert opens[0]["side"] == "sell"  # 新空头

    def test_scaled_legs_by_side(self) -> None:
        """风控乘数缩放: 多头腿与空头腿名义不同 (long/short_mult)."""
        orders = build_orders(
            target_longs=["BTC-USDT"], target_shorts=["LAB-USDT"],
            current={},
            per_leg_long=250.0, per_leg_short=125.0,
        )
        by_sym = {o["symbol"]: o for o in orders}
        assert by_sym["BTC-USDT-SWAP"]["notional"] == 250.0   # 多头腿
        assert by_sym["LAB-USDT-SWAP"]["notional"] == 125.0   # 空头腿(regime/风控打折)

    def test_per_leg_short_defaults_to_long(self) -> None:
        """未传 per_leg_short → 与 long 相同 (向后兼容)."""
        orders = build_orders(
            target_longs=["BTC-USDT"], target_shorts=["LAB-USDT"],
            current={}, per_leg_long=200.0,
        )
        assert all(o["notional"] == 200.0 for o in orders)
