#!/usr/bin/env python3
"""策略复盘日报 — Loop Engineering 每日闭环 cron.

流程: 复盘引擎 (体检+假设流转+参数自适应) → 变体生成 → 日报文本 → 飞书推送.

用法: cd /Users/laohu/Vibe-Trading/agent && ../.venv/bin/python review_daily.py
凭证: ~/.hermes/.env 的 FEISHU_APP_ID / FEISHU_APP_SECRET
推送目标: 老胡 open_id (ou_2111554e56d40c5cce1ebab5188bf57b)
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/Users/laohu/Vibe-Trading/agent")

from src.hypotheses.registry import HypothesisRegistry
from src.strategy.review_engine import compute_review
from src.strategy.variant_generator import generate_variants

HOME = Path.home()
COMBO_STATE = HOME / ".vibe-trading" / "runs" / "paper_combo" / "state.json"
COMBO_METRICS = HOME / ".vibe-trading" / "runs" / "paper_combo" / "backtest_metrics.json"
HYPOTHESES = HOME / ".vibe-trading" / "hypotheses.json"
WORKBENCH = HOME / ".vibe-trading" / "workbench" / "strategies.json"
OPEN_ID = "ou_2111554e56d40c5cce1ebab5188bf57b"
ENV_FILE = Path.home() / ".hermes" / ".env"
PROXY = "http://127.0.0.1:7890"  # ClashX


def _load_env(name: str) -> str:
    """从 ~/.hermes/.env 读取凭证 (仅取值, 不打印)."""
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _fmt_pct(v) -> str:
    if v is None:
        return "--"
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "--"


def build_report() -> str:
    review = compute_review(COMBO_STATE, COMBO_METRICS, HYPOTHESES)
    variants = generate_variants(HypothesisRegistry(HYPOTHESES), max_new=2)
    vs = review.vs_backtest
    sig = review.signal_health
    data = review.data_freshness

    # 当前杠杆乘子
    multiplier = 1.0
    try:
        raw = json.loads(WORKBENCH.read_text(encoding="utf-8"))
        for s in raw.get("strategies", []):
            if s.get("strategy_id") == "combo_bab_52w":
                multiplier = float(s.get("params", {}).get("exposure_multiplier", 1.0))
                break
    except (OSError, ValueError, TypeError):
        pass

    lines: list[str] = []
    lines.append(f"📊 策略复盘日报 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    lines.append("策略: BAB+high52w 双因子组合")
    lines.append("---")
    lines.append(
        f"📈 模拟盘: NAV {vs.paper_nav if vs.paper_nav is not None else '--'} "
        f"| 调仓 {vs.paper_trades} 次 | 连亏 {vs.consecutive_losses} 笔"
    )
    lines.append(
        f"🎯 回测: 年化 {_fmt_pct(vs.backtest_annual)} | 最大回撤 {vs.backtest_max_dd}%"
    )
    dd = "⚠️超限" if vs.dd_breach else "正常"
    out = "跑赢" if vs.outperforming is True else ("跑输" if vs.outperforming is False else "样本不足")
    sig_s = "⚠️过期" if sig.stale else "正常"
    data_s = "⚠️过期" if data.stale else "正常"
    lines.append(f"🩺 体检: vs回测 {out} | 回撤 {dd} | 信号 {sig_s} | 数据 {data_s}")
    lines.append(f"⚙️ 杠杆乘子: {multiplier:.2f}")
    lines.append("---")
    if review.adaptations:
        lines.append("🔄 参数自适应:")
        for a in review.adaptations:
            lines.append(f"  • {a.param} {a.from_value:.2f} → {a.to_value:.2f} ({a.reason})")
    lines.append("💡 建议:")
    for r in review.recommendations:
        icon = {"critical": "⛔", "warn": "⚠️", "info": "ℹ️"}.get(r.level, "•")
        lines.append(f"  {icon} {r.text}")
    if review.hypothesis_updates:
        lines.append("📋 假设流转:")
        for u in review.hypothesis_updates:
            lines.append(f"  • {u.title[:24]}… {u.from_status} → {u.to_status}")
    if variants:
        lines.append("🧬 新变体候选:")
        for v in variants:
            lines.append(f"  • {v['title'][:30]} (exploring)")
    lines.append("---")
    # 最近调仓归因 (缓存优先, 失败静默不影响主报告)
    try:
        from src.strategy.attribution import attribution_latest
        attr = attribution_latest("combo_bab_52w")
        if attr and not attr.get("error"):
            lines.append("🔬 最近调仓归因:")
            lines.append(f"  {attr['from']}→{attr['to']} 收益 {attr['ret']}%"
                         + (" (持仓≈重放)" if attr.get("approx") else ""))
            lines.append(f"  多头 {attr['r_long_pct']:+.3f}% | 空头 {attr['r_short_pct']:+.3f}%"
                         f" | 资金费 {attr['funding_pct']:+.3f}% | 残差 {attr['residual_pct']:+.3f}%")
            ics = {k: v for k, v in (attr.get("factor_ic") or {}).items() if v is not None}
            if ics:
                ranked = sorted(ics.items(), key=lambda x: -abs(x[1]))
                lines.append("  因子IC: " + "  ".join(f"{k}={v:+.2f}" for k, v in ranked))
            if attr.get("coin_contrib"):
                top = sorted(attr["coin_contrib"].items(), key=lambda x: -abs(x[1]))[:3]
                lines.append("  贡献币: " + "  ".join(f"{c}={v:+.2f}%" for c, v in top))
    except Exception:
        pass
    lines.append("来源: Vibe-Trading 策略流水线工作台 /workbench")
    return "\n".join(lines)


def send_feishu(text: str) -> bool:
    """纯 Python 推飞书 (走 ClashX 代理), 返回是否成功."""
    app_id = _load_env("FEISHU_APP_ID")
    app_secret = _load_env("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        print("ERROR: FEISHU_APP_ID/SECRET 未配置", file=sys.stderr)
        return False

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
    )

    # 1. token
    token_req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        token_resp = json.loads(opener.open(token_req, timeout=15).read().decode())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: 飞书 token 获取失败: {exc}", file=sys.stderr)
        return False
    token = token_resp.get("tenant_access_token", "")
    if not token:
        print(f"ERROR: 飞书 token 为空: {token_resp}", file=sys.stderr)
        return False

    # 2. 发消息 (content 必须是 JSON 字符串!)
    msg_body = json.dumps(
        {
            "receive_id": OPEN_ID,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
    ).encode()
    msg_req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        data=msg_body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        result = json.loads(opener.open(msg_req, timeout=15).read().decode())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: 飞书发送失败: {exc}", file=sys.stderr)
        return False
    if result.get("code") == 0:
        print("飞书推送成功")
        return True
    print(f"ERROR: 飞书返回 {result}", file=sys.stderr)
    return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="策略复盘日报 (Loop 闭环 cron)")
    parser.add_argument("--no-send", action="store_true", help="只生成日报, 不推送飞书")
    args = parser.parse_args()

    report = build_report()
    print(report)
    print("=" * 40)
    if args.no_send:
        print("dry-run: 未推送飞书")
        sys.exit(0)
    ok = send_feishu(report)
    sys.exit(0 if ok else 1)
