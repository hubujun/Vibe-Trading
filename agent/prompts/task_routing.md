# Vibe-Trading Task Routing Reference

This document contains the full workflow guidance for the Vibe-Trading agent.

## Backtest workflow

1. `load_skill("strategy-generate")` — read the SignalEngine contract.
2. `write_file("config.json", ...)` — include source, codes, dates, parameters.
   If the strategy is expected to produce ≥10 trades, include:
   ```json
   "validation": {"monte_carlo": {"n_simulations": 1000}}
   ```
3. `write_file("code/signal_engine.py", ...)` — supply the SignalEngine class.
4. Syntax check → `backtest(run_dir=...)` → `read_file("artifacts/metrics.csv")`.
5. Post-backtest attribution analysis: run layers as conditions are met.
   - If a layer is skipped, add one line: `ℹ️ Layer N (name): skipped — [reason]`
   - If data is missing or a tool call fails, skip that layer with a note.
   - Present results as markdown pipe tables.

### Strategy routing

Evaluate top-down, first match wins:
- At-risk: Sharpe ≤ 0.5 or MaxDD ≥ 40% → run Layer 1 + Layer 4.
- Sub-optimal: Sharpe ≤ 1.0 or MaxDD ≥ 20% → run all layers.
- Healthy: everything else → run Layer 1 + Layer 2 only.
- Override: if the user explicitly requests full analysis, run all layers.

### Layer 1 — Trade Attribution

- Read `artifacts/trades.csv`, using exit rows only (`pnl != 0`).
- Report top-5 winners and losers by `pnl`.
- Compute robustness after removing top-5 winners.
- Provide exit-reason breakdown and holding-period buckets.

### Layer 2 — Beta Regression

- Use `get_market_data` for benchmark returns.
- A-shares → CSI 300 (`000300.SH`), US equities → SPY, crypto → BTC-USDT.
- Use equal-weighted composite if no majority market exists.
- Compute OLS: `R_strategy = α + β × R_benchmark`.
- Report α, β, R², and α t-stat.
- Warn if |t| < 2.

### Layer 3 — Regime Analysis

- Condition: more than 1 year of backtest and Layer 2 benchmark data.
- Load `correlation-analysis` and classify regimes.
- Report trade counts, win rates, total PnL, and avg PnL per regime.
- Flag if >60% of profit comes from one regime.

### Layer 4 — Monte Carlo Permutation Test

- Condition: `artifacts/validation.json` contains `monte_carlo`.
- Report actual Sharpe, p-value, actual max drawdown, and p-value.
- Warn if p-value > 0.05.

### Self-check

- Data fidelity: cite specific data points.
- Logical consistency: do not contradict your own analysis.
- Risk disclosure: identify the main strategy risk.

## Swarm team workflow

- Use swarm only when the user explicitly asks for a team/committee flow.
- For named presets: `run_swarm(prompt="<user's full request>", preset_name="<preset>")`.
- Otherwise: `run_swarm(prompt="<user's full request>")`.
- Reuse the previous result or original request for follow-ups like "continue".

## Research and general analysis

- Load the relevant skill first.
- Use the matching tool for factor analysis, options pricing, market data, or other research.

## Document / web workflows

- Use `read_document(path=...)` for PDFs.
- Use `read_url(url=...)` for web content.

## Trade journal workflow

1. Load `shadow-account` or relevant journal tools.
2. Parse the provided CSV/Excel broker export.
3. Analyze the journal with the appropriate tools.

## Shadow Account workflow

- Load `shadow-account` before any shadow_* tools.
- Confirm the journal is parsed.
- Use `extract_shadow_strategy`, `run_shadow_backtest`, and `render_shadow_report`.

## Trading plan / to-do list workflow

1. Read the source file(s) first.
2. Fetch observed prices for every cited symbol in this session.
3. Save any fetched OHLC as CSV in `data/raw/`.
4. Only write the output after all cited prices are observed.
5. Bind each figure to symbol + currency + as-of.
