"""策略变体生成器 — Loop Engineering 第二圈: 已通过假设 → 下一代实验候选.

当基策略假设进入 validated/monitoring 后, 自动生成下一代变体
(权重/因子/阈值维度) 并以 exploring 状态写入假设注册表, 让"研究卡"
直接看到下一圈实验, 形成螺旋上升.

规则式生成 (确定性、可测、无外部依赖):
    - 权重变体: BAB:high52w = 0.3:0.7 / 0.7:0.3 (基 0.5:0.5)
    - 因子变体: 三因子等权 BAB+RMW+high52w
    - 阈值变体: top2/bottom2 (更聚焦) / top4/bottom4 (更分散)

去重: 注册表中已存在相同 signal_definition 的变体不重复创建.
限流: 每轮最多 max_new 个, 防止注册表膨胀.
"""

from __future__ import annotations

import logging
from typing import Any

from src.hypotheses.registry import HypothesisRegistry

logger = logging.getLogger(__name__)

__all__ = ["BASE_VARIANTS", "variant_signal_definition", "generate_variants"]

#: 变体模板 (确定性顺序). weights/top_n 为与基策略的差异维度.
BASE_VARIANTS: list[dict[str, Any]] = [
    {
        "name": "BAB 权重 0.3 / high52w 0.7",
        "weights": {"BAB": 0.3, "high52w": 0.7},
    },
    {
        "name": "BAB 权重 0.7 / high52w 0.3",
        "weights": {"BAB": 0.7, "high52w": 0.3},
    },
    {
        "name": "三因子等权 (BAB + RMW + high52w)",
        "weights": {"BAB": 1 / 3, "RMW": 1 / 3, "high52w": 1 / 3},
    },
    {
        "name": "top2/bottom2 更聚焦",
        "top_n": 2,
    },
    {
        "name": "top4/bottom4 更分散",
        "top_n": 4,
    },
]

#: 因子级变体候选池 — 从 crypto_mined zoo (factor miner 挖掘产出) 挑选的
#: 可加入组合的因子. 生成时校验 zoo 实际存在, 避免引用无效因子.
FACTOR_POOL: list[str] = [
    "volume_surge_reversal",
    "microstructure_vol_reversal",
    "volume_flow_momentum",
    "volume_confirmed_momentum",
    "open_volume_reversal",
]

#: 学术因子池 — academic zoo 除组合基座 (BAB/high52w) 外的可探索因子
ACADEMIC_POOL: list[str] = [
    "carhart_mom",   # 动量 (Carhart UMD)
    "strev",         # 短期反转
    "illiq",         # 非流动性 (负 IC, 反向)
    "smb",           # 小市值 (负 IC, 反向)
    "hml",           # 价值
    "cma",           # 投资
    "retskew",       # 收益偏度
    "mkt_rf",        # 市场因子
]

#: 基策略定义 — 当前 fork 主策略 (与 combo_backtest/daily_signal 一致).
BASE_STRATEGY = {
    "name": "BAB+high52w 双因子组合",
    "weights": {"BAB": 0.5, "high52w": 0.5},
    "top_n": 3,
    "bot_n": 3,
}

#: 触发生成所需的状态 (基策略至少进入这些状态之一).
TRIGGER_STATUSES = {"validated", "monitoring"}


def variant_signal_definition(variant: dict[str, Any]) -> str:
    """把变体序列化为可解析的 signal_definition (供 daily_signal 消费)."""
    weights = variant.get("weights", BASE_STRATEGY["weights"])
    top_n = variant.get("top_n", BASE_STRATEGY["top_n"])
    bot_n = variant.get("bot_n", BASE_STRATEGY["bot_n"])
    w_str = ",".join(f"{k}:{round(float(v), 2)}" for k, v in weights.items())
    factors = variant.get("factors")
    if factors:
        f_str = ",".join(str(f) for f in factors)
        return f"combo_variant: factors=[{f_str}] weights={{{w_str}}} top_n={top_n} bot_n={bot_n}"
    return f"combo_variant: weights={{{w_str}}} top_n={top_n} bot_n={bot_n}"


def _zoo_factor_ids() -> set[str]:
    """当前 crypto_mined zoo 实际存在的因子 id (fail-open 空集)."""
    try:
        from src.crypto_autopilot.factor_store import FactorStore

        return {str(f.get("alpha_id")) for f in FactorStore().list_factors_with_meta()}
    except Exception:  # noqa: BLE001
        return set()


