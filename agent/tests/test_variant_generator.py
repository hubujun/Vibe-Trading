"""Tests for the variant generator (Loop Engineering 第二圈).

Covers: trigger on validated/monitoring base strategy, variant creation,
dedup by signal_definition, per-round limit, and exploring-status writes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.hypotheses.registry import HypothesisRegistry
from src.strategy.variant_generator import (
    BASE_VARIANTS,
    BASE_STRATEGY,
    TRIGGER_STATUSES,
    generate_variants,
    variant_signal_definition,
)

__all__ = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _seed_registry(path: Path, status: str, title: str | None = None) -> HypothesisRegistry:
    registry = HypothesisRegistry(path)
    registry.create(
        title=title or f"{BASE_STRATEGY['name']} 基策略",
        thesis=f"{BASE_STRATEGY['name']} 回测验证",
        status=status,
        universe="crypto",
        signal_definition="combo_variant: base",
        data_sources=["okx_candles_1d"],
        skills=["crypto", "alpha-bench"],
    )
    return registry


def _statuses(path: Path) -> dict[str, str]:
    return {h.hypothesis_id: h.status for h in HypothesisRegistry(path).list()}


def _defs(path: Path) -> list[str]:
    return [h.signal_definition for h in HypothesisRegistry(path).list()]


class TestTrigger:
    def test_generates_when_base_validated(self, tmp_path: Path) -> None:
        registry = _seed_registry(tmp_path / "h.json", "validated")

        created = generate_variants(registry, max_new=2)

        assert len(created) == 2
        assert all(c["status"] == "exploring" for c in created)
        # 第二轮生成"下一批"模板, 且不重复第一批 (去重生效)
        created2 = generate_variants(registry, max_new=2)
        assert len(created2) == 2
        first_defs = {c["signal_definition"] for c in created}
        second_defs = {c["signal_definition"] for c in created2}
        assert first_defs.isdisjoint(second_defs)

    def test_generates_when_base_monitoring(self, tmp_path: Path) -> None:
        registry = _seed_registry(tmp_path / "h.json", "monitoring")

        created = generate_variants(registry, max_new=1)

        assert len(created) == 1

    @pytest.mark.parametrize("status", ["exploring", "testing", "rejected"])
    def test_no_generation_when_base_not_passed(self, tmp_path: Path, status: str) -> None:
        registry = _seed_registry(tmp_path / "h.json", status)

        created = generate_variants(registry, max_new=2)

        assert created == []

    def test_no_generation_without_base(self, tmp_path: Path) -> None:
        registry = HypothesisRegistry(tmp_path / "h.json")
        created = generate_variants(registry, max_new=2)
        assert created == []


class TestVariantContent:
    def test_variants_cover_weights_and_thresholds(self, tmp_path: Path) -> None:
        registry = _seed_registry(tmp_path / "h.json", "validated")

        created = generate_variants(registry, max_new=len(BASE_VARIANTS))

        assert len(created) == len(BASE_VARIANTS)
        defs = {c["signal_definition"] for c in created}
        # 权重变体
        assert "combo_variant: weights={BAB:0.3,high52w:0.7} top_n=3 bot_n=3" in defs
        # 因子变体
        assert any("RMW" in d for d in defs)
        # 阈值变体
        assert any("top_n=2" in d for d in defs)
        assert any("top_n=4" in d for d in defs)

    def test_signal_definition_roundtrip(self) -> None:
        d = variant_signal_definition({"weights": {"BAB": 0.7, "high52w": 0.3}, "top_n": 2})
        assert d == "combo_variant: weights={BAB:0.7,high52w:0.3} top_n=2 bot_n=3"
        # 空变体 = 基策略定义
        base = variant_signal_definition({})
        assert base == "combo_variant: weights={BAB:0.5,high52w:0.5} top_n=3 bot_n=3"


class TestDedup:
    def test_existing_variant_not_duplicated(self, tmp_path: Path) -> None:
        registry = _seed_registry(tmp_path / "h.json", "validated")
        first = generate_variants(registry, max_new=1)
        assert len(first) == 1

        # 新注册表实例 (模拟重启) 再跑 → 补满 2 个新模板, 不重复第一个
        registry2 = HypothesisRegistry(tmp_path / "h.json")
        second = generate_variants(registry2, max_new=2)

        assert len(second) == 2
        assert all(s["signal_definition"] != first[0]["signal_definition"] for s in second)
        assert len(_defs(tmp_path / "h.json")) == 4  # 基 + 3 个不同变体

    def test_variants_persisted_as_exploring(self, tmp_path: Path) -> None:
        registry = _seed_registry(tmp_path / "h.json", "validated")
        generate_variants(registry, max_new=2)

        statuses = _statuses(tmp_path / "h.json")
        assert list(statuses.values()).count("exploring") == 2
        assert list(statuses.values()).count("validated") == 1
