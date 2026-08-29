"""LLM 因子挖掘 — deepseek 云端生成横截面因子, 自动入 zoo + 自动进变体池.

流程:
1. 读现有挖掘因子示例 (2 个) 作为风格参考
2. 调 deepseek API 生成新因子 (__alpha_meta__ + compute 函数)
3. 安全校验: import 白名单 + 危险操作禁止 (生成代码不可信!)
4. 写入 src/factors/zoo/crypto_mined/llm_<hash>.py (zoo 自动发现, 无需注册)
5. 加载验证 + 假 panel 冒烟测试
6. 写入 hypotheses.json 作为 exploring 变体 (BAB+high52w+新因子)
   → 08:45 自动回测评估接管 (跑赢基策略自动晋升/播种)

用法:
  python -m src.strategy.factor_miner [--count 2] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[2]  # agent/
sys.path.insert(0, str(AGENT_ROOT))

ZOO_DIR = AGENT_ROOT / "src" / "factors" / "zoo" / "crypto_mined"
ENV_PATH = Path.home() / ".hermes" / ".env"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"

# 安全白名单: 生成代码只允许这些 import (startswith 匹配)
ALLOWED_IMPORTS = {"import pandas", "import numpy", "from src.factors.base import",
                   "from __future__ import annotations"}
FORBIDDEN = ["os.", "sys.", "subprocess", "exec(", "eval(", "__import__",
             "open(", "requests", "urllib", "socket", "shutil", "pathlib",
             "import os", "import sys", "pickle", "tempfile", "ctypes", "multiprocessing"]


def _load_api_key() -> str:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DEEPSEEK_API_KEY 未配置")


def _example_factors() -> str:
    """读 2 个现有挖掘因子作为风格参考."""
    names = ["volume_price_corr.py", "reversal_wick_exhaustion.py"]
    parts = []
    for n in names:
        p = ZOO_DIR / n
        if p.exists():
            parts.append(p.read_text(encoding="utf-8")[:2500])
    return "\n\n# === 示例因子 ===\n".join(parts)


def _build_prompt() -> str:
    return f"""你是量化因子研究员。请设计一个新的加密市场横截面 alpha 因子，用于 17 个币种的日频横截面排序（做多 top3 / 做空 bottom3）。

要求:
1. 输出单个 Python 文件: __alpha_meta__ dict + compute(panel) 函数
2. compute 接收 panel = {{"close": DataFrame, "volume": DataFrame}}（行为日期、列为币种）, 返回因子值 DataFrame
3. 只允许 import pandas / numpy / src.factors.base 的工具函数 (delta, rank, safe_div, ts_corr, ts_rank, ts_std, ts_max, ts_min, ts_mean, ts_sum, ts_delta, zscore, sma, ema)
4. 必须是横截面因子 (每行跨币种有区分度), 日频, 市场中性 (不赌单边方向)
5. 不要与以下已存在的因子重复思路: BAB(低贝塔), high52w(52周高点), RMW(低波动), volume_price_corr, market_regime_momentum, reversal_wick_exhaustion
6. 思路要新颖: 可以结合 波动率/动量/量价关系/换手/极值/微观结构 的交叉组合
7. __alpha_meta__ 的 theme 只能从这些选: momentum/reversal/volume/volatility/quality/value/liquidity/microstructure/sentiment/growth/leverage/carry

输出格式: 直接输出 python 代码, 不要解释, 不要 markdown 围栏。