def _retired_factor_ids() -> set[str]:
    """autopilot 已退役 (过拟合三关拒绝) 的因子 id 集合.

    退役记录的 alpha_id 带 ``crypto_mined_`` 前缀, 与 zoo 文件名对齐后比较.
    """
    import json
    from pathlib import Path

    try:
        raw = json.loads(
            (Path.home() / ".vibe-trading" / "runs" / "autopilot" / "factors.json").read_text(
                encoding="utf-8"
            )
        )
        retired = {
            str(r.get("alpha_id", "")).removeprefix("crypto_mined_")
            for r in raw.get("retired", [])
        }
        return {r for r in retired if r}
    except (OSError, ValueError, TypeError):
        return set()


def _factor_level_variants() -> list[dict[str, Any]]:
    """从因子池生成因子级变体 (三因子: BAB + high52w + X).

    - 学术池 (ACADEMIC_POOL): 文献因子, 代码库固定资产, 恒可用
    - 挖掘池: FACTOR_POOL 固定 5 个优先 + zoo 全部未退役候选自动补充
      (矿机每挖出/复活一个新因子, 这里自动出现新候选 — 挖掘 → 组合 通路)
    """
    zoo_ids = _zoo_factor_ids()
    retired_ids = _retired_factor_ids()
    variants: list[dict[str, Any]] = []
    for fid in ACADEMIC_POOL:
        variants.append(
            {
                "name": f"加入学术因子 {fid}",
                "factors": ["BAB", "high52w", fid],
                "weights": {"BAB": 1 / 3, "high52w": 1 / 3, fid: 1 / 3},
            }
        )
    mined_pool = list(FACTOR_POOL) + sorted(zoo_ids - set(FACTOR_POOL))
    for fid in mined_pool:
        if fid in zoo_ids and fid not in retired_ids:
            variants.append(
                {
                    "name": f"加入挖掘因子 {fid}",
                    "factors": ["BAB", "high52w", fid],
                    "weights": {"BAB": 1 / 3, "high52w": 1 / 3, fid: 1 / 3},
                }
            )
    return variants


def _all_variant_templates() -> list[dict[str, Any]]:
    """基础参数变体 + 因子级变体 (因子级在后, 参数级优先)."""
    return BASE_VARIANTS + _factor_level_variants()


def generate_variants(
    registry: HypothesisRegistry,
    *,
    max_new: int = 2,
) -> list[dict[str, Any]]:
    """为已通过(validated/monitoring)的基策略生成下一代变体.

    Args:
        registry: 假设注册表 (读写 hypotheses.json).
        max_new: 本轮最多创建的变体数量.

    Returns:
        本轮新建的变体记录 (dict 列表), 供 review 反馈展示.
    """
    existing = registry.list()
    # 触发条件: 基策略存在且已通过
    base_alive = any(
        str(h.status) in TRIGGER_STATUSES
        and variant_signal_definition({}) not in _signal_defs(existing)
        for h in existing
        if BASE_STRATEGY["name"] in str(h.title)
    )
    if not base_alive:
        return []

    existing_defs = _signal_defs(existing)
    created: list[dict[str, Any]] = []
    for variant in _all_variant_templates():
        if len(created) >= max_new:
            break
        sig_def = variant_signal_definition(variant)
        if sig_def in existing_defs:
            continue
        try:
            hyp = registry.create(
                title=f"{BASE_STRATEGY['name']} · {variant['name']}",
                thesis=(
                    f"基策略 {BASE_STRATEGY['name']} 已验证, 迭代变体: "
                    f"{variant['name']}. signal={sig_def}"
                ),
                status="exploring",
                universe="crypto",
                signal_definition=sig_def,
                data_sources=["okx_candles_1d"],
                skills=["crypto", "alpha-bench"],
            )
        except Exception:  # noqa: BLE001 — 单个变体失败不阻断后续
            logger.warning("variant generator: create failed for %s", variant["name"], exc_info=True)
            continue
        existing_defs.add(sig_def)
        created.append(
            {
                "hypothesis_id": hyp.hypothesis_id,
                "title": hyp.title,
                "signal_definition": sig_def,
                "status": hyp.status,
                "variant_name": variant["name"],
            }
        )
        logger.info("variant generator: created %s (%s)", hyp.hypothesis_id, sig_def)
    return created


def _signal_defs(existing: list[Any]) -> set[str]:
    return {str(getattr(h, "signal_definition", "")).strip() for h in existing if getattr(h, "signal_definition", "")}
