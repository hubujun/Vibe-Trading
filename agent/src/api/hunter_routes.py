"""事件猎手 (hunter): 百倍杠杆事件机会跟踪 — 本地增强, 与上游无关.

数据: ~/.vibe-trading/hunter_state.json
  {"opportunities": [...], "shots": [...]}

机会状态机: watching(观察中) -> triggered(已触发) -> won(赢了) / lost(爆了) / discarded(放弃)
开仓记录 shots = 实盘账本: 每次冲进去的一注 (面值/杠杆/盈亏), 用于攒这个玩法的真实样本.

铁律 (源自 2026-08 strategies.json 覆盖事故):
- GET 端点只读, 绝不带写副作用; 所有写操作走显式 POST/PATCH/DELETE.
- 写文件用 tmp + rename 原子替换, 每次写全量 (opportunities + shots 同一 dict).
"""

from __future__ import annotations

import json
import sys as _sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

_HUNTER_PATH = Path.home() / ".vibe-trading" / "hunter_state.json"
_PAPER_RESULTS_PATH = Path.home() / ".vibe-trading" / "hunter" / "paper_results.json"

#: 机会类型 (与前端 KIND_LABEL 一一对应).
OPPORTUNITY_KINDS = {
    "listing": "上新/首日",
    "squeeze": "轧空点火",
    "liquidation": "清算瀑布",
    "blowoff": "赶顶反转",
    "depeg": "脱锚修复",
    "other": "其他",
}
#: 机会状态.
OPPORTUNITY_STATUSES = ("watching", "triggered", "won", "lost", "discarded")
SHOT_OUTCOMES = ("open", "won", "lost")

_CN_TZ = timezone(timedelta(hours=8))


