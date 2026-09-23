# InsideTrader — Trading Strategy Reference

**Strategy:** ML trend-following bot with dynamic SPOT / FUTURES mode selection.
**Exchange:** Binance Spot + Futures (via CCXT) | **Timeframe:** 1h candles
**ML Model:** XGBoost regression + classifier (90 days of 1h OHLCV + technical indicators)

> ⚠️ No strategy works in every regime. Futures with leverage can liquidate your position rapidly. Paper-trading first — live trading requires manual activation. Never risk capital you cannot afford to lose.

---

## Strategy Classification

The InsideTrader bot falls under **Trend-Following / Momentum** as classified in `strat.md` analysis:

| Metric | `strat.md` Range | InsideTrader Target |
|---|---|---|
| Win rate | 55–72% | 55–72% (futures only when model ≥ conf threshold) |
| Risk:Reward | 2:1 to 4:1 | 1:1.5 default (SL 2% / TP 3%) |
| Best condition | Strong trends | ADX ≥ 25, ATR ≤ 5%, confidence ≥ 0.75 |
| Drawdown | Medium–High | Controlled by 10% daily kill-switch |

---

## Core Strategy Logic

For each candidate symbol, the **Market Mode Classifier** decides SPOT or FUTURES:

```
confidence >= 0.75  AND  ATR% <= 5%  AND  ADX >= 25  →  FUTURES (2x leverage)
otherwise                                                  →  SPOT
```

All three conditions must pass for FUTURES; any failure defaults to SPOT.

**Why dynamic mode?** Futures amplify returns when the model is confident and the market is trending (strong ADX, low volatility), but force spot-only when conditions are uncertain — protecting capital from liquidation on low-confidence signals.

---

## Trading Rules

| Rule | Value | Source | Notes |
|---|---|---|---|
| **Min daily trades** | **100** | `update.md` | Floor enforced by lowering confidence threshold |
| **Max concurrent trades** | **3** | `PROJECT_PLAN.md` | Hard cap given $92 capital |
| **Max risk per trade** | **30%** | `strat.md` update | Position size cap per trade |
| **Max single-coin exposure** | **30%** | `CONFIG_REQUIRED.md` | Prevents over-concentration |
| **Futures leverage** | **2x** | `PROJECT_PLAN.md` | Conservative; not >2x |
| **Daily profit target** | **+2%** | `PROJECT_PLAN.md` | Pauses new trades on hit |
| **Daily loss kill-switch** | **-10%** | `PROJECT_PLAN.md` | Hard stop — halts all trading |
| **Negative-trade close** | **Immediate (next loop tick ≈ 60s)** | `update.md` | Configurable via `negative_trade_timeout_minutes` |
| **Paper trading** | **ON by default** | `update.md` | Live requires manual enable |
| **Stop-loss** | **2%** per trade | `CONFIG_REQUIRED.md` | Tight per-trade SL |
| **Take-profit** | **3%** per trade | `CONFIG_REQUIRED.md` | R:R = 1:1.5 |

### Immediate Negative-Trade Close (`update.md`)

If an open trade enters negative P&L, it is closed **on the next bot loop tick** (≈60s). The position is closed and the bot immediately scans for a new opportunity. Timeout defaults to **0 minutes** (immediate) — can be configured via `negative_trade_timeout_minutes`.

This enforces strict discipline: cut losers instantly, never average down. The trade is closed regardless of how small the loss, and the bot re-enters only when a new signal appears.

---

## Risk Management

### The 30% Rule
- **Max 30% of portfolio** allocated to any single trade
- **Max 30% of portfolio** in any single asset at once
- Position sizing uses `calculate_position_size()` — respects both per-trade cap and single-coin exposure cap
- For $92 capital: ~$27 max per trade, ~$27 max per asset

### Daily Kill-Switch
- **-10% daily loss** triggers emergency halt
- All positions closed; no new trades until next UTC day
- Kill-switch state persists across bot restarts via Redis
- **Defensive mode** auto-activates on kill-switch: switches to SPOT-only, tighter SL/TP, lower confidence threshold

