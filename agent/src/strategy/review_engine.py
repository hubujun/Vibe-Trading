"""策略复盘引擎 — Loop Engineering 第一圈闭环: 复盘 → 研究反馈.

把 paper_combo 模拟盘的实际表现自动回流到研究层:

- 体检: 模拟盘 vs 回测 (年化/回撤), 信号新鲜度, 回测数据新鲜度
- 假设自动流转 (写回 hypotheses.json, 复用 HypothesisRegistry 保证
  状态词表校验 + 原子写):
    - status == "testing" 且连亏 >= 3 笔 → rejected (与风控熔断一致)
    - status == "testing" 且样本足 + 跑赢回测 → validated
    - status == "validated" 且回撤超限 → 降级 monitoring (附原因)
- 推荐动作: 规则生成的下一步建议, 驱动用户把策略推回研究阶段

设计约束:
- 幂等: 每次运行对同一状态只流转一次 (status 变化后不再匹配规则)
- fail-open: 任何输入缺失/损坏 → 对应体检项降级为 "数据不足", 不抛异常
- 写回失败仅记录, 不影响体检结果返回
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.hypotheses.registry import HypothesisRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "StrategyReview",
    "compute_review",
]

#: 模拟盘样本门槛 — 少于该调仓次数不下 vs 回测结论
MIN_TRADES = 20
#: 连亏阈值 — 与用户风控规则一致 (连续 3 笔亏损当日停交易)
CONSECUTIVE_LOSSES = 3
#: 回撤超限倍数 — 模拟盘回撤超过回测最大回撤的该倍数 → 标红
DD_BREACH_MULTIPLIER = 1.5
#: 信号过期天数
SIGNAL_STALE_DAYS = 2
#: 回测指标过期天数
METRICS_STALE_DAYS = 30
#: 参数自适应 — 杠杆乘子区间与步长 (第三圈)
EXPOSURE_MIN = 0.25
EXPOSURE_MAX = 1.0
EXPOSURE_STEP = 0.5  # 降杠杆步长
EXPOSURE_RECOVER_STEP = 0.1  # 恢复步长


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str) -> datetime | None:
    """解析 ISO 时间戳, 兼容 naive/aware 与 date-only; 失败返回 None."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


# ============================================================================
# 输出模型 (dataclass → dict, 与 FastAPI/Pydantic 解耦便于单测)
# ============================================================================


@dataclass
class ReviewVsBacktest:
    paper_nav: float | None = None
    paper_annual: float | None = None
    backtest_annual: float | None = None
    backtest_max_dd: float | None = None
    current_dd: float | None = None
    dd_breach: bool = False
    outperforming: bool | None = None
    sample_sufficient: bool = False
    paper_trades: int = 0
    consecutive_losses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ReviewSignalHealth:
    last_signal_date: str | None = None
    signal_age_days: float | None = None
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ReviewDataFreshness:
    metrics_updated_at: str | None = None
    metrics_age_days: float | None = None
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ReviewHypothesisUpdate:
    hypothesis_id: str
    title: str
    from_status: str
    to_status: str
    reason: str
    at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ReviewRecommendation:
    level: str  # info | warn | critical
    text: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ReviewAdaptation:
    """一条参数自适应变更 (第三圈: review 输出 → 策略参数)."""

    param: str
    from_value: float
    to_value: float
    reason: str
    at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class StrategyReview:
    vs_backtest: ReviewVsBacktest = field(default_factory=ReviewVsBacktest)
    signal_health: ReviewSignalHealth = field(default_factory=ReviewSignalHealth)
    data_freshness: ReviewDataFreshness = field(default_factory=ReviewDataFreshness)
    hypothesis_updates: list[ReviewHypothesisUpdate] = field(default_factory=list)
    adaptations: list[ReviewAdaptation] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)
    variant_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    recommendations: list[ReviewRecommendation] = field(default_factory=list)
    #: 下一圈去向: compose(回组合迭代) / research(回研究回炉)
    loop_next: str = "compose"
    reviewed_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vs_backtest": self.vs_backtest.to_dict(),
            "signal_health": self.signal_health.to_dict(),
            "data_freshness": self.data_freshness.to_dict(),
            "hypothesis_updates": [u.to_dict() for u in self.hypothesis_updates],
            "adaptations": [a.to_dict() for a in self.adaptations],
            "variants": list(self.variants),
            "variant_metrics": dict(self.variant_metrics),
            "recommendations": [r.to_dict() for r in self.recommendations],
            "loop_next": self.loop_next,
            "reviewed_at": self.reviewed_at,
        }