def _cn_now() -> str:
    """北京时间 YYYY-MM-DD HH:MM (与页面/CSV 记录口径一致)."""
    return datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _load() -> dict[str, Any]:
    """读 hunter_state.json; 缺失/损坏返回空结构 (读失败不覆盖文件)."""
    try:
        raw = json.loads(_HUNTER_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("opportunities", [])
    raw.setdefault("shots", [])
    return raw


def _save(data: dict[str, Any]) -> None:
    """原子写全量 (tmp + rename)."""
    _HUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _HUNTER_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(_HUNTER_PATH)


# --- Pydantic 请求模型 -----------------------------------------------------


class OpportunityIn(BaseModel):
    inst: str = Field(min_length=1, max_length=64)
    kind: str = "other"
    direction: Literal["long", "short"] = "long"
    catalyst: str = Field(default="", max_length=500)
    trigger: str = Field(default="", max_length=500)
    plan: str = Field(default="", max_length=1000)
    note: str = Field(default="", max_length=1000)
    status: str = "watching"


class OpportunityPatch(BaseModel):
    inst: Optional[str] = Field(default=None, min_length=1, max_length=64)
    kind: Optional[str] = None
    direction: Optional[Literal["long", "short"]] = None
    catalyst: Optional[str] = Field(default=None, max_length=500)
    trigger: Optional[str] = Field(default=None, max_length=500)
    plan: Optional[str] = Field(default=None, max_length=1000)
    note: Optional[str] = Field(default=None, max_length=1000)
    status: Optional[str] = None


class ShotIn(BaseModel):
    inst: str = Field(min_length=1, max_length=64)
    direction: Literal["long", "short"] = "long"
    leverage: float = Field(default=100.0, ge=1, le=200)
    margin: float = Field(default=0.0, ge=0)  # 保证金面值 (U)
    entry: Optional[float] = None
    exit: Optional[float] = None
    pnl: Optional[float] = None  # 已实现盈亏 (U, 带符号)
    outcome: str = "open"
    at: str = ""  # 留空 = 后端填北京时间
    note: str = Field(default="", max_length=1000)


def _validate_kind(kind: str) -> None:
    if kind not in OPPORTUNITY_KINDS:
        raise HTTPException(400, f"未知机会类型: {kind}")


def _validate_status(status: str, allowed: tuple[str, ...]) -> None:
    if status not in allowed:
        raise HTTPException(400, f"非法状态: {status}")


# --- 路由注册 ---------------------------------------------------------------

AuthDep = Any


def register_hunter_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
) -> None:
    """Mount the hunter (事件猎手) routes onto ``app``."""

    h = _sys.modules.get("api_server")
    if h is None:
        raise RuntimeError(
            "register_hunter_routes: api_server module not in sys.modules; "
            "ensure api_server is imported before calling this function"
        )
    if require_auth is None:
        require_auth = h.require_auth

    @app.get(
        "/api/hunter",
        dependencies=[Depends(require_auth)],
    )
    def hunter_summary() -> dict[str, Any]:
        """事件猎手全量: 候选机会 + 开仓账本. 只读, 无写副作用."""
        return _load()

    @app.get(
        "/api/hunter/paper",
        dependencies=[Depends(require_auth)],
    )
    def hunter_paper() -> dict[str, Any]:
        """玩法体检: 回测/模拟盘汇总 (listing 上新首日 + squeeze 深负费率). 只读."""
        try:
            return json.loads(_PAPER_RESULTS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    # ---- 候选机会 ----

    @app.post(
        "/api/hunter/opportunities",
        dependencies=[Depends(require_auth)],
    )
    def create_opportunity(body: OpportunityIn) -> dict[str, Any]:
        _validate_kind(body.kind)
        _validate_status(body.status, OPPORTUNITY_STATUSES)
        data = _load()
        now = _cn_now()
        opp = body.model_dump()
        opp.update(
            {
                "id": _new_id(),
                "created_at": now,
                "updated_at": now,
            }
        )
        data["opportunities"].insert(0, opp)  # 新的放最前
        _save(data)
        return opp

    @app.patch(
        "/api/hunter/opportunities/{oid}",
        dependencies=[Depends(require_auth)],
    )
    def update_opportunity(oid: str, body: OpportunityPatch) -> dict[str, Any]:
        data = _load()
        for opp in data["opportunities"]:
            if opp.get("id") != oid:
                continue
            patch = body.model_dump(exclude_none=True)
            if "kind" in patch:
                _validate_kind(patch["kind"])
            if "status" in patch:
                _validate_status(patch["status"], OPPORTUNITY_STATUSES)
            opp.update(patch)
            opp["updated_at"] = _cn_now()
            _save(data)
            return opp
        raise HTTPException(404, f"机会不存在: {oid}")

    @app.delete(
        "/api/hunter/opportunities/{oid}",
        dependencies=[Depends(require_auth)],
    )
    def delete_opportunity(oid: str) -> dict[str, bool]:
        data = _load()
        before = len(data["opportunities"])
        data["opportunities"] = [
            o for o in data["opportunities"] if o.get("id") != oid
        ]
        if len(data["opportunities"]) == before:
            raise HTTPException(404, f"机会不存在: {oid}")
        _save(data)
        return {"ok": True}

    # ---- 开仓记录 ----

    @app.post(
        "/api/hunter/shots",
        dependencies=[Depends(require_auth)],
    )
    def create_shot(body: ShotIn) -> dict[str, Any]:
        _validate_status(body.outcome, SHOT_OUTCOMES)
        data = _load()
        shot = body.model_dump()
        if not shot.get("at"):
            shot["at"] = _cn_now()
        shot["id"] = _new_id()
        data["shots"].insert(0, shot)  # 新的放最前
        _save(data)
        return shot

    @app.delete(
        "/api/hunter/shots/{sid}",
        dependencies=[Depends(require_auth)],
    )
    def delete_shot(sid: str) -> dict[str, bool]:
        data = _load()
        before = len(data["shots"])
        data["shots"] = [s for s in data["shots"] if s.get("id") != sid]
        if len(data["shots"]) == before:
            raise HTTPException(404, f"记录不存在: {sid}")
        _save(data)
        return {"ok": True}