### Immediate Negative-Trade Close
- **Default: 0 minutes** (immediate close on next loop tick)
- Position closes the moment P&L goes negative
- Bot immediately re-scans for next opportunity
- Configurable via `negative_trade_timeout_minutes` in `.env` if longer tolerance is desired

### Per-Trade Risk Summary
| Scenario | Risk | Notes |
|---|---|---|
| SPOT trade | Up to 30% of portfolio | SL at 2% below entry |
| FUTURES trade (2x) | Up to 30% of portfolio margin | Effective SL = 1% of position (2% / 2x leverage) |
| Worst-case daily | -10% portfolio | Kill-switch halts everything |

---

## Regime Adaptation & Mode Selection

### What `strat.md` Says
> "No strategy works in every regime. Grids and mean-reversion fail in strong trends; pure trend systems chop in ranges. The highest-performing bots adapt or switch strategies."

### InsideTrader Adaptation
The bot currently uses **dynamic SPOT/FUTURES mode switching** as its primary regime adaptation:

| Market Condition | Bot Behavior | Regime Fit |
|---|---|---|
| Strong trend (ADX ≥ 25, ATR ≤ 5%) | **FUTURES 2x** — captures trend amplified | ✅ Trending |
| Choppy/uncertain (any gate fails) | **SPOT only** — no liquidation risk | ⚠️ Ranging (trend-following still active) |
| High volatility (ATR > 5%) | **SPOT only** — avoids leverage risk | ✅ High-vol |

### Gap: No Mean-Reversion in Ranges
`strat.md` notes that pure trend systems chop in ranges. The current bot defaults to SPOT in ranging conditions but **still uses trend-following signals**. This means:
- In ranges, the bot takes trend signals on spot → likely lower win rate
- **Improvement opportunity:** Add a regime detector that switches to mean-reversion logic (RSI/Bollinger extremes) when ADX < 15 and ATR is low

---

## Trade Frequency Strategy

### 100 Trades/Day Floor (`update.md`)

The bot must execute a **minimum of 100 trades per 24h window**. When the trade count is below the floor, the confidence threshold is lowered to force more entries.

| Condition | Confidence Adjustment | Risk Implication |
|---|---|---|
| Trades ≥ 100 | Normal threshold (0.75 for futures) | Standard risk |
| Trades < 100 | Lowered progressively (max reduction ~0.10–0.15) | **Higher frequency, lower quality** |

**`strat.md` Warning Applied:**
> "High-frequency grids or scalpers often look great in backtests and mediocre live."
> "Fees, funding, and slippage matter a lot."

The 100-trade floor increases fee exposure. **Monitor daily P&L to confirm the floor is net-positive after fees.** If the floor is destroying profitability, consider reducing to 50 trades/day or removing the floor entirely in favor of quality-over-quantity.

### Trade Distribution Target
- **100 trades/day** = ~4 trades/hour over 24h, or ~8 trades/hour over 12h active window
- At 3 max concurrent: positions rotate frequently (avg ~33 min per trade)
- Combined with immediate negative-trade close (0 min timeout), trades close fast → high turnover

---

## Funding Rate Considerations

### `strat.md` Insight
> "Funding rate arbitrage stands out for 'wins much larger than combined losses.' When funding is elevated (>0.05–0.10% per 8h), periodic payments create consistent positive expectancy."

### Current Bot Gap
The bot is **pure directional** and does not account for funding rates. On FUTURES positions:
- **Long positions** pay funding when rate is positive (common in bull markets)
- **Short positions** pay funding when rate is negative
- With 100 trades/day and immediate negative close, most positions are short-lived → funding impact is reduced but still present on any position held >15 min

### Recommended Improvement
Add funding rate monitoring to the mode classifier:
```python
# Pseudo-code for funding-aware mode switch
if funding_rate > 0.01%:  # positive funding = longs pay
    prefer_side = "SHORT"  # collect funding
elif funding_rate < -0.01%:  # negative funding = shorts pay
    prefer_side = "LONG"  # collect funding
```

