# Settings lockdown + mode-only control

**Branch:** `Features` (dual-bot: 1h manager + 15m researcher)  
**Date:** 2026-09-29  
**Status:** spec — implement on `Features`

The dashboard must stop being a second trading engine. The user picks **how** the book is allowed to express a view (spot, futures, or both). Size, count, heat, and whether a signal is allowed are locked inside the two bots.

---

## 1. What the user may still change

Exactly one trading control:

| Control | Values | Meaning |
|---|---|---|
| **Trading mode** | `SPOT` | Longs only. Cash. No leverage. |
| | `DYNAMIC` | Spot **or** futures, chosen per trade from trend strength. |
| | `FUTURES` | Levered longs and shorts only. |

Keep the existing non-strategy chrome that is not a trading parameter:

- paper vs live
- capital (paper restart)
- kill-switch reset
- system health
- notifications
- hard reset / archive

Remove or disconnect from the Settings page (and from `POST /trade-limits` for those keys):

- Trading Parameters: max concurrent, confidence, SL, TP, kill-switch %, leverage, negative-timeout
- Trade Targets: min trades / 24h, min simultaneous, max trades / 24h
- Trading Goal (the one that lowers confidence when behind)
- Futures BUY gate toggle (ML may still log it internally; user does not set it)

API still *reads* Redis so the bot can run. API must **reject writes** to the locked keys from the dashboard. Only `trading_mode` / `force_trade_mode` stay writable.

If a stale Redis value exists from the old page (`bot:min_daily_trades=50`, `bot:max_concurrent_trades=20`), first boot after this change **overwrites them with the locked defaults below**. Do not keep the user’s old floors.

---

## 2. Locked portfolio law — 40% heat

**Hard cap:** open trades together may never use more than **40% of portfolio equity**.

Heat is **cash + futures margin**, not leveraged notional.

```
equity        = paper capital or live USDT equity
heat_limit    = 0.40 * equity
open_heat     = sum(spot notional) + sum(futures margin)
               where futures margin = notional / leverage
free_heat     = heat_limit - open_heat
```

A new order is illegal if `free_heat` cannot pay its size. This beats “max concurrent = 20” and “min 50 trades/day”. Those two settings are how the book used to exceed any sensible fraction of capital.

Single-name cap stays inside the 40%:

```
max_one_symbol_heat = 0.10 * equity     # 10% of book, 25% of the 40% sleeve
```

Never bump a position up to a Binance minimum if that bump would push `open_heat` over 40%. Skip the symbol instead.

---

## 3. Ideal max daily trades (locked)

The daily cap is not a target. It is a ceiling that also sizes the book.

### Why not 50–200

On this stack a round trip is ~0.08–0.18% in fees + slippage. Fifty trades/day is several percent of equity in friction before the 1h model is even right. The dual-bot only gets a clean decision on **1h close + 15m arm**. That is at most one *new* idea per symbol per hour, and most hours will be `skip`.

### Locked numbers

| Mode | Max new trades / UTC day | Max simultaneous | Per-trade heat |
|---|---|---|---|
| `SPOT` | **12** | **3** | `min(free_heat, 0.40/3 * equity)` ≈ 13.3% if the book is empty |
| `DYNAMIC` | **16** | **4** | `min(free_heat, 0.40/4 * equity)` = 10% |
| `FUTURES` | **16** | **4** | same 10% sleeve, expressed as margin |

`max_simultaneous` is **derived**, not a separate Settings slider:

```
max_simultaneous(mode) = 3 if mode == SPOT else 4
per_trade_heat         = heat_limit / max_simultaneous
max_daily_trades       = 12 if mode == SPOT else 16
```

The daily cap and the simultaneous cap share the same 40% sleeve:

1. Cannot open if `daily_count >= max_daily_trades`.
2. Cannot open if `open_count >= max_simultaneous`.
3. Cannot open if `per_trade_heat > free_heat` (the book is already using the 40%).
4. Size of the new trade = `min(per_trade_heat, free_heat, max_one_symbol_heat remaining on that coin)`.

So “max daily trades” is not an independent knob. It is the daily budget on a book that is only allowed to run 40% hot. If three futures seats are full at 10% each (30% heat), a fourth may open at 10%. A fifth may not, even if the daily count is 4/16.

