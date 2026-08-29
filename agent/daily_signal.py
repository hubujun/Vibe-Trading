#!/usr/bin/env python3
"""组合策略每日信号生成器 + paper track record 追踪 (多策略并行)

用法:
  ../.venv/bin/python daily_signal.py                       # 默认 combo_bab_52w
  ../.venv/bin/python daily_signal.py --strategy <sid>      # 指定工作台策略

每个策略从 workbench strategies.json 读取 signal_definition (combo_variant: ...),
解析因子/权重/top_n/bot_n, 生成多空信号并记账到自己的 run_dir/state.json —
多策略并行时各自独立跑模拟盘.
输出: 今日多空信号 + 组合模拟盘累计表现
"""
import sys, os, json, time, math, argparse
import requests
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.strategy.macro_events import (
    event_leverage_multiplier,
    get_regime,
    market_state_features,
)
from src.strategy.variant_backtester import SECTOR, SECTOR_CAP, VOL_TARGET, _sector_cap

SYMBOLS = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT',
            'DOGE-USDT', 'OKB-USDT', 'ADA-USDT', 'AVAX-USDT', 'LINK-USDT',
            'LTC-USDT', 'DOT-USDT', 'UNI-USDT', 'APT-USDT', 'ARB-USDT',
            'TRUMP-USDT', 'LAB-USDT']
COST = 0.001

#: 无现货、只有永续的币 → 蜡烛用永续 instId (LAB 2025-11 上市, 无现货交易对)
PERP_ONLY = {"LAB-USDT": "LAB-USDT-SWAP"}

#: 调仓缓冲: 新旧持仓重叠 ≥ 此比例则不调仓 (省换手/成本)
BAND_KEEP_RATIO = 0.67

#: 永续合约资金费率 (OKX: 0.01%/8h 基准 = 0.03%/天; 多头付/空头收)
FUNDING_RATE_DAY = 0.0003

#: 信号逻辑版本 — 信号/记账逻辑变更时 +1, 每笔调仓记录版本 (复盘可按版本分组)
#: v1: 静态权重 + 动态多空比 + 板块上限 + 波动率目标 + 事件/regime (与回测一致)
SIGNAL_LOGIC_VERSION = 1
WORKBENCH_PATH = os.path.expanduser('~/.vibe-trading/workbench/strategies.json')
RUNTIME_ROOT = os.path.expanduser('~/.vibe-trading/runs')
DAYS = 800


def load_strategy(strategy_id: str) -> dict | None:
    """从工作台读取策略配置 (含 signal_definition / run_dir)."""
    try:
        raw = json.load(open(WORKBENCH_PATH))
        for s in raw.get('strategies', []):
            if s.get('strategy_id') == strategy_id:
                return s
    except Exception:
        pass
    return None


def load_exposure_multiplier(strategy_id: str) -> float:
    """读取工作台参数自适应结果 (第三圈): 该策略的 exposure_multiplier."""
    try:
        raw = json.load(open(WORKBENCH_PATH))
        for s in raw.get('strategies', []):
            if s.get('strategy_id') == strategy_id:
                return float(s.get('params', {}).get('exposure_multiplier', 1.0))
    except Exception:
        pass
    return 1.0