# ============================================================================
# 计算: 净值/回撤/年化 (从 trades 反推, 与前端逻辑一致)
# ============================================================================


def _reconstruct_nav(trades: list[dict[str, Any]], final_nav: float) -> list[float]:
    """从最近一笔往前反推净值序列 (trades 按时间正序, 每笔有 ret%)."""
    navs: list[float] = []
    nav = float(final_nav or 1.0)
    navs.append(nav)
    for t in reversed(trades):
        ret = float(t.get("ret", 0))
        nav = nav / (1 + ret / 100)
        navs.append(nav)
    return list(reversed(navs))


def _max_drawdown(navs: list[float]) -> float:
    """返回最大回撤 (正数, 如 0.12 = 回撤 12%)."""
    peak = navs[0]
    worst = 0.0
    for v in navs:
        if v > peak:
            peak = v
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def _annualized(nav: float, days: float) -> float | None:
    if nav <= 0 or days <= 0:
        return None
    return nav ** (365.0 / days) - 1.0


# ============================================================================
# 主入口
# ============================================================================


def compute_review(
    combo_state_path: Path,
    metrics_path: Path,
    hypotheses_path: Path | None = None,
) -> StrategyReview:
    """对 combo 策略跑一轮复盘, 返回体检 + 假设流转 + 推荐动作.

    Args:
        combo_state_path: paper_combo/state.json
        metrics_path: paper_combo/backtest_metrics.json
        hypotheses_path: hypotheses.json (None → 跳过假设流转)
    """
    review = StrategyReview()
    state = _read_json(combo_state_path)
    metrics = _read_json(metrics_path)
    today = datetime.now(timezone.utc)

    # --- vs 回测体检 ---
    trades = state.get("trades", []) or []
    nav = state.get("nav")
    started_at = state.get("started_at")
    bt = metrics.get("backtest", {}) or {}
    combo2 = bt.get("COMBO2(BAB+52w)", {}) or {}

    vs = review.vs_backtest
    vs.paper_trades = len(trades)
    vs.sample_sufficient = len(trades) >= MIN_TRADES
    vs.consecutive_losses = _consecutive_losses(trades)

    if nav is not None:
        vs.paper_nav = float(nav)
        if started_at:
            start_dt = _parse_dt(str(started_at))
            if start_dt is not None:
                days = max((today - start_dt).days, 1)
                vs.paper_annual = _annualized(float(nav), days)
        navs = _reconstruct_nav(trades, float(nav)) if trades else []
        if navs:
            vs.current_dd = round(_max_drawdown(navs) * 100, 2)

    if combo2:
        vs.backtest_annual = combo2.get("annual")
        vs.backtest_max_dd = combo2.get("max_dd")
        if vs.paper_annual is not None and vs.backtest_annual is not None and vs.sample_sufficient:
            vs.outperforming = vs.paper_annual > vs.backtest_annual
        if vs.current_dd is not None and vs.backtest_max_dd is not None:
            # backtest_max_dd 存负值 (-10.62), current_dd 为正 (20.0) — 按绝对值比较
            vs.dd_breach = abs(vs.current_dd) > abs(vs.backtest_max_dd) * DD_BREACH_MULTIPLIER

    # --- 信号新鲜度 ---
    last_signal = state.get("last_signal_date")
    if last_signal:
        sig_dt = _parse_dt(str(last_signal))
        if sig_dt is not None:
            age_days = (today - sig_dt).total_seconds() / 86400
            review.signal_health.last_signal_date = str(last_signal)
            review.signal_health.signal_age_days = round(age_days, 1)
            review.signal_health.stale = age_days > SIGNAL_STALE_DAYS

    # --- 回测数据新鲜度 ---
    metrics_ts = metrics.get("updated_at")
    if metrics_ts:
        m_dt = _parse_dt(str(metrics_ts))
        if m_dt is not None:
            age_days = (today - m_dt).total_seconds() / 86400
            review.data_freshness.metrics_updated_at = str(metrics_ts)
            review.data_freshness.metrics_age_days = round(age_days, 1)
            review.data_freshness.stale = age_days > METRICS_STALE_DAYS

    # --- 假设自动流转 ---
    if hypotheses_path is not None:
        try:
            registry = HypothesisRegistry(hypotheses_path)
            for hyp in registry.list():
                update = _apply_hypothesis_rule(hyp, trades, vs)
                if update is not None:
                    _persist_hypothesis_update(registry, hyp, update, review)
        except Exception:  # noqa: BLE001 — 假设流转失败不拖垮体检
            logger.warning("review: hypothesis transition failed", exc_info=True)

    # --- 推荐动作 (规则) ---
    recs = review.recommendations
    if not vs.sample_sufficient:
        recs.append(
            ReviewRecommendation(
                level="info",
                text=f"模拟盘样本不足 ({vs.paper_trades}/{MIN_TRADES} 次调仓)，继续积累数据再下结论",
            )
        )
    elif vs.outperforming is True:
        recs.append(
            ReviewRecommendation(
                level="info",
                text=f"模拟盘年化 {vs.paper_annual:.1%} 跑赢回测 {vs.backtest_annual:.1%}，可评估上线执行",
            )
        )
    elif vs.outperforming is False:
        recs.append(
            ReviewRecommendation(
                level="warn",
                text=f"模拟盘年化 {vs.paper_annual:.1%} 落后回测 {vs.backtest_annual:.1%}，建议回到研究阶段检查参数",
            )
        )
    if vs.dd_breach:
        review.loop_next = "research"
        recs.append(
            ReviewRecommendation(
                level="critical",
                text=f"模拟盘回撤 {vs.current_dd}% 已超过回测最大回撤 {vs.backtest_max_dd}% 的 {DD_BREACH_MULTIPLIER} 倍，建议暂停并回研究",
            )
        )
    if vs.consecutive_losses >= CONSECUTIVE_LOSSES:
        review.loop_next = "research"
    if review.signal_health.stale:
        recs.append(
            ReviewRecommendation(
                level="warn",
                text=f"信号已 {review.signal_health.signal_age_days} 天未更新（> {SIGNAL_STALE_DAYS} 天），检查每日 07:00 cron 是否在跑",
            )
        )
    if review.data_freshness.stale:
        recs.append(
            ReviewRecommendation(
                level="warn",
                text=f"回测指标已 {review.data_freshness.metrics_age_days} 天未更新（> {METRICS_STALE_DAYS} 天），建议重跑 combo_backtest.py",
            )
        )
    if not recs:
        recs.append(
            ReviewRecommendation(level="info", text="各项体检正常，继续按当前节奏运行")
        )
    return review


