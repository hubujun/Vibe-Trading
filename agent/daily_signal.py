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

SYMBOLS = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT',
            'DOGE-USDT', 'OKB-USDT', 'ADA-USDT', 'AVAX-USDT', 'LINK-USDT',
            'LTC-USDT', 'DOT-USDT', 'UNI-USDT', 'APT-USDT', 'ARB-USDT']
COST = 0.001
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
    for page in range(math.ceil(days / 100)):
        params = {'instId': symbol, 'bar': '1D', 'limit': '100'}
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
    longs = last_scores.head(top_n).index.tolist()
    shorts = last_scores.tail(bot_n).index.tolist()

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
            prev_day = pd.Timestamp(state['last_signal_date']).date()
            last_day = last_date.date()
            if prev_day < last_day:
                rets = close_df.pct_change()
                seg = rets.loc[pd.Timestamp(prev_day) + pd.Timedelta(days=1): last_day]
                if len(seg) >= 1:
                    r_long = seg[state['last_longs']].mean(axis=1).sum()
                    r_short = -seg[state['last_shorts']].mean(axis=1).sum()
                    net = ((r_long + r_short) / 2 - COST) * mult
                    state['nav'] *= (1 + net)
                    state['trades'].append({
                        'from': str(prev_day), 'to': str(last_day),
                        'ret': round(net * 100, 2),
                        'exposure_multiplier': mult,
                    })
        except Exception as e:
            print('  track 更新失败:', e)

    state['last_signal_date'] = str(last_date.date())
    state['last_longs'] = longs
    state['last_shorts'] = shorts
    state['scores'] = {c: round(float(last_scores[c]), 3) for c in longs + shorts}
    if state['started_at'] is None:
        state['started_at'] = str(last_date.date())
    json.dump(state, open(state_path, 'w'), ensure_ascii=False, indent=2)

    return {
        'strategy_id': strategy_id,
        'name': strategy.get('name', strategy_id),
        'date': str(last_date.date()),
        'longs': longs, 'shorts': shorts,
        'scores': {c: round(float(last_scores[c]), 3) for c in longs + shorts},
        'nav': state['nav'], 'started_at': state['started_at'],
        'trades': state['trades'][-3:],
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
