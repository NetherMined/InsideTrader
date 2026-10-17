# Trend Sleeve — side-by-side paper test (Phase 6)

## Goal

Run the weekly trend strategy on major coins as a second paper bot next to the current bot, from the same starting
balance, and compare the two on live data for 6–8 weeks. The current bot keeps running unchanged.

Research behind it: the dashboard's Test Results page (Phases 1–5c, Oct 16 2026).
Backtest 2021-03 → 2026-10: +22.7%/yr, Sharpe 1.02, worst drawdown −24%, held up in 93% of tested settings,
but weakening (2025 +6%, 2026 YTD −5%).

## Strategy rules (must match the backtest)

| Rule | Value |
|---|---|
| Universe | BTC, ETH, SOL, BNB, XRP, DOGE, ADA, LINK, AVAX, LTC (USDT-M perpetuals) |
| Data | Binance daily candles (UTC), public API |
| Rebalance | At 00:05 UTC when the candle that just closed is a rebalance close: `(close date - 2021-01-01).days % 5 == 0` |
| Between rebalances | Hold quantities unchanged (no drift resizing; Phase 1 showed it costs ~3%/yr) |
| Signal | Long if `close / close 28 days ago - 1 > 0`, otherwise flat. Never short |
| Size per coin | `equity / 10 × min(2, 0.40 / annualised 30-day volatility)` |
| Fill | Market order at the next open; paper fill = open ± (taker 0.05% + slippage 0.03%) |
| Funding | Charged every funding interval using Binance's real funding rate |

## Risk rules (adapted from the supplied day-trading rules)

- Isolated margin per position; leverage set to 2x. Total exposure is capped at 2x equity by the sizing rule itself.
- Disaster stop per position: −40% from entry, inside the ~−45% liquidation level of a 2x isolated long. It is placed with the
  position, never a mental stop. (Phase 1: −25% fired 63 times on crash wicks and cost ~4%/yr; −40% fired 15 times and costs ~1.5%/yr.)
- Kill switch: if equity falls 35% below its peak (worse than the backtest's worst −24%), close everything, stop
  trading and send a Telegram alert. Restart only by hand.
- Liquid pairs only (the 10 above). No intraday trading.
- Dropped on purpose: "no overnight", session windows and the 3R daily limit. The edge comes from holding for days.

## Isolation from the current bot

- Separate Docker service `trend` from the same bot image (`python -m bot.trend`). Restarting either one leaves the other running.
- Own tables, so the current bot's executor, stats and dashboard never see these trades:
  - `trend_positions` (symbol, qty, entry_price, opened_at, stop_price)
  - `trend_fills` (time, symbol, side, qty, price, fee, slippage, reason: rebalance / stop / kill)
  - `trend_funding` (time, symbol, rate, amount)
  - `strategy_equity` (date, strategy, equity): a daily snapshot of both bots, for the comparison
- Own Redis key `trend:capital_usdt`. Starting value = the current bot's `paper:capital_usdt` at launch, recorded
  in `strategy_equity` as day 0 for both.

## Phases (pause after each for confirmation)

### Phase 1: Strategy module and backtest parity (done Oct 17)
Result (`python -m bot.tools.trend_backtest`, 2021-01 → 2026-10): research parity holds (2022–2025 and max drawdown identical; 2021
differs because the research assumed flat funding before 2022). Live simulation with all rules at $450
(−40% stop, Binance minimum order sizes, kill switch, quantities held): +26.5%/yr, Sharpe 1.14, worst drawdown −25.8%,
2025 +1.6%, 2026 YTD −3.1%. Kill switch never triggered.

- `bot/trend/strategy.py`: pure functions (signal, volatility, target weights, rebalance-day check, orders from current to target).
- Re-run the 2021–2026 backtest through this module. It must reproduce the research numbers within rounding
  (yearly return, Sharpe, drawdown).
- Decide the sizing drift rule. The backtest resizes every day as volatility moves, but live trading will only resize on
  rebalance days, or when a position drifts more than 25% from target. Measure the difference.
- Measure the −25% disaster stop's effect on the backtest.
- Unit tests for the signal, sizing, rebalance calendar and order diffing.

### Phase 2: Paper engine and storage (done Oct 17; running since 2026-10-16 22:41 UTC)
Built as `bot/trend/engine.py` (pure book logic), `bot/trend/market.py` (public Binance data), `bot/trend/runner.py`
(hourly cycle at minute 5 UTC), `trend` service in both compose files, 8 engine tests. Change from the plan: cash,
peak equity and the halt flag live in the `trend_account` table so every fill and its cash move commit together;
Redis `trend:capital_usdt` only mirrors equity for display. Started with $511.82 (current bot equity, no open trades),
initial rebalance on the Oct 15 close: 7 longs, ~42% of equity. First scheduled rebalance: Oct 17 close (Oct 18 00:05 UTC).

- Tables above (migration in `bot/db`).
- `bot/trend/runner.py`: a daily scheduler. Fetch candles, and on rebalance days compute targets and simulate fills. Check stops
  hourly, accrue funding and write the daily equity snapshot. Runs are idempotent, so a restart never double-trades.
- `docker-compose.yml` / `docker-compose.prod.yml`: new `trend` service.
- Telegram message on each rebalance and on any stop or kill switch.
- Tests for fills, funding, idempotency and the kill switch.

### Phase 3: Comparison (done Oct 17)
- The trend runner writes both bots' daily equity to `strategy_equity` and a live summary to Redis `trend:summary`.
- The dashboard's Test Results page, "Side-by-side test" tab, shows both balances, returns, drawdowns, trade counts
  and the trend holdings, live (`GET /api/v1/research/comparison`, refreshes every minute). See
  `PLAN_TEST_RESULTS_PAGE.md`.

### Phase 4: Run and review
- 6–8 weeks of paper trading (about 8–11 rebalances). A weekly review is added to the testing history (`bot/results/phases.json`, then `python -m bot.tools.publish_research`).
- Decision gate: go live with small size only if the trend sleeve beats the current bot and stays within its
  backtest drawdown range.

## Out of scope
- Any change to the current bot's logic.
- Live (real-money) orders for the trend sleeve.
