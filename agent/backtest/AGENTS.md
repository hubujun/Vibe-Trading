# agent/backtest — 回测引擎

## 模块边界

本目录包含回测引擎、数据加载器和优化器，共 77 个 Python 源文件。

| 子包 | 职责 |
|------|------|
| `engines/` | 回测引擎（base、china_a、china_futures、composite、options_portfolio） |
| `loaders/` | 数据加载器（akshare、tushare、finnhub、cn_adjust 等） |
| `optimizers/` | 参数优化器 |
| `runner.py` | 回测运行入口 |
| `metrics.py` | 回测指标计算 |

## 关键约定

- 引擎继承 `engines/base.py` 的 `BaseEngine` 接口
- 数据加载器通过 `loaders/__init__.py` 注册，支持多市场（美股、A股、港股、加密货币）
- `cn_adjust.py` 处理 A 股复权，变更需验证 QDQ（前复权）正确性
- `composite.py` 涉及多币种组合，变更需验证货币守卫

## 聚焦测试命令

```bash
# 回测引擎变更
pytest agent/tests/test_composite_currency_guard.py agent/tests/test_metrics_tracking_error.py agent/tests/test_options_smile_consistency.py -q

# 数据加载器变更
pytest agent/tests/test_alpha_bench_qfq.py agent/tests/test_alpha_bench_universe_metadata.py agent/tests/test_tushare_loader.py -q

# 通用回归
pytest --ignore=agent/tests/e2e_backtest --ignore=agent/tests/test_e2e_harness_v2.py --tb=short -q
```
