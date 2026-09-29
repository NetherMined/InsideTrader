# InsideTrader — Trading Strategy Reference

## Overview

The bot operates three distinct macro-driven strategies, automatically switching between them based on real-time market conditions. Every 60-second executor loop, the macro trend is recomputed from price data across all 67 tracked USDT pairs and classified as **BULLISH**, **NEUTRAL**, or **BEARISH**. This classification gates which trade directions are allowed, adjusts confidence thresholds, and controls take-profit sizing.

---

## Macro Trend Classification

**Source:** `bot/trading/executor.py` — `_get_macro_trend()`
**Data:** 24h price changes across all tracked pairs from Redis `price:*` keys
**Cache TTL:** 120 seconds

| Regime | Condition |
|--------|-----------|
| BULLISH | mean 24h change > +0.3% AND advance_ratio > 55% of pairs rising |
| BEARISH | mean 24h change < -0.3% AND advance_ratio < 45% of pairs rising |
| NEUTRAL | all other conditions (moderate move or mixed breadth) |

---

## Strategy 1 — BULLISH

**Trigger:** macro_trend == "BULLISH"

### Direction Gate
- All SELL-side pairs are evaluated for a **SELL→BUY override** before being skipped
- Pairs that fail the bullish structure check are discarded — no shorts are opened in a bull market

### Bullish Structure Override (SELL → BUY)
When a pair is ML-predicted as SELL but macro is BULLISH, it passes a three-part structure check:

| Check | Condition | Meaning |
|-------|-----------|---------|
| EMA21 proximity | `ema21_ratio >= -0.03` | Price no more than 3% below EMA21 |
| Not overbought | `rsi < 72` | RSI below overbought territory |
| Trend momentum | `adx > 10` | Some directional strength present |

If all three pass, the pair is **forced to BUY** regardless of ML prediction. Pairs that fail any check are skipped entirely.

### Entry Patterns (logged on each override trade)
| Pattern | Condition |
|---------|-----------|
| pullback-long | bb_pct <= 0.35 AND rsi <= 55 — price at lower BB, RSI not extended |
| trend-follow-long | adx >= 20 AND ema21_ratio >= 0.01 — strong trend, price above EMA21 |
| breakout-long | bb_pct >= 0.70 — price at upper BB, momentum breakout |
| bullish-long | fallback for all other passing pairs |

### Confidence Thresholds
- Base confidence adjustment: `adjusted_confidence - 0.10` (floor: 0.40)
- Trend-aligned floor: `trend_floor = max(0.45, adjusted_confidence - 0.15)`
- Sell threshold is held at full `adjusted_confidence` — effectively blocking shorts
- Additional -0.08 reduction if below min_daily_trades target (floor 0.45)
- Additional -0.05 reduction if goal progress below 50% of target

### Take-Profit Sizing
| Trade Type | ATR Multiplier |
|------------|---------------|
| Forced BUY (SELL→BUY override) | 1.5x ATR |
| Standard BUY (ML-confirmed) | 1.0x ATR |
| RANGING regime trade | 0.6x ATR |

### Stop-Loss
Controlled by Trading Level preset (see below). Defaults: SL = 1.5–3%, TP = 2–5% of entry price.

### Trade Mode
Mode classifier applies normally: FUTURES BUY if confidence, ATR, and ADX all pass thresholds. Falls back to SPOT BUY if any condition fails. No short (SELL FUTURES) positions are opened in BULLISH macro.

### Summary
The BULLISH strategy is an aggressive long-only mode. The bot opens BUY positions on ML-confirmed longs and additionally overrides ML-bearish signals when the underlying price structure is still bullish. Wider TP targets (1.5x ATR) allow winners to run in strong uptrends. The confidence floor is significantly lowered to maximize trade count during favorable conditions.

---

## Strategy 2 — NEUTRAL

**Trigger:** macro_trend == "NEUTRAL"

### Direction Gate
- Both BUY and SELL pairs are allowed by default
- **Soft SELL block:** if advance_ratio >= 0.60 (60%+ of pairs advancing), SELL-side pairs are additionally blocked to avoid shorting a broadly advancing market even in NEUTRAL classification

### Entry Conditions
- No directional confidence discount applied — full `adjusted_confidence` is used as the threshold
- SELL threshold: `params["confidence_threshold"]` (no adjustment)
- BUY threshold: `params["confidence_threshold"]` (no adjustment)

### Regime-Specific Behavior
Within NEUTRAL macro, individual symbol regimes still apply:

| Symbol Regime | Behavior |
|---------------|----------|
| TRENDING (ADX >= 25) | Full ATR TP, confidence multiplier 1.0x, standard entry |
| RANGING (ADX < 20, low BB width) | Tight ATR TP (0.6x), confidence multiplier 0.8x, mean-reversion entry style |
| TRANSITION (ADX 20–25) | Conservative multiplier 0.85x, confidence threshold raised +0.05 |
| High volatility (ATR > threshold, non-TRENDING) | Confidence cap at 0.6x regardless of regime |

### Mean-Reversion Trades (RANGING symbols)
When a symbol is classified RANGING and a mean-reversion signal is present (MR signal strength >= 0.5), the bot may override ML direction to trade against the recent move — buying oversold dips or selling overbought peaks. These trades use 0.6x ATR take-profit to cycle profits quickly.

