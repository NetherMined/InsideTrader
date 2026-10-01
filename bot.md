# InsideTrader — Bot Operations Reference

## How the Bot Works

The bot runs as a continuous async Python process inside Docker. It follows a repeating 60-second loop: scan the market, analyse opportunities with ML, rank them, and execute trades. Positions are monitored every loop tick and closed when TP, SL, or timeout conditions are met. All state is persisted in PostgreSQL (trades, positions, candles, predictions) and Redis (live prices, bot state, counters, cooldowns).

---

## Startup Sequence

1. `bot/main.py` is the entry point — it initialises the async engine
2. Database connection pool is established (asyncpg → PostgreSQL)
3. Redis connection is established (aioredis)
4. Bot reads its start signal from Redis key `bot:command`
5. If `bot:command == "start"`, the main trading loop begins
6. The data pipeline starts concurrently: scanner → fetcher → stream
7. An initial analysis cycle runs to populate predictions before the first trade tick
8. Executor begins its 60-second loop

---

## Data Pipeline

### 1. Scanner (`bot/data/scanner.py`)
- Queries Binance public API for all USDT markets
- Filters to valid pairs: spot symbols ending in `/USDT`, futures symbols ending in `/USDT:USDT`
- Normalises futures symbols to `BTC/USDT` format for internal use
- Currently identifies ~67 valid tradeable pairs
- Provides the universe of symbols for analysis and sentiment calculation

### 2. Fetcher (`bot/data/fetcher.py`)
- Downloads OHLCV candlestick data for each symbol from Binance public API (no auth required)
- Stores candles in PostgreSQL `candles` table per symbol and timeframe
- Analysis timeframe: `1h`; lookback for sub-hourly capped at 30 days
- Runs periodically to keep candles current

### 3. Price Stream (`bot/data/stream.py`)
- Subscribes to live price ticks for all valid pairs
- Writes current price to Redis key `price:{symbol}` every update
- Feeds the dashboard WebSocket at `/ws/prices`
- Also used to compute market sentiment (advance_ratio) each executor loop

---

## Analysis Cycle

The analysis cycle runs periodically and independently of the trade executor (concurrency-gated to 8 symbols at a time).

### Step 1 — Technical Indicators (`bot/analysis/indicators.py`)
For each symbol's candle history, compute:
- RSI(14), MACD(12/26/9), Bollinger Bands(20), ATR(14), ADX(14)
- EMA(9), EMA(21), EMA(50), EMA(200)
- Volume SMA(20)
- Stochastic(14,3) — stoch_k, stoch_d
- OBV and OBV SMA

Requires minimum 100 rows of candle data.

### Step 2 — Feature Engineering (`bot/analysis/features.py`)
45 features engineered from indicators:

| Category | Features |
|----------|---------|
| Momentum | rsi, macd_hist, stoch_k, stoch_d, rsi_oversold, rsi_overbought |
| Volatility | atr_pct, bb_pct, bb_width_pct, bb_squeeze, vol_surge, vol_ratio |
| Trend | adx, ema9_ratio, ema21_ratio, ema50_ratio, ema50_200_ratio, above_ema200 |
| Returns | ret_1, ret_4, ret_24 |
| Volume | obv_ratio |
| Regime | regime_trending, near_support |

`ema21_ratio = (close / ema21) - 1` — positive means price above EMA21 (bullish), negative means below.

### Step 3 — ML Model (`bot/analysis/model.py`)
Two XGBoost models per symbol, saved as `.pkl` files in Docker volume `ml_models`:

| Model | Type | Output |
|-------|------|--------|
| Regressor | XGBRegressor | `predicted_change_pct` — expected % price change over 24h |
| Classifier | XGBClassifier | `confidence` — probability that the predicted direction is correct |

`PricePredictor.predict()` returns `(predicted_change_pct, confidence)`. Confidence is the classifier probability aligned to the regressor's direction.

**Known bias:** The ML model is trained on historical data and tends to predict downside (negative `predicted_change_pct`) even in sustained bull markets due to bearish skew in training data. The BULLISH macro override compensates for this by forcing BUY on structure-passing pairs despite negative predictions.

### Step 4 — Performance Feedback (`bot/analysis/performance_tracker.py`)
- Computes per-symbol win rate over the last 30 days from the `trades` table
- Requires minimum 10 trades before applying adjustment
- Applies a confidence multiplier to the model's output:

| Win Rate | Multiplier |
|----------|-----------|
| < 40% | 0.90x |
| 40–55% | 0.95x |
| 55–65% | 1.00x |
| > 65% | 1.10x |

### Step 5 — Ranking (`bot/analysis/ranker.py`)
All analysed symbols are scored and ranked:

```
score = (abs(predicted_change_pct) * confidence) / max(atr_pct, 0.1)
```

- Uses absolute value of predicted change — ranker is direction-neutral; direction is determined by the executor
- Funding rate bonus: `abs(funding_rate) * 1000` added to score when trade side collects funding
- Symbols with `confidence <= 0` are excluded
- Final output: list sorted by score descending

