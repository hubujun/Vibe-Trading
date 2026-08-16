#!/usr/bin/env python3
"""BAB+high52w 双因子每日信号生成器 + paper track record 追踪

用法: cd /Users/laohu/Vibe-Trading/agent && ../.venv/bin/python daily_signal.py
输出: 今日多空信号 + 组合模拟盘累计表现 (状态存 /Users/laohu/Vibe-Trading/agent/runs/paper_combo/state.json)
"""
import sys, os, json, time, math
import requests
import pandas as pd
import numpy as np

sys.path.insert(0, '/Users/laohu/Vibe-Trading/agent')
from src.factors.zoo.academic import bab, high52w

SYMBOLS = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT',
           'DOGE-USDT', 'OKB-USDT', 'ADA-USDT', 'AVAX-USDT', 'LINK-USDT']
COST = 0.001
STATE_PATH = '/Users/laohu/.vibe-trading/runs/paper_combo/state.json'
DAYS = 800


def fetch_okx_daily(symbol: str, days: int = DAYS) -> pd.Series:
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
                    return pd.Series(dtype=float)
                time.sleep(2)
    if not all_rows:
        return pd.Series(dtype=float)
    rows = sorted(all_rows, key=lambda x: int(x[0]))[-days:]
    idx = pd.to_datetime([int(x[0]) for x in rows], unit='ms')
    return pd.Series([float(x[4]) for x in rows], index=idx, name=symbol)


def build_signal() -> dict:
    closes = {}
    for s in SYMBOLS:
        closes[s] = fetch_okx_daily(s)
        time.sleep(0.3)
    close_df = pd.DataFrame(closes).dropna(axis=1, how='all').ffill().dropna()
    if close_df.shape[0] < 300:
        return {'error': 'panel 数据不足'}
    panel = {'close': close_df}
    bab_f = bab.compute(panel)
    h52_f = high52w.compute(panel)
    score = ((bab_f + h52_f) / 2).reindex(close_df.index)

    last_date = close_df.index[-1]
    last_scores = score.iloc[-1].dropna().sort_values(ascending=False)
    longs = last_scores.head(3).index.tolist()
    shorts = last_scores.tail(3).index.tolist()

    # 追踪组合表现: 从上一次信号日起
    state = {'started_at': None, 'nav': 1.0, 'last_signal_date': None,
             'last_longs': [], 'last_shorts': [], 'trades': []}
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    if os.path.exists(STATE_PATH):
        try:
            state = json.load(open(STATE_PATH))
        except Exception:
            pass
    if state.get('last_signal_date') and state['last_longs']:
        try:
            prev_date = pd.Timestamp(state['last_signal_date'])
            if prev_date < last_date:
                rets = close_df.pct_change()
                seg = rets.loc[prev_date:last_date]
                if len(seg) >= 1:
                    r_long = seg[state['last_longs']].mean(axis=1).sum()
                    r_short = -seg[state['last_shorts']].mean(axis=1).sum()
                    # 每日多空等权, 单边成本摊到每次调仓
                    net = (r_long + r_short) / 2 - COST
                    state['nav'] *= (1 + net)
                    state['trades'].append({
                        'from': str(prev_date.date()), 'to': str(last_date.date()),
                        'ret': round(net * 100, 2),
                    })
        except Exception as e:
            print('  track 更新失败:', e)

    state['last_signal_date'] = str(last_date.date())
    state['last_longs'] = longs
    state['last_shorts'] = shorts
    state['scores'] = {c: round(float(last_scores[c]), 3) for c in longs + shorts}
    if state['started_at'] is None:
        state['started_at'] = str(last_date.date())
    json.dump(state, open(STATE_PATH, 'w'), ensure_ascii=False, indent=2)

    return {
        'date': str(last_date.date()),
        'longs': longs, 'shorts': shorts,
        'scores': {c: round(float(last_scores[c]), 3) for c in longs + shorts},
        'nav': state['nav'], 'started_at': state['started_at'],
        'trades': state['trades'][-3:],
    }


if __name__ == '__main__':
    sig = build_signal()
    if 'error' in sig:
        print('ERROR:', sig['error'])
        sys.exit(1)
    print(f"=== BAB+high52w 双因子信号 {sig['date']} ===")
    print(f"做多(top3): {', '.join(sig['longs'])}")
    print(f"做空(bottom3): {', '.join(sig['shorts'])}")
    print(f"得分: {sig['scores']}")
    print(f"模拟盘净值: {sig['nav']:.4f} (起于 {sig['started_at']})")
    if sig['trades']:
        print('最近调仓:', json.dumps(sig['trades'], ensure_ascii=False))