### Funding Rate Arbitrage
When a futures funding rate >= 0.05% per 8h is detected, the bot may override ML direction to collect the funding payment. This applies in NEUTRAL macro where both directions are open. Longs collect when funding is positive (market is paying longs); shorts collect when funding is negative.

### Take-Profit Sizing
| Regime | ATR Multiplier |
|--------|---------------|
| TRENDING | 1.0x ATR |
| RANGING | 0.6x ATR |
| TRANSITION | 1.0x ATR |

### Summary
The NEUTRAL strategy is balanced and regime-aware. It allows both long and short entries but applies individual symbol analysis (regime detection, mean-reversion signals, funding arb) to select the optimal direction and sizing. It is the most rules-driven mode, deferring fully to ML model direction and symbol-level regime classification.

---

## Strategy 3 — BEARISH

**Trigger:** macro_trend == "BEARISH"

### Direction Gate
- All BUY-side pairs are blocked — no long positions are opened in a bear market
- Only SELL-side pairs (ML-predicted negative change) are eligible

### Entry Conditions
- Sell threshold is **softened**: `sell_threshold = max(0.40, adjusted_confidence - 0.05)`
- This lowers the bar for short entries by 5 percentage points (floor 0.40), making the bot more willing to open shorts when the market is broadly falling
- Standard confidence threshold applies as the BUY floor (but BUY is blocked, so this is irrelevant)

### Trade Mode
- FUTURES SELL (short) allowed if Binance API has futures permissions and mode classifier passes (confidence, ATR, ADX thresholds met)
- SPOT SELL is a sell/close of held positions — not a fresh short
- No forced BUY overrides apply

### Confidence Thresholds
- `adjusted_confidence - 0.05` for sell floor (floor: 0.40)
- No bullish floor discounts apply
- Additional -0.08 reduction if below min_daily_trades target
- Additional -0.05 reduction if goal progress below 50%

### Take-Profit Sizing
| Regime | ATR Multiplier |
|--------|---------------|
| TRENDING | 1.0x ATR |
| RANGING | 0.6x ATR |

### Loss Streak Protection
The loss streak cooldown (bot/trading/executor.py) applies equally in all macro regimes:
- Any closed losing trade: 5-minute base cooldown per symbol
- Loss streak >= 2: 4-hour cooldown
- Loss streak >= 3: 24-hour cooldown
- Win: streak counter reset

### Summary
The BEARISH strategy is a short-only mode. The bot opens SELL/FUTURES SELL positions on ML-predicted downside setups. The confidence bar for shorts is slightly lowered to increase trade frequency during broad market declines. All long entries are blocked regardless of individual symbol signals.

---

## Trading Level Presets

Applied at all three macro strategies. Level is set via Settings page and stored in Redis (`bot:trading_level`).

| Level | Mode | Confidence | SL | TP | Max Trades | Kill Switch |
|-------|------|------------|----|----|------------|-------------|
| CONSERVATIVE | SPOT only | >= 0.80 | 1.5% | 2% | 2 | -5% |
| BALANCED (default) | DYNAMIC 2x lev | >= 0.75 | 2% | 3% | 3 | -10% |
| AGGRESSIVE | DYNAMIC 3x lev | >= 0.65 | 3% | 5% | 5 | -15% |

Level presets override the base confidence threshold but are themselves further adjusted by the macro trend discounts described above.

---

## Dynamic Confidence Adjustments (Stacking)

All adjustments are additive. Applied in order:

1. **Base threshold** — from Trading Level preset (0.65–0.80)
2. **Below min_daily_trades** — `-0.08` (floor 0.45)
3. **Below min_concurrent_trades** — small additional reduction
4. **BULLISH macro (BUY)** — `-0.10` (floor 0.40)
5. **BEARISH macro (SELL)** — `-0.05` (floor 0.40)
6. **Goal pacing below 50%** — `-0.05`
7. **Trend-aligned floor** — `max(0.45, adjusted - 0.15)` for BULLISH/BEARISH direction-aligned trades
8. **Regime multiplier** — applied by regime_detector (0.6–1.0x on final confidence)
9. **Performance multiplier** — per-symbol win-rate feedback (0.90–1.10x)

Maximum possible discount: up to -0.28 from base threshold before regime and performance multipliers.

---

## Position Sizing and Risk

- **Risk per trade:** `100 / min_daily_trades` % of capital (equal share per slot)
- **Position value:** risk% of available capital per slot
- **FUTURES margin:** position value / leverage (e.g., 5x leverage = 20% margin of notional)
- **Stop-loss:** percentage from entry, set by Trading Level preset
- **Take-profit:** ATR-based multiplied by regime and macro multiplier
- **Daily loss kill switch:** halts all new trades for the day if daily PnL falls below kill threshold
- **Max concurrent trades:** hard limit from Trading Level or config (checked against open DB positions each loop)

---

## Pair Selection and Correlation Filtering

1. Scanner identifies ~67 valid USDT spot/futures pairs on Binance
2. Pairs are scored by ranker: `score = abs(predicted_change_pct * confidence) / max(atr_pct, 0.1)`
3. Funding rate bonus added: `abs(funding_rate) * 1000` when trade side collects funding
4. Correlation filter removes pairs with Pearson r > 0.95 (30-day 1h returns) to avoid over-exposure to correlated assets
5. Remaining candidates are passed to the macro gate for directional filtering
6. Final ranked list is iterated top-to-bottom until `max_concurrent_trades` slots are filled