If equity is too small to put `per_trade_heat` above the exchange minimum (`$10` spot / `$35` futures notional), drop `max_simultaneous` until each seat clears the minimum **or** skip trading that mode. Do not silently exceed 40% to hit Binance mins.

Worked example, `$100` paper, `DYNAMIC`, 2x futures:

- Heat limit = `$40` margin/cash  
- 4 seats × `$10` margin  
- Futures notional per seat = `$20` at 2x  
- Daily ceiling = 16 entries  
- If average hold is 3 hours, expect **6–12** fills, not 16. 16 is the brake, not the goal.

There is **no min daily trades** and **no min simultaneous**. Behind-pace confidence cuts are deleted.

---

## 4. Other internals the user no longer sets

These become constants in code (Redis may mirror them for the API status payload, read-only):

| Param | Locked value | Why |
|---|---|---|
| `analysis_timeframe` | `1h` | Manager clock |
| `research_timeframe` | `15m` | Researcher clock |
| `require_15m_confirm` | `true` | Dual-bot gate |
| `research_min_confidence` | `0.55` | Packet arm floor |
| `confidence_threshold` (1h model) | `0.70` | Do not let targets drag this to 0.45 |
| `futures_leverage` | `2` | Matches Experiments / strat.md |
| `stop_loss_percent` | `2.0` effective, still hard-capped at **2% effective loss** already in the executor |
| `take_profit_percent` | `3.0` (ATR stretch may stay inside 2.5–6% as today) |
| `daily_loss_limit_percent` | `10` of equity in **USDT**, then defensive / kill |
| `negative_trade_timeout_minutes` | `60` | Features already uses this |
| `max_risk_per_trade_percent` | unused — replaced by `per_trade_heat` |
| `min_daily_trades` | **0 / removed** | |
| `min_concurrent_trades` | **0 / removed** | |
| Goal confidence taper | **removed** | |

Paper remains default. Live is still a separate, explicit switch — not a trading-parameter slider.

---

## 5. Trend alignment (manager prediction vs market trend)

Two views must agree before an order. Mode only decides **venue and side legality**, not whether the idea exists.

### 5.1 The two views

| View | Source | Values |
|---|---|---|
| **Bot prediction** | Manager 1h model (`predicted_change_pct`, `confidence`) | `BULL` if predicted change > 0 and confidence ≥ 0.70, else `BEAR` if predicted change < 0 and confidence ≥ 0.70, else `NONE` |
| **Market trend** | 1h EMA21 vs EMA50 **and** researcher 15m packet | `BULL` / `BEAR` / `NONE` |

Market trend:

```
1h_trend     = BULL if EMA21 > EMA50 else BEAR
packet_side  = packet.pred_1h_close_side   # bull | bear
packet_ok    = packet.arm and not packet.invalidation_hit
               and packet.confidence >= 0.55

market_trend = 1h_trend if packet_ok and packet_side agrees with 1h_trend
               else NONE
```

If 1h EMAs and the 15m packet disagree → `market_trend = NONE` → **no new trade**. That is the dual-bot skip.

Regime `RANGING` (existing detector) also forces `market_trend = NONE` unless mode is `SPOT` and the 1h prediction is `BULL` with packet arm — spot may still buy a reclaim. Futures do not fade a range.

### 5.2 Alignment matrix

A trade exists only when `bot_prediction == market_trend` and that value is not `NONE`.

Then mode filters venue + side:

| User mode | Bot = market = BULL | Bot = market = BEAR | Disagree / NONE |
|---|---|---|---|
| `SPOT` | Spot **BUY** only | **Skip** (no spot short as a strategy) | Skip |
| `FUTURES` | Futures **BUY** (long) | Futures **SELL** (short) | Skip |
| `DYNAMIC` | Futures long if 1h ADX ≥ 25 **and** packet ADX ≥ 18, else spot buy | Futures short if the same ADX test passes, else **skip** (do not spot-sell into a bear unless inventory exists to flatten) | Skip |

DYNAMIC rule in one line: **strong aligned trend → futures; weak aligned bull → spot; aligned bear without strength → skip.**

Researcher still never places the order. It only publishes the packet the manager uses for `market_trend`.

### 5.3 Existing Features bits this replaces