def _consecutive_losses(trades: list[dict[str, Any]]) -> int:
    """从最近一笔往前数连续亏损笔数."""
    streak = 0
    for t in reversed(trades):
        if float(t.get("ret", 0)) < 0:
            streak += 1
        else:
            break
    return streak


def _apply_hypothesis_rule(
    hyp: Any,
    trades: list[dict[str, Any]],
    vs: ReviewVsBacktest,
) -> ReviewHypothesisUpdate | None:
    """对单条假设应用流转规则; 不匹配返回 None."""
    status = str(hyp.status)
    title = str(hyp.title)
    hid = str(hyp.hypothesis_id)

    # testing + 连亏 >= 3 → rejected
    if status == "testing" and trades:
        streak = _consecutive_losses(trades)
        if streak >= CONSECUTIVE_LOSSES:
            return ReviewHypothesisUpdate(
                hypothesis_id=hid,
                title=title,
                from_status=status,
                to_status="rejected",
                reason=f"模拟盘连续 {streak} 笔亏损 (≥{CONSECUTIVE_LOSSES}), 触发风控规则",
            )
    # testing + 样本足 + 跑赢 → validated
    if status == "testing" and vs.sample_sufficient and vs.outperforming is True:
        return ReviewHypothesisUpdate(
            hypothesis_id=hid,
            title=title,
            from_status=status,
            to_status="validated",
            reason=f"模拟盘样本 {vs.paper_trades} 笔, 年化 {vs.paper_annual:.1%} 跑赢回测 {vs.backtest_annual:.1%}",
        )
    # validated + 回撤超限 → monitoring
    if status == "validated" and vs.dd_breach:
        return ReviewHypothesisUpdate(
            hypothesis_id=hid,
            title=title,
            from_status=status,
            to_status="monitoring",
            reason=f"模拟盘回撤 {vs.current_dd}% 超回测最大回撤 {vs.backtest_max_dd}% 的 {DD_BREACH_MULTIPLIER} 倍, 降级观察",
        )
    return None


