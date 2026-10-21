# Trade history fixes (Oct 21 review)

## Goal

The Oct 21 review of 184 paper trades (Oct 14–21) found the bot wins 55% of trades but loses money: the average
loss ($0.89) is about 1.4× the average win, and fees ($11.89) are the whole loss. It also found bugs that make the
dashboard numbers wrong and that leave positions without price checks. This plan fixes the bugs first, then tests
exit and coin-filter changes on past data before any live setting is touched.

## Phase 1: Daily P&L and fee counters (reporting only)

Nothing here changes which trades open or close, except that defensive mode exits at the right time.

1. **Daily P&L % measured against capital.** `RiskManager.on_trade_closed` (`bot/trading/risk.py`) adds each
   trade's leveraged % to `bot:daily_pnl`, so the bot showed -18.8% when the real loss was -1.1%.
   - Store `bot:daily_pnl` as `bot:daily_pnl_usdt / capital * 100`. Capital is passed in already.
   - `trade_returns` keeps the per-trade %, since that is what it is for.
2. **Defensive recovery uses the fixed %.** `check_defensive_recovery` reads `bot:daily_pnl`. After fix 1 it
   compares like with like. No other change.
3. **Daily fees reset each day.** `_reset_if_new_day` does not reset `bot:daily_fees` or `bot:daily_gross_pnl`.
   - Reset both there.
   - Add the fee to `bot:cumulative_fees` in `record_fee` (the key is read but never written).
4. **Gross P&L is gross, not fees.** `record_fee` adds the fee to `bot:daily_gross_pnl`, so gross always equals
   fees, and the fee-erosion warning never fires.
   - Remove that line from `record_fee`.
   - Call `record_gross_pnl` with `closed.gross_usdt` on both close paths in `executor.py` (the normal check and
     the worst-position close), next to the existing `record_fee` calls.
5. **Tests** in `bot/tests/` for: % against capital, day reset of the fee and gross keys, and gross minus fees
   equal to net.
6. **Live Redis keys.** Set `bot:daily_pnl` once from `bot:daily_pnl_usdt` and capital. Reset `bot:daily_fees`
   and `bot:daily_gross_pnl` to today's values from the `trades` table.

## Phase 2: Price and data reliability

1. **Prices for futures-only coins.** `get_current_price` (`bot/trading/orders.py`) asks the spot market only.
   Coins listed only on futures (e.g. `4/USDT`, and the earlier ghost positions) fail every time the price cache
   is stale. Their stop-loss and take-profit are then not checked.
   - If spot fails, try the USDT futures market (`defaultType: future`, symbol `X/USDT:USDT`).
   - Only log a warning when both fail.
2. **Funding rates from the real market.** `fetch_funding_rates` (`bot/analysis/funding_rate.py`) switches to
   testnet when `USE_TESTNET=true`. Testnet errors or has almost none of the coins, so 0 of 184 trades have a
   funding rate recorded.
   - Funding rates are public data. Fetch them from the main Binance futures API with no keys, like the price
     data.
   - Orders still follow the testnet setting.
3. **Redis timeouts.** The trading engine logged "Timeout reading from redis" 15 times, all during model
   retraining. Each one skips a full position check.
   - Give the bot's Redis clients (`bot/main.py`, `bot/data/stream.py`) retry-on-timeout and a health check
     interval, so one slow read is retried instead of skipping the loop.
4. **Tests** for the spot-then-futures price fallback and the funding fetch not using testnet.

## Phase 3: Exit and coin-filter replay (no live changes)

Replay the 184 trades with the existing replay code (`bot/tools/baseline.py`, 1m candles), using the actual entry
times and sides. Compare each variant against the current rules on net P&L, win rate, and avg win / avg loss.

| Variant | Change |
|---|---|
| A | Current rules (control) |
| B | Trail switches on later: +0.6%, +0.8%, +1.0% raw price move |
| C | No trailing stop: take-profit, stop-loss, and timeout only |
| D | Skip coins whose normal 15m swing (ATR %) is larger than half the stop distance |
| E | Shorts only when BTC is below its 20-day average; otherwise longs only |
| F | Best of B/C combined with D and E |

- Earlier exit replays (Oct 16) found that exit changes alone did not make the bot profitable. A variant only
  counts as a win if it beats the control on net P&L across both the first and second half of the period, not
  just overall.
- Save the results as `bot/results/trade_fixes_replay.json` and load them into the Test Results page with
  `python -m bot.tools.publish_research`.
- Add a line to `phases.json` (testing history) with the question, method, finding, and decision.

## Phase 4: Apply what the replay supports

Only if a Phase 3 variant passes. Change the matching settings in `bot/config.py` (and Redis where live-switchable)
and record the change in the testing history. If nothing passes, record that and leave the live rules as they are.

## Out of scope

- Telegram 403: the bot was blocked in Telegram by the user; this needs unblocking in Telegram, not code.
- Network warnings when loading Binance's market list: these are retried already and did not affect trades.