{_example_factors()}"""


def _call_deepseek(prompt: str) -> str:
    import requests
    resp = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {_load_api_key()}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8,
            "max_tokens": 2000,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _extract_code(text: str) -> str:
    """提取 python 代码 (去掉 markdown 围栏/解释)."""
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.S)
    if m:
        return m.group(1).strip()
    # 无围栏: 取第一个 def/import 到结尾
    idx = text.find("def compute")
    if idx == -1:
        idx = text.find("__alpha_meta__")
    return text[idx:].strip() if idx != -1 else text.strip()


def _validate_code(code: str) -> tuple[bool, str]:
    """安全校验: import 白名单 + 危险操作禁止."""
    for line in code.splitlines():
        ls = line.strip()
        if ls.startswith("import") or ls.startswith("from"):
            if not any(ls.startswith(a) for a in ALLOWED_IMPORTS):
                return False, f"非法 import: {ls}"
    for kw in FORBIDDEN:
        if kw in code:
            return False, f"禁止内容: {kw}"
    if "def compute" not in code or "__alpha_meta__" not in code:
        return False, "缺少 compute 函数或 __alpha_meta__"
    return True, "ok"


def _inject_base_import(code: str) -> str:
    """强制注入完整的 src.factors.base 工具导入 — LLM 常漏 import (如只用不导).

    替换生成代码中所有 base import 行为完整工具列表, 保证 compute 可独立加载.
    """
    tools = ("rank, zscore, ts_rank, ts_corr, ts_cov, ts_mean, ts_std, ts_max, "
             "ts_min, ts_argmax, ts_argmin, delta, decay_linear, safe_div, "
             "signed_power, scale, vwap")
    lines = [l for l in code.splitlines()
             if not l.strip().startswith("from src.factors.base import")]
    future = [l for l in lines if l.strip().startswith("from __future__")]
    rest = [l for l in lines if not l.strip().startswith("from __future__")]
    return "\n".join(future + [f"# auto-injected imports (factor_miner)",
                               f"from src.factors.base import {tools}", ""] + rest)


def _write_zoo(code: str, nickname: str) -> Path:
    """写入 zoo 目录 (文件名 = factor_id, 自动被发现). 调用方保证 code 已注入 base import."""
    h = hashlib.sha256((nickname + code).encode()).hexdigest()[:8]
    fname = f"llm_{h}.py"
    path = ZOO_DIR / fname
    if not path.exists():
        path.write_text(code, encoding="utf-8")
    return path


def _smoke_test(fid: str) -> bool:
    """假 panel 冒烟: compute 能跑出结果."""
    import pandas as pd
    from src.strategy.variant_backtester import load_factor_module
    mod = load_factor_module(fid)
    if mod is None:
        # import 失败 — 手动加载打印真实错误
        import importlib
        import traceback
        try:
            importlib.import_module(f"src.factors.zoo.crypto_mined.{fid}")
        except Exception:
            print(f"    导入异常:\n{traceback.format_exc()[-400:]}")
        return False
    idx = pd.date_range("2024-01-01", periods=200, freq="D")
    cols = [f"COIN{i:02d}" for i in range(17)]
    rng = __import__("numpy").random.default_rng(42)
    close = pd.DataFrame(rng.normal(100, 5, (200, 17)).cumsum(axis=0) + 1000, index=idx, columns=cols)
    volume = pd.DataFrame(rng.integers(1e6, 1e8, (200, 17)), index=idx, columns=cols)
    try:
        out = mod.compute({"close": close, "volume": volume})
        return out is not None and out.shape == close.shape and out.notna().any().any()
    except Exception as exc:  # noqa: BLE001
        print(f"    冒烟异常: {type(exc).__name__}: {str(exc)[:200]}")
        return False


def _seed_hypothesis(fid: str, nickname: str, thesis: str, dry_run: bool) -> None:
    """入 hypotheses 池 (exploring), 08:45 自动回测接管."""
    sd = (f"combo_variant: factors=[BAB,high52w,{fid}] "
          f"weights={{BAB:0.33,high52w:0.33,{fid}:0.33}} top_n=3 bot_n=3")
    from src.hypotheses.registry import HypothesisRegistry
    from src.strategy.variant_backtester import HYPOTHESES_PATH
    reg = HypothesisRegistry(HYPOTHESES_PATH)
    existing = {h.signal_definition for h in reg.list()}
    if sd in existing:
        print(f"  已存在相同变体, 跳过入池")
        return
    if dry_run:
        print(f"  [dry-run] 将入池: {sd}")
        return
    reg.create(
        title=f"LLM挖掘因子: {nickname}",
        thesis=thesis,
        status="exploring",
        universe="crypto",
        signal_definition=sd,
        data_sources=["okx"],
        skills=["factor-mining"],
    )
    print(f"  ✅ 已入池 exploring: {nickname} (08:45 自动回测评估)")


def mine_one(dry_run: bool) -> str | None:
    print("  LLM 生成因子中...")
    code = _extract_code(_call_deepseek(_build_prompt()))
    ok, reason = _validate_code(code)
    if not ok:
        print(f"  ⚠️ 安全校验失败: {reason} (丢弃)")
        return None
    m = re.search(r'"nickname":\s*"([^"]+)"', code)
    nickname = m.group(1) if m else f"llm_{hashlib.sha256(code.encode()).hexdigest()[:6]}"
    code = _inject_base_import(code)  # 注入后统一算 fid/文件名
    fid = f"llm_{hashlib.sha256((nickname + code).encode()).hexdigest()[:8]}"
    path = _write_zoo(code, nickname)
    print(f"  已写入: {path.name} ({nickname})")

    if not _smoke_test(fid):
        print("  ⚠️ 冒烟测试失败 (compute 无法运行), 移除")
        path.unlink(missing_ok=True)
        return None
    print("  ✅ 冒烟测试通过")

    _seed_hypothesis(fid, nickname, code.split('"""')[1].strip() if '"""' in code else "LLM 挖掘候选因子",
                     dry_run)
    return fid


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 因子挖掘 (deepseek)")
    ap.add_argument("--count", type=int, default=2, help="生成数量 (默认 2)")
    ap.add_argument("--dry-run", action="store_true", help="只生成验证, 不入库不入池")
    args = ap.parse_args()

    print(f"🧠 LLM 因子挖掘 (deepseek, count={args.count})")
    ok_n = 0
    for i in range(args.count):
        print(f"[{i + 1}/{args.count}]")
        try:
            fid = mine_one(args.dry_run)
            if fid:
                ok_n += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ 生成失败: {exc}")
        time.sleep(2)
    print(f"完成: {ok_n}/{args.count} 个因子通过并入库")
    return 0


if __name__ == "__main__":
    sys.exit(main())