def _persist_hypothesis_update(
    registry: HypothesisRegistry,
    hyp: Any,
    update: ReviewHypothesisUpdate,
    review: StrategyReview,
) -> None:
    """写回状态 + 追加 invalidation_notes; 失败仅记录."""
    try:
        stamp = update.at
        note = f"{stamp}: {update.reason}"
        prev_notes = str(getattr(hyp, "invalidation_notes", "") or "")
        combined = f"{prev_notes}\n{note}" if prev_notes else note
        registry.update(
            update.hypothesis_id,
            status=update.to_status,
            invalidation_notes=combined,
        )
        review.hypothesis_updates.append(update)
        logger.info(
            "review: hypothesis %s %s → %s (%s)",
            update.hypothesis_id,
            update.from_status,
            update.to_status,
            update.reason,
        )
    except Exception:  # noqa: BLE001
        logger.warning("review: persist hypothesis update failed for %s", update.hypothesis_id)


# ============================================================================
# 第三圈: 参数自适应 (复盘体检 → 策略参数)
# ============================================================================


def compute_adaptations(
    review: StrategyReview,
    current_params: dict[str, Any] | None = None,
) -> list[ReviewAdaptation]:
    """根据复盘体检计算参数自适应变更 (仅计算, 由调用方应用持久化).

    规则 (与用户风控偏好一致):
    - 回撤超限 (dd_breach)        → exposure_multiplier *= 0.5 (下限 0.25)
    - 连续亏损 >= 3 笔            → exposure_multiplier *= 0.5 (下限 0.25)
    - 样本足且跑赢回测            → exposure_multiplier += 0.1 (上限 1.0)

    Args:
        review: 复盘引擎输出.
        current_params: 策略当前参数 (含 exposure_multiplier), 缺省取默认值.

    Returns:
        需要应用的参数变更列表 (空 = 无需调整).
    """
    current = float((current_params or {}).get("exposure_multiplier", 1.0))
    vs = review.vs_backtest
    adaptations: list[ReviewAdaptation] = []

    def _step_down(reason: str) -> None:
        nonlocal current
        target = round(max(current * EXPOSURE_STEP, EXPOSURE_MIN), 2)
        if target < current:
            adaptations.append(
                ReviewAdaptation(
                    param="exposure_multiplier",
                    from_value=current,
                    to_value=target,
                    reason=reason,
                )
            )
            current = target

    def _step_up(reason: str) -> None:
        nonlocal current
        target = round(min(current + EXPOSURE_RECOVER_STEP, EXPOSURE_MAX), 2)
        if target > current:
            adaptations.append(
                ReviewAdaptation(
                    param="exposure_multiplier",
                    from_value=current,
                    to_value=target,
                    reason=reason,
                )
            )
            current = target

    if vs.dd_breach:
        _step_down(
            f"回撤 {vs.current_dd}% 超回测最大回撤 {vs.backtest_max_dd}% 的 "
            f"{DD_BREACH_MULTIPLIER} 倍 → 自动降杠杆"
        )
    if vs.consecutive_losses >= CONSECUTIVE_LOSSES:
        _step_down(f"连续 {vs.consecutive_losses} 笔亏损 (≥{CONSECUTIVE_LOSSES}) → 自动降杠杆")
    if vs.sample_sufficient and vs.outperforming is True:
        _step_up(f"样本 {vs.paper_trades} 笔跑赢回测 → 逐步恢复杠杆")

    return adaptations