### Step 6 — Correlation Filter (`bot/analysis/ranker.py` / executor)
- Pearson correlation matrix computed from 30-day 1h returns
- Pairs with r > 0.95 are deduplicated — only the higher-scored pair is kept
- Prevents simultaneous positions in effectively identical assets (e.g., two ETH-correlated tokens)

---

## Trade Execution Loop (`bot/trading/executor.py`)

The executor runs on a 60-second tick. Each iteration:

### 1. Read Runtime Config
- Fetches Trading Level preset from Redis → resolves confidence threshold, SL, TP, max trades, leverage, kill-switch level
- Reads current paper/live/testnet mode flags from Redis
- Reads bot:command — stops loop if set to "stop"

### 2. Daily Reset Check
- `_reset_if_new_day()` — midnight UTC resets `bot:daily_trade_count` and `bot:daily_pnl_pct` in Redis
- Does NOT reset open positions — positions survive midnight

### 3. Kill Switch Check
- If `daily_pnl_pct < -kill_switch_pct`, no new trades are opened for the rest of the day
- Existing positions continue to be monitored and closed normally

### 4. Position Monitoring (`_monitor_positions`)
For every open position in PostgreSQL:
- Fetch current price from Redis `price:{symbol}`
- Compute current unrealised PnL
- **Take-profit:** close if price reaches TP level
- **Stop-loss:** close if price reaches SL level
- **Trailing stop:** activates when profit >= 5.0% of entry, closes on 1.0% reversal from peak
- **Negative timeout:** close if position has been negative for > `negative_trade_timeout_minutes` (default 15 min)
- TP follower: on TP trigger, write Redis key `bot:tp_follower:{symbol}` using SET NX (prevents duplicate TP log spam)

### 5. Trade Switching (`_maybe_switch_trade`)
- Compares existing open positions against current top-ranked pairs
- If a better opportunity exists and the current position is underperforming, the position may be closed and replaced
- Uses level preset's confidence threshold for switch decisions

### 6. Macro Trend Calculation
- Computes mean 24h change across all tracked pairs from Redis price data
- Computes `advance_ratio` = fraction of pairs that are up
- Classifies as BULLISH / NEUTRAL / BEARISH (see strat.md)
- Result cached in Redis for 120 seconds

### 7. Dynamic Confidence Adjustment
Starting from the level preset's confidence threshold:
- If `daily_trade_count < min_daily_trades`: reduce by -0.08 (floor 0.45)
- If BULLISH macro and opening BUY: reduce by -0.10 (floor 0.40)
- If BEARISH macro and opening SELL: reduce by -0.05 (floor 0.40)
- If goal progress < 50%: reduce by -0.05

### 8. New Trade Loop
Iterates the ranked pair list top-to-bottom:
1. Skip if symbol already has an open position
2. Skip if cooldown active for symbol (Redis `bot:cooldown:{symbol}`)
3. Apply macro direction gate (BULLISH blocks SELL, BEARISH blocks BUY)
4. Apply NEUTRAL soft SELL block if advance_ratio >= 0.60
5. Apply BULLISH SELL→BUY override if pair passes bullish structure check
6. Check correlation against existing open positions
7. Call `_try_open_trade()` — opens position if confidence clears threshold
8. Stop when `max_concurrent_trades` slots are filled

---

## Opening a Trade (`_try_open_trade`)

1. **Determine side:** `forced_side` if override is active, else `_determine_side(predicted_change_pct)` (positive = BUY, negative = SELL)
2. **Regime detection:** `detect_regime(symbol)` → TRENDING / RANGING / TRANSITION + confidence multiplier
3. **Confidence check:** `effective_confidence = confidence * regime_multiplier * performance_multiplier`; skip if below threshold
4. **SELL floor check:** if SELL, skip if effective_confidence < sell_threshold
5. **Mode classification:** FUTURES if confidence >= futures_threshold AND atr_pct <= volatility_cap AND adx >= adx_threshold; else SPOT
6. **Compute TP ATR multiplier:** 0.6x (RANGING), 1.5x (forced BUY override), 1.0x (default)
7. **Position sizing:** `calculate_position_size()` — risk_pct of capital per slot
8. **SL/TP prices:** `calculate_sl_tp_prices()` — ATR-adjusted from entry price
9. **Risk check:** `can_open_trade()` — verifies open count, daily loss limits, capital available
10. **Place order:** live → `orders.py` via ccxt; paper → `paper.py` simulator
11. **Record:** `journal.py` inserts to `positions` and `trades` tables
12. **Increment counters:** Redis `bot:daily_trade_count++`, `bot:open_count` synced from DB

---

## Closing a Trade

Triggered by: TP hit, SL hit, trailing stop, negative timeout, manual close via dashboard, or trade switch.

