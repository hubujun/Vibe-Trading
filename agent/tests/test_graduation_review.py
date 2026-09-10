"""毕业评审 --apply 晋升路径回归测试 (2026-09-10 修复).

原实现有两处连环 bug, 导致 20 笔样本达标后 --apply 实际晋升 0 条:
1. ``hyps.get("hypotheses", [])`` —— hypotheses.json 顶层是 JSON 列表,
   list 上没有 ``.get`` → AttributeError 抛在写回前;
2. 匹配键 ``seeded_strategy_id`` 不在注册表 schema 里 (136 条假设 0 条有)
   → 即使不崩也永远匹配不到。

修复: 统一走 HypothesisRegistry, 策略 ↔ 假设按 ``signal_definition`` 匹配。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.strategy.graduation_review import _apply_promotion


def _write_hypotheses(path: Path, records: list[dict]) -> None:
    """按生产格式写假设库 —— 顶层必须是 JSON **列表**."""
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def _hyp(hid: str, sd: str, status: str, created: str) -> dict:
    return {
        "hypothesis_id": hid,
        "title": f"假设 {hid}",
        "thesis": "测试用",
        "status": status,
        "universe": "crypto",
        "signal_definition": sd,
        "data_sources": [],
        "skills": [],
        "run_cards": [],
        "invalidation_notes": "",
        "created_at": created,
        "updated_at": created,
    }


def _read(path: Path) -> dict[str, dict]:
    return {h["hypothesis_id"]: h for h in json.loads(path.read_text(encoding="utf-8"))}


class TestApplyPromotion:
    def test_promotes_matching_signal_definition(self, tmp_path: Path) -> None:
        path = tmp_path / "hypotheses.json"
        _write_hypotheses(
            path,
            [
                _hyp("hyp_a", "combo_variant: factors=[BAB,high52w]", "testing", "2026-08-01T00:00:00Z"),
                _hyp("hyp_b", "combo_variant: factors=[BAB,smb]", "testing", "2026-08-02T00:00:00Z"),
                _hyp("hyp_c", "combo_variant: factors=[BAB,high52w]", "rejected", "2026-08-03T00:00:00Z"),
            ],
        )

        changed = _apply_promotion(
            {"signal_definition": "combo_variant: factors=[BAB,high52w]"},
            {"n": 20, "cum": 6.5},
            hypo_path=path,
        )

        assert changed == 1
        records = _read(path)
        assert records["hyp_a"]["status"] == "validated"
        assert "毕业评审" in records["hyp_a"]["invalidation_notes"]
        assert records["hyp_b"]["status"] == "testing"  # 别的 signal_definition 不动
        assert records["hyp_c"]["status"] == "rejected"  # 非 testing 不动

    def test_list_format_does_not_crash(self, tmp_path: Path) -> None:
        """回归: 顶层 list 曾让旧实现 AttributeError (list 无 .get)."""
        path = tmp_path / "hypotheses.json"
        _write_hypotheses(path, [_hyp("hyp_a", "sd_x", "testing", "2026-08-01T00:00:00Z")])

        assert _apply_promotion({"signal_definition": "sd_x"}, {"n": 20, "cum": 9.0}, path) == 1
        assert _read(path)["hyp_a"]["status"] == "validated"

    def test_no_signal_definition_is_noop(self, tmp_path: Path) -> None:
        path = tmp_path / "hypotheses.json"
        _write_hypotheses(path, [_hyp("hyp_a", "sd_x", "testing", "2026-08-01T00:00:00Z")])

        assert _apply_promotion({}, {"n": 20, "cum": 9.0}, path) == 0
        assert _read(path)["hyp_a"]["status"] == "testing"

    def test_unmatched_signal_definition_noop(self, tmp_path: Path) -> None:
        path = tmp_path / "hypotheses.json"
        _write_hypotheses(path, [_hyp("hyp_a", "sd_x", "testing", "2026-08-01T00:00:00Z")])

        assert _apply_promotion({"signal_definition": "sd_y"}, {"n": 20, "cum": 9.0}, path) == 0
        assert _read(path)["hyp_a"]["status"] == "testing"

    def test_registry_resave_preserves_schema(self, tmp_path: Path) -> None:
        """写回不得丢字段 (旧实现手改 dict, 新实现走 registry 重建)."""
        path = tmp_path / "hypotheses.json"
        _write_hypotheses(path, [_hyp("hyp_a", "sd_x", "testing", "2026-08-01T00:00:00Z")])

        _apply_promotion({"signal_definition": "sd_x"}, {"n": 20, "cum": 9.0}, path)

        record = _read(path)["hyp_a"]
        for key in ("hypothesis_id", "title", "thesis", "signal_definition", "created_at"):
            assert key in record
        assert record["created_at"] == "2026-08-01T00:00:00Z"
