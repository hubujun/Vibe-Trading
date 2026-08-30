"""预测-实际归因分析 — 每笔调仓的收益来源分解.

对每笔 trade (from → to 区间):
  多头贡献   r_long  = seg[longs].mean(axis=1).sum()          (乘 long_mult)
  空头贡献   r_short = -seg[shorts].mean(axis=1).sum()        (乘 short_mult)
  资金费贡献  funding_net (trade 已记录)
  残差       = ret/100 - 已解释部分 (成本/滑点/近似误差)
因子归因: 重放 from 日期的各因子横截面得分, 与区间收益算截面 IC →
          回答"这轮赚/亏主要来自哪个因子、哪个币".

用法:
  python -m src.strategy.attribution --strategy <id>            # 最近一笔
  python -m src.strategy.attribution --strategy <id> --all      # 全部历史
  python -m src.strategy.attribution --strategy <id> --json     # 机器可读
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_signal import fetch_okx_daily, _row_zscore, SYMBOLS  # noqa: E402
from src.strategy.variant_backtester import (  # noqa: E402
    ACADEMIC_MODULES, load_factor_module, parse_signal_definition,
)
from src.strategy.macro_events import get_regime  # noqa: E402
from src.strategy.variant_backtester import SECTOR_CAP, _sector_cap  # noqa: E402

RUNTIME_ROOT = Path.home() / ".vibe-trading"
CACHE_PATH = RUNTIME_ROOT / "attribution_cache.json"


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _load_strategy(strategy_id: str) -> dict | None:
    raw = json.loads((RUNTIME_ROOT / "workbench" / "strategies.json").read_text(encoding="utf-8"))
    for s in raw.get("strategies", []):
        if s.get("strategy_id") == strategy_id:
            return s
    return None


def _load_state(strategy: dict) -> dict:
    st = Path(strategy["run_dir"]) / "state.json"
    return json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}


def _fetch_panel() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """拉 17 币 close/volume/high/low 面板 (与 daily_signal 一致)."""
    closes, volumes, highs, lows = {}, {}, {}, {}
    for s in SYMBOLS:
        df = fetch_okx_daily(s)
        if df.empty or len(df) < 300:
            continue
        closes[s] = df["close"]
        volumes[s] = df["volume"]
        highs[s] = df["high"]
        lows[s] = df["low"]
    close_df = pd.DataFrame(closes).ffill().dropna()
    volume_df = pd.DataFrame(volumes).reindex(close_df.index).ffill()
    high_df = pd.DataFrame(highs).reindex(close_df.index).ffill()
    low_df = pd.DataFrame(lows).reindex(close_df.index).ffill()
    return close_df, volume_df, high_df, low_df


def _factor_scores(close_df: pd.DataFrame, volume_df: pd.DataFrame,
                   high_df: pd.DataFrame, low_df: pd.DataFrame,
                   spec: dict) -> dict[str, pd.DataFrame]:
    """重放各因子 (非合成) 的横截面得分序列. 返回 {fid: DataFrame(每行=日期, 列=币)}."""
    out = {}
    for fid in spec["factors"]:
        mod = load_factor_module(fid)
        if mod is None:
            continue
        try:
            f = mod.compute({"close": close_df, "volume": volume_df,
                             "high": high_df, "low": low_df})
        except Exception:
            continue
        f = f.reindex(close_df.index)
        if fid not in ACADEMIC_MODULES:
            f = _row_zscore(f)
        out[fid] = f
    return out


def _approx_positions(score_row: pd.Series, regime: str,
                      top_n: int, bot_n: int) -> tuple[list[str], list[str]]:
    """旧 trades 无持仓快照时的近似重放 (band 无法复刻, 标记 approx)."""
    if regime == "risk_on":
        top_n_eff, bot_n_eff = top_n, max(1, bot_n - 1)
    elif regime == "risk_off":
        top_n_eff, bot_n_eff = max(1, top_n - 1), bot_n
    else:
        top_n_eff, bot_n_eff = top_n, bot_n
    ranked = score_row.dropna().sort_values(ascending=False)
    longs = _sector_cap(ranked.index.tolist(), top_n_eff)
    shorts = _sector_cap(ranked.index.tolist()[::-1], bot_n_eff)
    return longs, shorts


def attribute_trade(trade: dict, close_df: pd.DataFrame,
                    factor_scores: dict[str, pd.DataFrame],
                    spec: dict, scores_all: dict[str, pd.Series]) -> dict:
    """归因单笔 trade. scores_all: {fid: from日期横截面得分 Series}."""
    prev_day = pd.Timestamp(trade["from"]).date()
    last_day = pd.Timestamp(trade["to"]).date()
    has_snapshot = "longs" in trade
    longs = list(trade.get("longs") or [])
    shorts = list(trade.get("shorts") or [])
    approx = not has_snapshot
    if approx:
        # 旧 trades 无持仓快照: 用因子合成得分重放当时持仓 (band 无法复刻, 近似)
        composite = None
        total_w = 0.0
        for fid, srow in scores_all.items():
            w = spec["weights"].get(fid, 1.0 / len(spec["factors"]))
            composite = srow * w if composite is None else composite.add(srow * w, fill_value=0)
            total_w += w
        if composite is not None and total_w > 0:
            composite = composite / total_w
            longs, shorts = _approx_positions(composite, trade.get("regime", "neutral"),
                                              int(spec["top_n"]), int(spec["bot_n"]))

    rets = close_df.pct_change()
    seg = rets.loc[pd.Timestamp(prev_day) + pd.Timedelta(days=1): last_day]
    if len(seg) < 1 or not longs:
        return {"from": trade["from"], "to": trade["to"], "ret": trade.get("ret", 0.0),
                "approx": approx, "error": "区间无数据"}

    r_long = seg[longs].mean(axis=1).sum() if longs else 0.0
    r_short = -seg[shorts].mean(axis=1).sum() if shorts else 0.0
    long_mult = trade.get("long_mult", 1.0)
    short_mult = trade.get("short_mult", 1.0)
    explained = (r_long * long_mult + r_short * short_mult) / 2
    funding = trade.get("funding_net", 0.0) / 100.0
    residual = trade["ret"] / 100.0 - explained - funding

    # 因子归因: 每因子截面得分 vs 区间收益的 Spearman 相关
    seg_ret = seg.sum()  # 每币区间累计收益
    factor_ic = {}
    for fid, srow in scores_all.items():
        common = srow.dropna().index.intersection(seg_ret.dropna().index)
        if len(common) < 4:
            continue
        ic = srow[common].corr(seg_ret[common], method="spearman")
        factor_ic[fid] = round(float(ic), 4) if pd.notna(ic) else None

    # 币种贡献 TOP (多头正贡献/空头负贡献)
    coin_contrib = {}
    for c in longs:
        coin_contrib[c] = round(float(seg[c].sum() * long_mult / len(longs) / 2 * 100), 3)
    for c in shorts:
        coin_contrib[c] = round(float(-seg[c].sum() * short_mult / len(shorts) / 2 * 100), 3)

    return {
        "from": trade["from"], "to": trade["to"],
        "ret": trade["ret"], "approx": approx,
        "longs": longs, "shorts": shorts,
        "r_long_pct": round(r_long * long_mult / 2 * 100, 3),
        "r_short_pct": round(r_short * short_mult / 2 * 100, 3),
        "funding_pct": round(funding * 100, 3),
        "residual_pct": round(residual * 100, 3),
        "factor_ic": factor_ic,
        "coin_contrib": coin_contrib,
    }


def attribution_latest(strategy_id: str) -> dict | None:
    """review_daily 集成用: 最近一笔调仓归因 (缓存优先, 避免每日重拉行情)."""
    strategy = _load_strategy(strategy_id)
    if strategy is None:
        return None
    state = _load_state(strategy)
    trades = state.get("trades") or []
    if not trades:
        return None
    t = trades[-1]
    key = f"{strategy_id}|{t['from']}|{t['to']}"
    cache = _load_cache()
    if key in cache:
        return cache[key]

    spec = parse_signal_definition(strategy.get("signal_definition", ""))
    if spec is None:
        return None
    try:
        close_df, volume_df, high_df, low_df = _fetch_panel()
        factor_scores = _factor_scores(close_df, volume_df, high_df, low_df, spec)
        d = pd.Timestamp(t["from"]).date()
        scores_all = {}
        for fid, fs in factor_scores.items():
            row = fs.loc[fs.index.date == d]
            if len(row):
                scores_all[fid] = row.iloc[0]
        r = attribute_trade(t, close_df, factor_scores, spec, scores_all)
    except Exception:
        return None
    cache[key] = r
    _save_cache(cache)
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description="预测-实际归因分析")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--all", action="store_true", help="全部历史 trades")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    strategy = _load_strategy(args.strategy)
    if strategy is None:
        print(f"ERROR: 策略 {args.strategy} 不存在")
        return 1
    state = _load_state(strategy)
    trades = state.get("trades") or []
    if not trades:
        print(f"策略 {args.strategy}: 无调仓样本")
        return 0
    targets = trades if args.all else trades[-1:]

    spec = parse_signal_definition(strategy.get("signal_definition", ""))
    if spec is None:
        print("ERROR: signal_definition 无法解析")
        return 1

    close_df, volume_df, high_df, low_df = _fetch_panel()
    factor_scores = _factor_scores(close_df, volume_df, high_df, low_df, spec)

    # 预计算每个目标 trade from 日期的因子得分行
    results = []
    for t in targets:
        d = pd.Timestamp(t["from"]).date()
        scores_all = {}
        for fid, fs in factor_scores.items():
            row = fs.loc[fs.index.date == d]
            if len(row):
                scores_all[fid] = row.iloc[0]
        results.append(attribute_trade(t, close_df, factor_scores, spec, scores_all))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 0

    for r in results:
        print("═" * 46)
        print(f"调仓 {r['from']} → {r['to']}  收益 {r['ret']}%"
              + ("  (持仓≈重放)" if r.get("approx") else ""))
        if r.get("error"):
            print(" ", r["error"])
            continue
        print(f"  多头贡献  {r['r_long_pct']:+.3f}%   空头贡献 {r['r_short_pct']:+.3f}%")
        print(f"  资金费    {r['funding_pct']:+.3f}%   残差     {r['residual_pct']:+.3f}%")
        print(f"  多头: {', '.join(r['longs'])}")
        print(f"  空头: {', '.join(r['shorts'])}")
        ics = {k: v for k, v in r["factor_ic"].items() if v is not None}
        if ics:
            ranked = sorted(ics.items(), key=lambda x: -abs(x[1]))
            print("  因子IC (得分 vs 区间收益): " + "  ".join(
                f"{k}={v:+.2f}" for k, v in ranked))
        if r.get("coin_contrib"):
            top = sorted(r["coin_contrib"].items(), key=lambda x: -abs(x[1]))[:3]
            print("  主要贡献币: " + "  ".join(f"{c}={v:+.2f}%" for c, v in top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