- Mean-reversion override that flips side inside a range: **off** for new entries (it fights alignment).
- Extreme funding override that flips side: **off** for new entries. Funding may still block futures (existing `should_avoid_futures`) but must not invert the aligned side.
- “Below min daily → confidence 0.45”: **delete**.
- “Goal behind → confidence −0.05”: **delete**.
- `_maybe_switch_trade` churn: only allowed if the replacement pair is aligned and the victim is the worst open **and** free heat is 0 (need a seat). Do not switch just because a raw 1h score is higher.

---

## 6. How the two bots use mode

```
Researcher (15m)
  - ignores user mode
  - writes packet: side, arm, invalidation, confidence
  - scores the closed 1h
  - may tighten require_15m_confirm / research_min_confidence
  - never reads Trade Targets (they are gone)

Manager (1h)
  - reads user mode
  - computes bot_prediction from 1h model
  - computes market_trend from 1h EMAs + packet
  - if not aligned → skip
  - if aligned → pick venue/side from the matrix above
  - size from free_heat / seats
  - refuse if daily cap or 40% heat would break
  - SL/TP/2% effective cap unchanged
```

If the user switches mode while positions are open:

- Do not force-close immediately.
- New entries follow the new mode.
- Existing futures stays until SL/TP/invalidation/timeout.
- Optional later: flatten futures if user switches to `SPOT` and the position is futures. v1 logs a warning only.

---

## 7. Files to change

| File | Change |
|---|---|
| `frontend/app/settings/page.tsx` | Delete Trading Parameters sliders, Trade Targets card, Trading Goal editor. Leave ModeToggle as the only strategy control. |
| `frontend/lib/types.ts` | `TradeLimits` shrinks to `{ trading_mode }`. |
| `frontend/components/StartupModal.tsx` | Stop listing confidence / min daily / leverage as user-confirmable settings. Show mode + “max 40% heat / 16 trades”. |
| `api/main.py` | `POST` trade-limits accepts **only** `trading_mode`. Other keys 400. On startup, write locked Redis defaults. |
| `bot/config.py` | Defaults from §3–§4. Remove reliance on min daily / min concurrent. |
| `bot/trading/risk.py` | `can_open_trade` checks heat + daily cap + simultaneous derived from mode. Delete confidence taper inputs. |
| `bot/trading/sizing.py` | Size = `per_trade_heat` / `free_heat`, not `max_risk_per_trade_percent`. |
| `bot/trading/executor.py` | Alignment matrix; delete min-daily / goal confidence cuts; pass mode into gate + sizing. |
| `bot/manager/gate.py` | Require `bot_prediction == market_trend` in addition to packet arm. |
| `bot/research/*` | No change to “never trade”. Packet still required for `market_trend`. |

---

## 8. Redis after lockdown

Writable by the user (via API):

```
bot:trading_mode          # SPOT | DYNAMIC | FUTURES
bot:force_trade_mode      # keep in sync with trading_mode
```

Written by the bot, read-only to the UI:

```
bot:heat_limit_pct        40
bot:max_daily_trades      12 or 16
bot:max_concurrent_trades 3 or 4
bot:open_heat_usdt
bot:free_heat_usdt
```

Delete or ignore on boot:

```
bot:min_daily_trades
bot:min_concurrent_trades
bot:goal                  # no longer drives entries
bot:confidence_threshold  # reset to 0.70
bot:futures_leverage      # reset to 2
```

---

## 9. Acceptance

- Settings page has no sliders for size, count, SL, TP, confidence, goal, or floors.
- Switching Spot / Dynamic / Futures is the only strategy click.
- With `$100` equity, open margin+spot cash never prints above `$40`.
- A fifth simultaneous DYNAMIC/FUTURES seat is refused even at 0 daily trades.
- 17th fill in a UTC day is refused.
- Bearish 1h model + bullish 15m packet → no order in any mode.
- Bullish both + `SPOT` → spot buy only.
- Bullish both + strong ADX + `DYNAMIC` → futures long.
- Bearish both + `SPOT` → skip.
- No log line that says “lowering confidence to 0.45”.

---

## 10. What this is not

- Not a second Settings page “for advanced users”.
- Not researcher-placed orders.
- Not raising leverage when DYNAMIC is selected.
- Not a 40% *notional* cap on 2x futures (that would be 80% market exposure in name only; heat is margin).
- Not keeping min-50 to “stay active”. Activity is a side effect of aligned hours, not a target.