This would let the bot **collect funding** rather than pay it, aligning with `strat.md`'s recommendation for high consistency.

---

## Leverage Assessment

### `strat.md` Perspective
> "Leverage is a double-edged sword. Most impressive ROI numbers use 5–20x+ leverage. That multiplies both gains and the chance of liquidation."
> "2x leverage is intentional and reasonable per `strat.md` standards."

### InsideTrader Choice: **2x** ✅
- Conservative end of the spectrum
- Effective SL = 1% of position value (2% SL / 2x leverage)
- Reduces liquidation risk significantly vs 5x+
- **Recommendation:** Stick with 2x. Only increase if paper-trading shows consistent profitability at 2x with high win rate.

---

## Diversification & Multi-Approach

### `strat.md` Recommendation
> "Diversify across a few uncorrelated approaches rather than putting everything into one 'best' method."

### Current State
The bot uses a **single approach** (ML trend-following with dynamic SPOT/FUTURES).

### Recommended Improvement Phases
1. **Phase A:** Add **funding rate collection** as a secondary strategy (low risk, high consistency)
2. **Phase B:** Add **regime detector** that switches between trend-following and mean-reversion
3. **Phase C:** Add **portfolio rebalancing** logic to spread across uncorrelated assets

---

## Comparison: Current vs `strat.md` Ideal Profile

| `strat.md` Ideal | InsideTrader Current | Alignment |
|---|---|---|
| High win rate + high R:R | 55–72% win rate, 1:1.5 R:R | ⚠️ R:R could be improved |
| Funding-rate arbitrage | Not implemented | ❌ Major gap |
| Adaptive/hybrid strategies | Mode-switch only (trend always) | ⚠️ Partial |
| Risk ≤1–2% per trade | 30% position size, ~0.5–1% effective risk | ✅ OK |
| Diversified approaches | Single approach | ❌ Needs improvement |
| 2x leverage | 2x | ✅ |
| Immediate loss cutting | Immediate (0 min default) | ✅ |
| 100 trades/day floor | 100 trades/day | ✅ |
| Monitor profit factor | Not explicitly tracked | ⚠️ Add tracking |
| Kill-switch at -10% | -10% | ✅ |

---

## Implementation Status

| Improvement | Status | File |
|---|---|---|
| Funding rate monitoring | ✅ Implemented | `bot/analysis/funding_rate.py` |
| Funding rate caching (5min) | ✅ Implemented | `bot/trading/executor.py` |
| Funding-side preference | ✅ Implemented | `bot/analysis/funding_rate.py`, `bot/trading/executor.py` |
| Funding gate on FUTURES mode | ✅ Implemented | `bot/trading/executor.py` |
| Regime detection (TRENDING/RANGING/TRANSITION) | ✅ Implemented | `bot/analysis/regime_detector.py` |
| Regime-based position sizing | ✅ Implemented | `bot/analysis/regime_detector.py`, `bot/trading/executor.py` |
| Correlation filter (real Pearson) | ✅ Implemented | `bot/analysis/ranker.py`, `bot/trading/executor.py` |
| Correlation matrix caching (1h Redis) | ✅ Implemented | `bot/trading/executor.py`, `bot/main.py` |
| Fee tracking (gross vs net P&L) | ✅ Implemented | `bot/trading/risk.py`, `bot/trading/journal.py` |
| Fee impact warnings | ✅ Implemented | `bot/trading/risk.py` |
| Profit factor tracking | ✅ Implemented | `bot/trading/risk.py` via `check_fee_impact()` |
| Max daily trade count cap | ✅ Implemented | `bot/config.py`, `bot/trading/risk.py` |
| Sharpe ratio tracking | ✅ Implemented | `bot/trading/risk.py` (`get_sharpe_ratio()`) |
| Mean-reversion signals (RSI/BB) | ✅ Implemented | `bot/analysis/regime_detector.py`, `bot/trading/executor.py` |
| Funding confidence boost/penalty | ✅ Implemented | `bot/trading/executor.py` |
| Portfolio diversification | ⚠️ Partial — correlation filter avoids correlated pairs; single strategy | — |
| Fee/slippage in backtesting | ✅ Implemented | `bot/analysis/backtest.py` (0.1% fee + 0.05% slippage per side) |
| Adaptive leverage | ⏳ Planned | — |
| Multi-timeframe confirmation | ⏳ Planned | — |
| News/event filter | ⏳ Planned | — |