1. Fetch current price
2. Calculate realised PnL: `(exit_price - entry_price) / entry_price * notional` (BUY), inverted for SELL
3. Live mode: place market sell order via ccxt
4. Paper mode: `paper.simulate_sell()` — returns margin + PnL to paper capital
5. `journal.close_position()` — deletes row from `positions`, updates `trades` with exit price, PnL, close reason, closed_at
6. Update Redis: `bot:daily_pnl_pct`, `bot:open_count`
7. **Loss streak:** if loss, increment Redis `bot:loss_streak:{symbol}` (TTL 2 days); apply cooldown
8. **Win:** delete `bot:loss_streak:{symbol}`, apply base 5-minute cooldown only

---

## Loss Streak Cooldown

Managed entirely in executor.py. State stored in Redis.

| Event | Cooldown Applied |
|-------|-----------------|
| Any closed trade (win or loss) | 5-minute base cooldown (`bot:cooldown:{symbol}`) |
| Loss streak >= 2 | 4-hour cooldown |
| Loss streak >= 3 | 24-hour cooldown |
| Win | Streak reset, base cooldown only |

**Gap:** Positions force-closed by a bot crash (NULL pnl_usdt, bypassing normal close path) do not increment the streak counter. The bot resuming after a crash may re-enter previously losing symbols sooner than intended.

---

## SPOT vs FUTURES Mode

Controlled by `mode_classifier.classify_mode()` per trade.

| Mode | Condition | Behaviour |
|------|-----------|-----------|
| FUTURES | conf >= futures_conf_threshold AND atr_pct <= vol_cap AND adx >= adx_threshold | Opens leveraged position; deducts margin (notional/leverage) from capital |
| SPOT | Any condition fails | Opens unleveraged buy; deducts full notional from capital |

FUTURES SELL (short) requires Binance API futures permission. Without it, SELL FUTURES falls back to skipping the trade cleanly (no fallback to SPOT SELL, since a spot sell requires holding the asset).

FUTURES BUY without futures permission falls back to SPOT BUY with quantity adjusted for leverage (`quantity / lev`).

---

## Paper Trading

When `PAPER_TRADING_MODE=true` (or Redis `bot:paper_trading_mode=1`):
- No real orders are placed — all trades go through `paper.py`
- Paper capital is tracked in Redis key `paper:capital_usdt`
- FUTURES paper: deducts `notional / leverage` (margin) on open; returns `margin + pnl` on close
- SPOT paper: deducts full notional on open; returns `notional + pnl` on close
- All trades are still persisted to PostgreSQL — paper trades look identical to live trades in the DB

Mode can be switched live via Settings page without restarting the bot. The executor reads the mode flag each loop tick.

---

## Trade Tracking

### PostgreSQL
- `positions` table: every currently open position (live or paper)
- `trades` table: every completed trade with full PnL, duration, close reason
- `predictions` table: ML output per analysis run

### Redis State
| Key | Contents |
|-----|---------|
| `bot:status` | Hash: daily_pnl_pct, open_count, daily_trade_count, mode, etc. |
| `bot:daily_trade_count` | Integer count of trades opened today |
| `bot:daily_pnl_pct` | Float daily PnL percentage |
| `bot:open_count` | Integer open position count (synced from DB each loop) |
| `bot:cooldown:{symbol}` | TTL key — symbol is in cooldown while key exists |
| `bot:loss_streak:{symbol}` | Integer loss streak count per symbol (TTL 2 days) |
| `bot:tp_follower:{symbol}` | SET NX lock — prevents TP spam per position |
| `bot:macro_trend_cache` | BULLISH / NEUTRAL / BEARISH (TTL 120s) |
| `market:sentiment` | Hash: sentiment, advance_ratio, total pairs |
| `price:{symbol}` | Latest live price per symbol |
| `paper:capital_usdt` | Paper trading capital balance |

### Dashboard
The Next.js dashboard reads all tracking data via the FastAPI endpoints:
- `/api/v1/bot/status` — real-time bot health, daily stats
- `/api/v1/positions` — open positions list
- `/api/v1/trades` — trade history with filters
- `/api/v1/stats/pnl?period=1h|24h|7d|30d|all` — P&L breakdown by period
- `/ws/prices` — live price WebSocket for the dashboard ticker

---

## Notifications

`bot/notifications/telegram.py` sends alerts to a configured Telegram bot when:
- A trade is opened
- A trade is closed (with PnL result)
- The daily loss kill switch activates
- The bot starts or stops

Telegram is optional — if credentials are not configured, notifications are silently skipped.

---

## Risk Controls Summary

| Control | Mechanism |
|---------|-----------|
| Max concurrent positions | Hard DB count check before every new trade (`can_open_trade`) |
| Daily trade cap | Redis counter checked each loop; resets at midnight UTC |
| Daily loss kill switch | Halts new trades if daily PnL < -kill_switch_pct |
| Stop-loss | Per-trade price level from SL% and ATR, monitored every loop tick |
| Negative timeout | Force-close positions that stay negative beyond timeout |
| Loss streak cooldown | Symbol-level Redis TTL escalating from 5 min to 24h |
| Correlation filter | Blocks adding positions in assets with r > 0.95 vs existing positions |
| Capital check | Verifies available paper/live capital before each order |
| FUTURES margin check | Paper mode enforces margin deduction (notional/leverage), not full notional |