def fetch_okx_daily(symbol: str, days: int = DAYS) -> pd.DataFrame:
    """拉 OKX 日线 close + volume (history-candles: ts,o,h,l,c,vol,...)."""
    proxies = {'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}
    url = 'https://www.okx.com/api/v5/market/history-candles'
    all_rows, after = [], None
    # 无现货的币用永续 instId (LAB 只有永续)
    inst_id = PERP_ONLY.get(symbol, symbol)
    for page in range(math.ceil(days / 100)):
        params = {'instId': inst_id, 'bar': '1D', 'limit': '100'}
        if after:
            params['after'] = str(after)
        for attempt in range(4):
            try:
                r = requests.get(url, params=params, timeout=15, proxies=proxies)
                data = r.json().get('data', [])
                if not data:
                    break
                all_rows.extend(data)
                after = min(int(x[0]) for x in data)
                break
            except Exception as e:
                if attempt == 3:
                    print(f'  fetch fail {symbol}: {str(e)[:50]}')
                    return pd.DataFrame(columns=['close', 'volume'])
                time.sleep(2)
    if not all_rows:
        return pd.DataFrame(columns=['close', 'volume'])
    rows = sorted(all_rows, key=lambda x: int(x[0]))[-days:]
    # OKX candle ts 为北京时间 00:00 (UTC+8), 归一到北京日期 00:00
    # (否则 naive UTC 解析 .date() 少一天, 且日期切片匹配不到 16:00 index)
    idx = pd.to_datetime([int(x[0]) for x in rows], unit='ms') + pd.Timedelta(hours=8)
    idx = idx.normalize()
    return pd.DataFrame(
        {'close': [float(x[4]) for x in rows],
         'volume': [float(x[5]) for x in rows],
         'high': [float(x[2]) for x in rows],
         'low': [float(x[3]) for x in rows]},
        index=idx,
    )


def _row_zscore(df: pd.DataFrame) -> pd.DataFrame:
    """横截面标准化: 每行 (每天) 减去该行均值后除以行标准差."""
    mean = df.mean(axis=1)
    std = df.std(axis=1)
    std = std.where(std > 0)
    return df.sub(mean, axis=0).div(std, axis=0)


def _ic_ir_weights(factor_values: dict, close_df, lookback: int = 60):
    """机构实践: 因子权重 ∝ IC_IR (滚动截面 IC 均值 / IC 标准差).

    负 IC_IR 因子截断为 0 (不反向用 — 反向信号不稳定); 数据不足返回 None (用原权重).
    """
    rets = close_df.pct_change().shift(-1)
    out: dict = {}
    for fid, f in factor_values.items():
        ff = f.reindex(close_df.index).rank(axis=1)
        rr = rets.reindex(close_df.index).rank(axis=1)
        ic = ff.corrwith(rr, axis=1).dropna().tail(lookback)
        if len(ic) >= 20:
            out[fid] = max(float(ic.mean() / (ic.std() + 1e-9)), 0.0)
        else:
            out[fid] = None
    if not out or all(v is None for v in out.values()):
        return None
    total = sum(v for v in out.values() if v is not None)
    if total <= 0:
        return None
    return {fid: (v or 0.0) / total for fid, v in out.items()}


def _apply_band(new_longs: list[str], new_shorts: list[str],
                old_longs: list[str], old_shorts: list[str],
                keep_ratio: float = BAND_KEEP_RATIO):
    """机构实践: 调仓缓冲 — 新旧持仓重叠 ≥ 阈值则不调仓 (省换手/成本)."""
    if not old_longs or not old_shorts:
        return new_longs, new_shorts, False
    # 多空 n 可能不同 (动态多空比) — 分别算保持阈值
    n_l = max(1, len(new_longs))
    keep_l = min(max(1, int(n_l * keep_ratio)), n_l)
    n_s = max(1, len(new_shorts))
    keep_s = min(max(1, int(n_s * keep_ratio)), n_s)
    ol = len(set(new_longs) & set(old_longs))
    os_ = len(set(new_shorts) & set(old_shorts))
    if ol >= keep_l and os_ >= keep_s:
        return old_longs, old_shorts, True
    return new_longs, new_shorts, False


def _vol_target_mult(close_df, longs: list[str], shorts: list[str],
                     target: float = VOL_TARGET, window: int = 20) -> float:
    """机构实践: 波动率目标 — 组合滚动波动率高于目标自动缩仓 (连续版风控)."""
    if not longs or not shorts:
        return 1.0
    rets = close_df.pct_change()
    port = rets[longs].mean(axis=1).sub(rets[shorts].mean(axis=1)).div(2)
    vol = port.tail(window).std() * (252 ** 0.5)
    if not math.isfinite(vol) or vol <= 1e-6:
        return 1.0
    return float(min(max(target / vol, 0.3), 1.5))


def build_signal(strategy: dict) -> dict:
    """按策略的 signal_definition 生成今日多空信号 + 更新该策略模拟盘."""
    from src.strategy.variant_backtester import load_factor_module, parse_signal_definition

    sd = strategy.get('signal_definition', '')
    spec = parse_signal_definition(sd)
    if spec is None:
        return {'error': f'signal_definition 无法解析: {sd}'}
    factors: list[str] = list(spec['factors'])
    weights: dict[str, float] = {k: float(v) for k, v in spec['weights'].items()}
    top_n, bot_n = int(spec['top_n']), int(spec['bot_n'])
    strategy_id = strategy.get('strategy_id', 'combo_bab_52w')
    state_path = os.path.join(
        strategy.get('run_dir') or os.path.join(RUNTIME_ROOT, 'paper_combo'), 'state.json',
    )

    # 拉面板: close + volume (zoo 因子需要 volume, 学术因子只用 close)
    closes, volumes = {}, {}
    for s in SYMBOLS:
        df = fetch_okx_daily(s)
        if df.empty or len(df) < 300:
            continue
        closes[s] = df['close']
        volumes[s] = df['volume']
        time.sleep(0.3)
    if len(closes) < 5:
        return {'error': 'panel 数据不足'}
    close_df = pd.DataFrame(closes).ffill().dropna()
    if close_df.shape[0] < 300:
        return {'error': 'panel 数据不足'}
    volume_df = pd.DataFrame(volumes).reindex(close_df.index).ffill()

    # 因子合成: Σ w_i × factor_i (学术因子已 z-score, zoo 因子 raw → 行 z-score 统一)
    # 注意: 权重必须与 backtest_variant 一致 (静态权重) — 实盘行为 = 回测行为
    # (IC_IR 动态加权仅实盘会导致回测评估的策略实盘跑不同逻辑, 已移除)
    from src.strategy.variant_backtester import ACADEMIC_MODULES

    score = None
    total_w = 0.0
    for fid in factors:
        mod = load_factor_module(fid)
        if mod is None:
            print(f'  警告: 因子 {fid} 模块缺失, 跳过')
            continue
        try:
            f = mod.compute({'close': close_df, 'volume': volume_df})
        except Exception as e:
            print(f'  警告: 因子 {fid} compute 失败: {str(e)[:60]}')
            continue
        f = f.reindex(close_df.index)
        if fid not in ACADEMIC_MODULES:
            f = _row_zscore(f)
        w = weights.get(fid, 1.0 / len(factors))
        score = f * w if score is None else score.add(f * w, fill_value=0)
        total_w += w
    if score is None or total_w <= 0:
        return {'error': '无可用因子'}
    score = score / total_w
    last_date = close_df.index[-1]
    last_scores = score.iloc[-1].dropna().sort_values(ascending=False)
    # 机构实践: 温和版动态多空比 — 按 regime 调整多空数量 (牛市减空/熊市减多)
    # 与回测 backtest_variant(dynamic_n) 一致, 实盘行为 = 回测行为
    regime_now = get_regime(close_df, d=last_date.date())["regime"]
    if regime_now == "risk_on":
        top_n_eff, bot_n_eff = top_n, max(1, bot_n - 1)
    elif regime_now == "risk_off":
        top_n_eff, bot_n_eff = max(1, top_n - 1), bot_n
    else:
        top_n_eff, bot_n_eff = top_n, bot_n
    # 机构实践: 板块权重上限 (同板块最多 SECTOR_CAP 个, 防 meme 扎堆)
    longs = _sector_cap(last_scores.index.tolist(), top_n_eff)
    shorts = _sector_cap(last_scores.index.tolist()[::-1], bot_n_eff)

    # 机构实践: 调仓缓冲 — 新旧持仓重叠 ≥ 阈值则不调仓 (省换手/成本)
    prev_state = {}
    if os.path.exists(state_path):
        try:
            prev_state = json.load(open(state_path))
        except Exception:
            pass
    longs, shorts, held = _apply_band(
        longs, shorts,
        prev_state.get('last_longs', []), prev_state.get('last_shorts', []),
    )
    if held:
        # band 保持持仓时也要应用动态多空比: 截断到当前 regime 的 n
        # (否则牛市 3+3→3+2 后重叠≥阈值导致减空头永不生效)
        longs = longs[:top_n_eff]
        shorts = shorts[:bot_n_eff]

    # 追踪组合表现: 从上一次信号日起
    state = {'started_at': None, 'nav': 1.0, 'last_signal_date': None,
             'last_longs': [], 'last_shorts': [], 'trades': []}
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    if os.path.exists(state_path):
        try:
            state = json.load(open(state_path))
        except Exception:
            pass
    if state.get('last_signal_date') and state['last_longs']:
        try:
            mult = load_exposure_multiplier(strategy_id)  # 第三圈: 参数自适应
            # 第 1 层: 事件日历降杠杆 (A/B 级事件日)
            event_mult = event_leverage_multiplier(last_date.date())
            # 第 2 层: regime 判定 (BTC 动量 → 全局缩仓 + 多空不对称)
            regime = get_regime(close_df, d=last_date.date())
            # 机构实践: 波动率目标 — 组合滚动波动率高于目标自动缩仓 (连续风控)
            vol_mult = _vol_target_mult(close_df, state['last_longs'], state['last_shorts'])
            long_mult = mult * event_mult * regime["long_factor"] * vol_mult
            short_mult = mult * event_mult * regime["short_factor"] * vol_mult
            prev_day = pd.Timestamp(state['last_signal_date']).date()
            last_day = last_date.date()
            if prev_day < last_day:
                rets = close_df.pct_change()
                seg = rets.loc[pd.Timestamp(prev_day) + pd.Timedelta(days=1): last_day]
                if len(seg) >= 1:
                    r_long = seg[state['last_longs']].mean(axis=1).sum()
                    r_short = -seg[state['last_shorts']].mean(axis=1).sum()
                    # 永续资金费 (OKX 规则: 0.01%/8h = 0.03%/天; 多头付, 空头收)
                    # 多空对称 (3/3) 时净资金费≈0 — 市场中性组合在永续市场的隐藏红利
                    n_days = max(1, (last_day - prev_day).days)
                    funding_paid = len(state['last_longs']) * FUNDING_RATE_DAY * n_days
                    funding_received = len(state['last_shorts']) * FUNDING_RATE_DAY * n_days
                    net_funding = funding_received - funding_paid
                    # 多空腿分别乘自己的乘数 (事件×regime×自适应), 成本按基准 mult
                    net = ((r_long * long_mult + r_short * short_mult) / 2 - COST * mult) + net_funding
                    state['nav'] *= (1 + net)
                    state['trades'].append({
                        'from': str(prev_day), 'to': str(last_day),
                        'ret': round(net * 100, 2),
                        'exposure_multiplier': round(mult, 3),
                        'event_multiplier': round(event_mult, 3),
                        'regime': regime["regime"],
                        'long_mult': round(long_mult, 3),
                        'short_mult': round(short_mult, 3),
                        'funding_paid': round(funding_paid * 100, 3),
                        'funding_received': round(funding_received * 100, 3),
                        'funding_net': round(net_funding * 100, 3),
                        'logic_version': SIGNAL_LOGIC_VERSION,  # 逻辑版本标记
                    })
        except Exception as e:
            print('  track 更新失败:', e)

    state['last_signal_date'] = str(last_date.date())
    state['last_longs'] = longs
    state['last_shorts'] = shorts
    state['scores'] = {c: round(float(v), 3) for c, v in last_scores.items()}
    if state['started_at'] is None:
        state['started_at'] = str(last_date.date())
    # append-only 保护: 新 trades 必须是旧 trades 的前缀扩展 —
    # 任何非追加方式的修改 (覆盖/删除历史) 拒绝写入, 防止迭代 bug 污染模拟盘数据
    if os.path.exists(state_path):
        try:
            old = json.load(open(state_path))
            old_trades = old.get('trades') or []
            new_trades = state.get('trades') or []
            if old_trades and new_trades[:len(old_trades)] != old_trades:
                raise RuntimeError(
                    'trades 被非追加方式修改 (历史被覆盖/删除)! 拒绝写入, 防止数据污染'
                )
        except RuntimeError:
            raise
        except Exception:
            pass
    json.dump(state, open(state_path, 'w'), ensure_ascii=False, indent=2)

    # 事件/regime 摘要 (无历史持仓时也有值)
    if 'regime' not in locals():
        regime = get_regime(close_df, d=last_date.date())
    if 'event_mult' not in locals():
        event_mult = event_leverage_multiplier(last_date.date())

    return {
        'strategy_id': strategy_id,
        'name': strategy.get('name', strategy_id),
        'date': str(last_date.date()),
        'longs': longs, 'shorts': shorts,
        'scores': {c: round(float(last_scores[c]), 3) for c in longs + shorts},
        'nav': state['nav'], 'started_at': state['started_at'],
        'trades': state['trades'][-3:],
        'regime': regime["regime"],
        'event_multiplier': event_mult,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='组合策略每日信号 (多策略并行)')
    parser.add_argument('--strategy', default='combo_bab_52w', help='workbench 策略 id')
    args = parser.parse_args()

    strategy = load_strategy(args.strategy)
    if strategy is None:
        print(f'ERROR: 策略不存在: {args.strategy}')
        sys.exit(1)
    sig = build_signal(strategy)
    if 'error' in sig:
        print('ERROR:', sig['error'])
        sys.exit(1)
    print(f"=== {sig['name']} 信号 {sig['date']} ===")
    print(f"做多(top{sig.get('longs') and len(sig['longs'])}): {', '.join(sig['longs'])}")
    print(f"做空(bottom{sig.get('shorts') and len(sig['shorts'])}): {', '.join(sig['shorts'])}")
    print(f"得分: {sig['scores']}")
    print(f"模拟盘净值: {sig['nav']:.4f} (起于 {sig['started_at']})")
    if sig['trades']:
        print('最近调仓:', json.dumps(sig['trades'], ensure_ascii=False))
