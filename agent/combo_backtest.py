#!/usr/bin/env python3
"""crypto 三因子等权组合回测 — BAB + RMW + high52w

数据: OKX 日线 (10个主流币)
因子: academic zoo 的 bab/rmw/high52w 直接复用
组合: 横截面等权 z-score, 每日多 top3 空 bottom3, 计入手续费
用法: cd /Users/laohu/Vibe-Trading/agent && ../.venv/bin/python combo_backtest.py
"""
import sys, time, math
import requests
import pandas as pd
import numpy as np

sys.path.insert(0, '/Users/laohu/Vibe-Trading/agent')
from src.factors.zoo.academic import bab, rmw, high52w

SYMBOLS = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT',
           'DOGE-USDT', 'OKB-USDT', 'ADA-USDT', 'AVAX-USDT', 'LINK-USDT']
COST = 0.001  # 单边: taker 0.05% + 滑点 0.05%
DAYS = 800    # 约2024-01起


def fetch_okx_daily(symbol: str, days: int = DAYS) -> pd.Series:
    """OKX 日线收盘价 (close), 分页拉取. 走 ClashX 代理."""
    proxies = {'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}
    url = 'https://www.okx.com/api/v5/market/history-candles'
    all_rows = []
    after = None
    per_page = 100
    pages = math.ceil(days / per_page)
    for page in range(pages):
        params = {'instId': symbol, 'bar': '1D', 'limit': str(per_page)}
        if after:
            params['after'] = str(after)
        for attempt in range(4):
            try:
                r = requests.get(url, params=params, timeout=15, proxies=proxies)
                data = r.json().get('data', [])
                if not data:
                    break
                all_rows.extend(data)
                after = min(int(x[0]) for x in data)  # OKX after=最早时间戳, 返回更早数据
                break
            except Exception as e:
                if attempt == 3:
                    print(f'  fetch fail {symbol} page{page}: {str(e)[:60]}')
                    return pd.Series(dtype=float)
                time.sleep(2)
    if not all_rows:
        return pd.Series(dtype=float)
    rows = sorted(all_rows, key=lambda x: int(x[0]))[-days:]
    idx = pd.to_datetime([int(x[0]) for x in rows], unit='ms')
    return pd.Series([float(x[4]) for x in rows], index=idx, name=symbol)


def main():
    print(f'拉取 {len(SYMBOLS)} 个币种 OKX 日线...')
    closes = {}
    for s in SYMBOLS:
        closes[s] = fetch_okx_daily(s)
        print(f'  {s}: {len(closes[s])} 根')
        time.sleep(0.4)

    close_df = pd.DataFrame(closes)
    close_df = close_df.dropna(axis=1, how='all')  # 删全空列
    close_df = close_df.ffill().dropna()
    if close_df.shape[0] < 300 or close_df.shape[1] < 4:
        print('panel 数据不足, 中止'); return
    print(f'\npanel: {close_df.shape[0]} 日 x {close_df.shape[1]} 币, '
          f'{close_df.index[0].date()} ~ {close_df.index[-1].date()}')

    panel = {'close': close_df}
    factors = {
        'BAB': bab.compute(panel),
        'RMW': rmw.compute(panel),
        'high52w': high52w.compute(panel),
    }
    # 去掉 warmup 前的 NaN 行
    valid = close_df.index[260:]
    for k in factors:
        factors[k] = factors[k].reindex(close_df.index)

    combo = pd.concat(factors.values()).groupby(level=0).mean()
    combo = combo.reindex(close_df.index)
    combo2 = ((factors['BAB'] + factors['high52w']) / 2).reindex(close_df.index)  # 双因子变体

    # 单因子与组合的 IC
    rets = close_df.pct_change()
    fwd = rets.shift(-1)
    print('\n=== 因子 IC (当日信号 vs 次日收益) ===')
    ic_table = {}
    for k, f in {**factors, 'COMBO': combo}.items():
        valid_mask = f.notna() & fwd.notna()
        ic = f[valid_mask].corrwith(fwd[valid_mask], axis=1)
        ic_clean = ic.dropna()
        ic_table[k] = ic_clean
        print(f'  {k:<8} IC均值={ic_clean.mean():+.4f}  IR={ic_clean.mean()/ic_clean.std():+.3f} '
              f'IC+比率={100*(ic_clean>0).mean():.1f}%')

    # 组合回测: 每日 rank, 多 top3 空 bottom3, 等权, 次日开盘调仓 (用收盘近似), 计成本
    print('\n=== 组合回测 (多top3空bottom3, 日频调仓, 单边成本0.1%) ===')
    bt_results = {}
    for name, f in [('COMBO3(等权)', combo), ('COMBO2(BAB+52w)', combo2)] + list(factors.items()):
        # 逐日 rank
        r = f.rank(axis=1, pct=True)
        long_mask = r >= 0.7   # top 3/10
        short_mask = r <= 0.3  # bottom 3/10
        w = long_mask.astype(float) - short_mask.astype(float)
        w = w.div(w.abs().sum(axis=1), axis=0)  # 归一化多空总敞口=1
        daily_ret = (w.shift(1) * rets).sum(axis=1)
        # 调仓换手成本: 权重变化绝对值/2 * cost
        turnover = w.diff().abs().sum(axis=1) / 2
        net = daily_ret - turnover * COST
        nav = (1 + net.fillna(0)).cumprod()
        total = nav.iloc[-1] - 1
        years = len(nav) / 365
        annual = (1 + total) ** (1 / years) - 1
        sharpe = net.mean() / net.std() * math.sqrt(365) if net.std() > 0 else 0
        dd = (nav / nav.cummax() - 1).min()
        bt_results[name] = {
            'annual': round(annual * 100, 2), 'sharpe': round(sharpe, 2),
            'max_dd': round(dd * 100, 2), 'cum': round(total * 100, 2),
            'turnover': round(float(turnover.mean()), 3),
        }
        print(f'  {name:<12} 年化={annual*100:+.1f}%  夏普={sharpe:.2f}  '
              f'最大回撤={dd*100:.1f}%  累计={total*100:+.1f}%  日均换手={turnover.mean():.2f}')

    # 固化指标供 web API 读取
    import json, os
    out = {
        'updated_at': str(pd.Timestamp.now().date()),
        'period': f'{close_df.index[0].date()} ~ {close_df.index[-1].date()}',
        'symbols': close_df.shape[1], 'days': close_df.shape[0],
        'cost_per_side': COST,
        'ic': {k: {'ic_mean': round(v.mean(), 4), 'ir': round(v.mean() / v.std(), 3),
                   'ic_pos': round(100 * (v > 0).mean(), 1)}
               for k, v in ic_table.items()},
        'backtest': bt_results,
    }
    os.makedirs(os.path.dirname('/Users/laohu/.vibe-trading/runs/paper_combo/'), exist_ok=True)
    json.dump(out, open('/Users/laohu/.vibe-trading/runs/paper_combo/backtest_metrics.json', 'w'),
              ensure_ascii=False, indent=2)
    print('\n指标已写入 runs/paper_combo/backtest_metrics.json')

    # 输出净值序列供后续使用
    combo_nav = (1 + ((combo.rank(axis=1, pct=True) >= 0.7).astype(float) -
                       (combo.rank(axis=1, pct=True) <= 0.3).astype(float)))
    combo_nav.to_csv('/tmp/combo_signal.csv')


if __name__ == '__main__':
    main()
