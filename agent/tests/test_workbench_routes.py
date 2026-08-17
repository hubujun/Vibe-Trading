"""API tests for the strategy lifecycle workbench.

Covers ``GET /api/workbench`` (aggregated pipeline view: strategy state
machine + combo research data + autopilot execution data) and
``POST /api/workbench/strategies/{sid}/transition`` (lifecycle moves:
research → paper → live, pause/resume, back_to_research).

All persisted artifacts are stubbed under a tmp root — no real cron job,
autopilot process or broker is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import api_server
from src.api import autopilot_routes, combo_routes, workbench_routes

__all__ = []


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    # Strategy state machine file lives under <tmp>/workbench/strategies.json
    monkeypatch.setattr(workbench_routes, "_WORKBENCH_ROOT", tmp_path / "workbench")
    monkeypatch.setattr(
        workbench_routes, "_STRATEGIES_PATH", tmp_path / "workbench" / "strategies.json"
    )
    # Combo paper runtime (signal / paper / metrics loaders)
    monkeypatch.setattr(workbench_routes, "_COMBO_RUNTIME_ROOT", tmp_path / "runs" / "paper_combo")
    monkeypatch.setattr(combo_routes, "_RUNTIME_ROOT", tmp_path / "runs" / "paper_combo")
    # Autopilot runtime + live tree (halt sentinel) — same redirection as autopilot tests
    monkeypatch.setattr(autopilot_routes, "_RUNTIME_ROOT", tmp_path / "autopilot")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path), raising=False)
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def _write_combo_state(tmp_path: Path, payload: dict) -> None:
    combo_dir = tmp_path / "runs" / "paper_combo"
    combo_dir.mkdir(parents=True, exist_ok=True)
    (combo_dir / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def _read_persisted(tmp_path: Path) -> list[dict]:
    raw = json.loads((tmp_path / "workbench" / "strategies.json").read_text(encoding="utf-8"))
    return raw["strategies"]


# ---------------------------------------------------------------------------
# GET /api/workbench — aggregation
# ---------------------------------------------------------------------------


class TestWorkbenchSummary:
    def test_summary_empty_seeds_research(self, tmp_path: Path, monkeypatch) -> None:
        """No artifacts → seeded strategy in research, combo empty, autopilot dormant."""
        client = _client(tmp_path, monkeypatch)

        response = client.get("/api/workbench")

        assert response.status_code == 200
        body = response.json()
        assert body["strategies"][0]["strategy_id"] == "combo_bab_52w"
        assert body["strategies"][0]["phase"] == "research"
        assert body["strategies"][0]["factors"] == ["BAB", "high52w"]
        assert body["combo"]["signal"]["longs"] == []
        assert body["autopilot"]["pipeline"]["phase"] == "idle"
        assert body["autopilot"]["health"]["alive"] is False

    def test_summary_infers_paper_phase(self, tmp_path: Path, monkeypatch) -> None:
        """paper_combo state with started_at → seeded strategy phase = paper."""
        _write_combo_state(tmp_path, {"started_at": "2026-07-01T00:00:00", "nav": 1.2})
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/workbench").json()

        assert body["strategies"][0]["phase"] == "paper"
        assert body["combo"]["paper"]["nav"] == 1.2

    def test_summary_aggregates_signal_and_hypotheses(self, tmp_path: Path, monkeypatch) -> None:
        """Signal + hypotheses flow through the aggregated payload."""
        _write_combo_state(
            tmp_path,
            {
                "started_at": "2026-07-01T00:00:00",
                "last_signal_date": "2026-08-17",
                "last_longs": ["BTC-USDT"],
                "last_shorts": ["DOGE-USDT"],
                "scores": {"BTC-USDT": 1.2, "DOGE-USDT": -1.1},
            },
        )
        hypo_dir = tmp_path / ".vibe-trading"
        hypo_dir.mkdir(parents=True, exist_ok=True)
        (hypo_dir / "hypotheses.json").write_text(
            json.dumps(
                [
                    {
                        "hypothesis_id": "H-001",
                        "title": "低贝塔溢价在币圈存在",
                        "status": "validating",
                    }
                ]
            ),
            encoding="utf-8",
        )
        client = _client(tmp_path, monkeypatch)

        body = client.get("/api/workbench").json()

        assert body["combo"]["signal"]["longs"][0]["symbol"] == "BTC-USDT"
        assert body["combo"]["hypotheses"][0]["hypothesis_id"] == "H-001"


# ---------------------------------------------------------------------------
# POST transition — lifecycle state machine
# ---------------------------------------------------------------------------


class TestTransition:
    def test_research_to_paper(self, tmp_path: Path, monkeypatch) -> None:
        client = _client(tmp_path, monkeypatch)

        response = client.post(
            "/api/workbench/strategies/combo_bab_52w/transition",
            json={"action": "start_paper", "note": "回测通过, 上模拟盘"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["phase"] == "paper"
        assert body["phase_history"][-1]["action"] == "start_paper"
        # Persisted
        assert _read_persisted(tmp_path)[0]["phase"] == "paper"

    def test_paper_to_live(self, tmp_path: Path, monkeypatch) -> None:
        client = _client(tmp_path, monkeypatch)
        client.post(
            "/api/workbench/strategies/combo_bab_52w/transition", json={"action": "start_paper"}
        )

        response = client.post(
            "/api/workbench/strategies/combo_bab_52w/transition",
            json={"action": "promote_live"},
        )

        assert response.status_code == 200
        assert response.json()["phase"] == "live"

    def test_live_pause_resume_cycle(self, tmp_path: Path, monkeypatch) -> None:
        client = _client(tmp_path, monkeypatch)
        for action in ("start_paper", "promote_live"):
            client.post(
                "/api/workbench/strategies/combo_bab_52w/transition", json={"action": action}
            )

        paused = client.post(
            "/api/workbench/strategies/combo_bab_52w/transition",
            json={"action": "pause", "note": "宏观数据周, 手动暂停"},
        ).json()
        assert paused["phase"] == "paused"
        assert paused["paused_from"] == "live"

        resumed = client.post(
            "/api/workbench/strategies/combo_bab_52w/transition", json={"action": "resume"}
        ).json()
        assert resumed["phase"] == "live"
        assert resumed["paused_from"] is None

    def test_back_to_research_from_live(self, tmp_path: Path, monkeypatch) -> None:
        client = _client(tmp_path, monkeypatch)
        for action in ("start_paper", "promote_live"):
            client.post(
                "/api/workbench/strategies/combo_bab_52w/transition", json={"action": action}
            )

        response = client.post(
            "/api/workbench/strategies/combo_bab_52w/transition", json={"action": "back_to_research"}
        )

        assert response.status_code == 200
        assert response.json()["phase"] == "research"

    def test_illegal_transition_409(self, tmp_path: Path, monkeypatch) -> None:
        """promote_live from research is illegal."""
        client = _client(tmp_path, monkeypatch)

        response = client.post(
            "/api/workbench/strategies/combo_bab_52w/transition", json={"action": "promote_live"}
        )

        assert response.status_code == 409
        assert "非法迁移" in response.json()["detail"]

    def test_pause_while_paused_409(self, tmp_path: Path, monkeypatch) -> None:
        client = _client(tmp_path, monkeypatch)
        client.post(
            "/api/workbench/strategies/combo_bab_52w/transition", json={"action": "start_paper"}
        )
        client.post(
            "/api/workbench/strategies/combo_bab_52w/transition", json={"action": "pause"}
        )

        response = client.post(
            "/api/workbench/strategies/combo_bab_52w/transition", json={"action": "pause"}
        )

        assert response.status_code == 409

    def test_unknown_action_409(self, tmp_path: Path, monkeypatch) -> None:
        client = _client(tmp_path, monkeypatch)

        response = client.post(
            "/api/workbench/strategies/combo_bab_52w/transition", json={"action": "teleport"}
        )

        assert response.status_code == 409


class TestDeleteStrategy:
    def test_delete_seeded_strategy(self, tmp_path: Path, monkeypatch) -> None:
        """播种的变体策略可删除; 删后 404."""
        client = _client(tmp_path, monkeypatch)
        r = client.post(
            "/api/workbench/strategies",
            json={
                "signal_definition": "combo_variant: factors=[BAB,high52w,volume_surge_reversal] weights={BAB:0.33,high52w:0.33,volume_surge_reversal:0.33} top_n=3 bot_n=3",
                "name": "临时变体",
            },
        )
        assert r.status_code == 200
        sid = r.json()["strategy_id"]
        assert sid != "combo_bab_52w"

        d = client.delete(f"/api/workbench/strategies/{sid}")
        assert d.status_code == 200
        assert d.json()["deleted"] == sid

        r2 = client.delete(f"/api/workbench/strategies/{sid}")
        assert r2.status_code == 404

    def test_delete_base_strategy_rejected(self, tmp_path: Path, monkeypatch) -> None:
        """基策略 (默认种子) 不可删除."""
        client = _client(tmp_path, monkeypatch)

        r = client.delete("/api/workbench/strategies/combo_bab_52w")

        assert r.status_code == 400

    def test_unknown_strategy_404(self, tmp_path: Path, monkeypatch) -> None:
        client = _client(tmp_path, monkeypatch)

        response = client.post(
            "/api/workbench/strategies/ghost_strategy/transition", json={"action": "start_paper"}
        )

        assert response.status_code == 404

    def test_persisted_state_survives_across_calls(self, tmp_path: Path, monkeypatch) -> None:
        """A second client (fresh load) sees the persisted phase."""
        client = _client(tmp_path, monkeypatch)
        client.post(
            "/api/workbench/strategies/combo_bab_52w/transition", json={"action": "start_paper"}
        )

        client2 = _client(tmp_path, monkeypatch)
        body = client2.get("/api/workbench").json()

        assert body["strategies"][0]["phase"] == "paper"