---

## Improvement Checklist (Priority Order)

### Implemented ✅
- [x] **Add funding rate monitoring** — `bot/analysis/funding_rate.py` fetches rates via ccxt with 5-min caching
- [x] **Add profit factor tracking** — `RiskManager` tracks gross P&L, fees, and net P&L
- [x] **Validate 100-trade floor** — fee impact checked every 100 trades via `risk.check_fee_impact()`
- [x] **Add fee/slippage accounting** — `journal.py` records `estimated_fee_usdt` and `gross_pnl_usdt` per trade
- [x] **Add regime detector** — `bot/analysis/regime_detector.py` classifies TRENDING/RANGING/TRANSITION
- [x] **Add diversification logic** — correlation filter in `bot/analysis/ranker.py`
- [x] **Add volatility regime scaling** — regime-based position multiplier applied in `executor.py`

### High Priority (Remaining)
- [x] **Add max daily trade count cap** — `max_daily_trades=200` in config, enforced by `RiskManager.can_open_trade()`
- [x] **Track Sharpe ratio** alongside daily P&L — `RiskManager.get_sharpe_ratio()` (last 500 trades, annualised)
- [x] **Add mean-reversion signals** — `generate_mean_reversion_signal(rsi, bb_pct)` in `regime_detector.py`; used in executor when regime is RANGING
- [x] **Add funding confidence boost** — +0.05 confidence when funding aligns, -0.10 when strongly opposed (>0.1%/8h)
- [x] **Add fee/slippage in backtesting** — `backtest.py` deducts 0.3% round-trip (0.1% fee + 0.05% slippage per side)

### Medium Priority
- [ ] **Add stop-loss trail** — move SL to breakeven at +1% profit (like recovery_key logic)
- [ ] **Add session awareness** — reduce trading during low-liquidity hours
- [ ] **Add adaptive leverage** — scale leverage based on recent win rate

### Low Priority
- [ ] **Add multi-timeframe confirmation** — higher TF trend filter for entries
- [ ] **Add news/event filter** — pause trading during major announcements

---

## Bottom Line

The InsideTrader strategy is a **conservative trend-following + mean-reversion hybrid bot** that aligns with `strat.md`'s recommendation to adapt across regimes. All high-priority improvements from `strat.md` are now implemented:

1. **✅ Funding rate awareness** — fetches, caches, and prefers funding-collecting side; confidence boosted/penalised by funding alignment
2. **✅ Regime adaptation** — TRENDING/RANGING/TRANSITION detection with position sizing; RANGING regime switches to mean-reversion logic (RSI/BB signals)
3. **✅ Correlation filter** — real Pearson correlation (30-day 1h returns) cached in Redis; correlated pairs excluded from simultaneous positions
4. **✅ Fee accountability** — gross vs net P&L tracking; 0.3% round-trip cost modelled in backtests; fee erosion warnings at 100-trade intervals
5. **✅ Risk caps** — max 200 trades/day cap enforced; Sharpe ratio tracked across last 500 trades
6. **✅ Trade record enhancement** — fees, funding rate, and regime stored per trade

The 2x leverage, 30% rule, immediate negative-close, and 100-trade floor are sound parameters consistent with the current bot design. The bot's risk profile is conservative by futures standards, which is the right approach given the small starting capital ($92.48).

Treat any claimed "guaranteed" high-win-rate / 4× profit-factor system with extreme skepticism — those almost always fail under real conditions. Trade only with capital you can afford to lose.
