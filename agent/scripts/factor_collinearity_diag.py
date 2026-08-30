#!/usr/bin/env python3
"""因子共线性诊断 (2026-08-30) — 因子池冗余/信息重叠分析.

方法:
1. 对每个评估日, 计算因子横截面 Spearman 相关矩阵 (17 币截面)
2. 对时间平均 → 平均相关矩阵
3. 输出: 高相关对 (|corr|>=0.7) / 冗余簇 (层次聚类) / 与基座 (BAB/high52w) 的相关
4. 分前后半段看相关稳定性 (共线性是否时变)

用法: cd ~/Vibe-Trading/agent && ~/Vibe-Trading/.venv/bin/python scripts/factor_collinearity_diag.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # agent/

from src.strategy.factor_health import FACTORS  # noqa: E402
from src.strategy.variant_backtester import (  # noqa: E402
    ACADEMIC_MODULES, _row_zscore, fetch_panel, load_factor_module,
)

CORR_TH = 0.7   # 高相关阈值 (信息冗余)
CLUSTER_TH = 0.6  # 聚类阈值


def _factor_values(panel: dict[str, pd.DataFrame], fids: list[str]) -> dict[str, pd.DataFrame]:
    """加载并标准化全部因子值 (zoo 因子行 z-score; 学术因子已 z-score)."""
    close = panel["close"]
    out: dict[str, pd.DataFrame] = {}
    for fid in fids:
        mod = load_factor_module(fid)
        if mod is None:
            print(f"  ⚠️ {fid}: 模块缺失, 跳过")
            continue
        try:
            f = mod.compute(panel).reindex(close.index)
            if fid not in ACADEMIC_MODULES:
                f = _row_zscore(f)
            out[fid] = f
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ {fid}: compute 失败 ({str(exc)[:40]}), 跳过")
    return out


def _mean_corr_matrix(vals: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """逐日截面 Spearman 相关, 对时间平均."""
    fids = list(vals.keys())
    daily_corrs: list[pd.DataFrame] = []
    for dt in vals[fids[0]].index:
        frame = pd.DataFrame({fid: vals[fid].loc[dt] for fid in fids})
        frame = frame.dropna(axis=0, how="any")
        if len(frame) >= 5:
            c = frame.corr(method="spearman")
            if not c.isna().all().all():
                daily_corrs.append(c)
    if not daily_corrs:
        return pd.DataFrame(index=fids, columns=fids, dtype=float)
    return pd.concat(daily_corrs).groupby(level=0).mean()


def _cluster(fids: list[str], corr: pd.DataFrame, th: float) -> list[list[str]]:
    """贪心聚类: |相关| >= th 的因子归入同一簇 (传递闭包)."""
    groups: list[list[str]] = []
    for fid in fids:
        joined = False
        for g in groups:
            if any(abs(corr.loc[fid, g2]) >= th for g2 in g):
                g.append(fid)
                joined = True
                break
        if not joined:
            groups.append([fid])
    return groups


def main() -> int:
    print(f"评估因子数: {len(FACTORS)} (factor_health 清单)")
    print("拉面板中 (17币 800天, 约2-3分钟)...")
    panel = fetch_panel()
    if panel is None:
        print("ERROR: panel fetch failed")
        return 1

    vals = _factor_values(panel, FACTORS)
    if len(vals) < 3:
        print("ERROR: 可用因子不足")
        return 1
    print(f"可用因子: {len(vals)} 个")
    fids = list(vals.keys())

    corr = _mean_corr_matrix(vals)
    print(f"\n平均截面相关矩阵 ({len(fids)}x{len(fids)}):")
    print(corr.round(2).to_string())

    # 高相关对
    print(f"\n=== 高相关对 (|corr| >= {CORR_TH}) ===")
    pairs = []
    for i, a in enumerate(fids):
        for b in fids[i + 1:]:
            c = corr.loc[a, b]
            if abs(c) >= CORR_TH:
                pairs.append((abs(c), a, b, c))
    pairs.sort(reverse=True)
    if not pairs:
        print("  无 (因子间独立度高, 健康)")
    for ac, a, b, c in pairs:
        print(f"  {a:<32} × {b:<32} corr={c:+.3f}")

    # 与基座的相关
    print(f"\n=== 与基座因子相关 (BAB / high52w) ===")
    for base in ("BAB", "high52w"):
        if base not in fids:
            continue
        row = [(corr.loc[base, f], f) for f in fids if f != base]
        row.sort(reverse=True)
        for c, f in row[:5]:
            flag = " ← 冗余候选" if abs(c) >= 0.7 else ""
            print(f"  {base:<10} × {f:<30} {c:+.3f}{flag}")

    # 冗余簇
    print(f"\n=== 冗余簇 (|corr| >= {CLUSTER_TH}, 同一簇 = 信息高度重叠) ===")
    for g in _cluster(fids, corr, CLUSTER_TH):
        if len(g) > 1:
            print("  簇:", ", ".join(g))

    # 分前后半段看相关稳定性 (共线性是否时变)
    print(f"\n=== 前后半段相关稳定性 (高相关对是否跨期稳定) ===")
    n = len(corr)
    half = n // 2
    close = panel["close"]
    daily_front: list[pd.DataFrame] = []
    daily_back: list[pd.DataFrame] = []
    for dt in close.index[:half]:
        frame = pd.DataFrame({fid: vals[fid].loc[dt] for fid in fids}).dropna(how="any")
        if len(frame) >= 5:
            daily_front.append(frame.corr(method="spearman"))
    for dt in close.index[half:]:
        frame = pd.DataFrame({fid: vals[fid].loc[dt] for fid in fids}).dropna(how="any")
        if len(frame) >= 5:
            daily_back.append(frame.corr(method="spearman"))
    corr_f = pd.concat(daily_front).groupby(level=0).mean() if daily_front else corr
    corr_b = pd.concat(daily_back).groupby(level=0).mean() if daily_back else corr
    for ac, a, b, c in pairs:
        cf = corr_f.loc[a, b] if a in corr_f.index and b in corr_f.columns else float("nan")
        cb = corr_b.loc[a, b] if a in corr_b.index and b in corr_b.columns else float("nan")
        stable = "稳定" if abs(cf - cb) < 0.2 else "时变!"
        print(f"  {a[:24]:<26} × {b[:24]:<26} 全期={c:+.3f} 前={cf:+.3f} 后={cb:+.3f} [{stable}]")

    # 汇总
    redundant = {f for _, a, b, _ in pairs for f in (a, b)}
    print(f"\n=== 汇总 ===")
    print(f"  因子总数 {len(fids)} | 高相关对 {len(pairs)} | 涉及因子 {len(redundant)} 个")
    print(f"  冗余簇 {len([g for g in _cluster(fids, corr, CLUSTER_TH) if len(g) > 1])} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
