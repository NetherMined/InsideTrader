# InsideTrader day-trading update

Branch reviewed: `Features` @ `1413cc4` (7 Oct 2026).
Evidence: `trades_history.csv` (1,758 rows, all paper) and `researcher_history.csv` (4,880 packets).

This is the change set required before the bot can be a profitable day trader. Do not stack another threshold cut on top of the current book. Apply in the order below, still on paper, and re-export the CSVs after a few hundred closed trades.

## What the book actually says

| Window | Closed | Wins | Losses | Win $ | Loss $ | Net |
|---|---:|---:|---:|---:|---:|---:|
| All history (24 Sep–7 Oct) | 1,755 | 564 | 1,111 | +1,032 | −1,210 | −178 |
| Non-archived, researcher era (5–7 Oct) | 323 | 74 | 249 | +231 | −212 | +19 |

- Profit factor on full history is 0.85. Expectancy is about −$0.11 per trade.
- Recent win rate is 23%. Losses outnumber wins about 3.4 to 1. Wins are only slightly larger in dollars, so the +$19 is noise.
- Round-trip taker fee is 0.08% of notional before slippage. At 3x that is a real cost on every scratch trade.
- `negative_timeout` is 194 of 323 recent closes and −$100. `stop_loss` is 54 closes and −$113. Take-profit, max-hold, and trail carry the book.
- Researcher packets are exchanged (92% of recent trades had one; 96% agreed) but confidence is 0.85 on 4,694 of 4,880 rows and `arm=true` on every row. Agreement did not raise win rate (21.6% agreed vs 23% disagreed).

Break-even at a 23% win rate needs winners at least 3.5 times the size of losers. A 0.5% take-profit does not clear fees plus the 15-minute timeout.

## 1. Entry floor: do not take trades that cannot pay the fee

File: `bot/trading/executor.py`

Current:

- skip when `abs(predicted_change_pct) < 0.10`
- skip when take-profit distance `< 0.5%`

Replace with:

- minimum predicted move: 1.0% price (not 0.10%)
- minimum take-profit distance: 1.2% from entry (not 0.5%)
- reject the entry if `tp_dist_pct` is less than round-trip cost plus a margin:
  - round-trip cost = `2 * taker_fee_rate * 100` plus 0.10% paper slippage
  - required edge = round-trip cost * leverage + 0.8 percentage points of price

Universe filter, same function, before sizing:

- skip symbols outside the top 40 USDT perpetuals by 24h quote volume
- hard-skip symbols whose recent 20 closed trades have negative sum PnL and win rate under 35% (the PHA / AIO / UAI / CBRS pattern)
- no new entry when the symbol is inside the existing per-symbol cooldown

Do not lower these again to "get more trades." More trades at this win rate increase the loss count.

## 2. Exits: stop manufacturing the 3-to-1 loss count

File: `bot/trading/executor.py` (close-reason block around the `negative_timeout` check)

Current rule:

```text
minutes_open >= negative_trade_timeout_minutes
and pnl_usdt < 0
and effective_pnl_pct <= -0.5
→ close_reason = negative_timeout
```

That rule is the strategy. Every fire is a loss, median hold about 15 minutes.

New early-exit rule, in this order:

1. Hard stop stays. Keep the existing safe-exit cap. Stops are fewer and already bounded.
2. Structure invalidation exit replaces `negative_timeout`:
   - long: close if 15m close is below the researcher `invalidation` (hour low) and the packet is not stale
   - short: close if 15m close is above the researcher `invalidation` (hour high) and the packet is not stale
   - if the packet is stale, fall back to the 1h structure flip (`structure.trend` opposite the position and `structure.confirmed`)
3. Scratch timeout only if both are true:
   - minutes open ≥ 45 (not 15)
   - effective PnL ≤ −1.0% (not −0.5%)
   - and neither invalidation nor structure has fired
4. Delete the path that closes a trade just because it is red after 15 minutes.

Trail, same file:

- do not arm the trail until effective PnL has cleared fees and is at least +1.0%
- once armed, trail by 0.5% effective from the peak
- historical trail bucket was 159 trades, all winners, +$389; the current build almost never reaches it (3 trades) because the timeout gets there first

Take-profit:

- keep the exchange TP order, but the price must satisfy the 1.2% distance gate
- do not convert a sub-1% winner into `profitable_timeout`

## 3. Researcher: filter and size, do not flip the side

Files: `bot/research/packets.py`, `bot/manager/gate.py`, `bot/research/scorer.py`

### Packet confidence must vary

Current in `packets.py`:

```text
confidence = max(0.35, min(0.85, 0.35 + votes))
arm = confidence >= 0.55 and not invalidation_hit
```

The votes are the same hour-open and EMA21 conditions that already chose the side, so the cap is hit almost every time.

New scoring:

- side still comes from sweep, then hour-open plus EMA21
- confidence is the fraction of independent checks that pass, not a clamped vote sum:
  - sweep in the trade direction (0.30)
  - 15m close on the correct side of EMA21 and hour open (0.20)
  - RSI on the correct side of 50 by at least 5 points (0.15)
  - ADX ≥ 22 (0.15)
  - 1h structure confirmed and matching the 15m side (0.20)
- no `min(0.85, ...)` clamp; store the raw 0–1 score
- `arm = true` only when:
  - setup is `15m sweep of hour low then reclaim` or `15m sweep of hour high then reject`
  - confidence ≥ 0.65
  - invalidation is not already hit
- "mixed 15m structure" and plain "holding above/below open and EMA21" stay unarmed. They may still be logged.

### Gate must not override direction

Current in `gate.py`: armed and confidence ≥ 0.70 replaces the manager side; confidence ≥ 0.80 vetoes a contradicting trade. The docstring still says the researcher does not veto. With every packet at 0.85 and armed, the override is always on and never selects.

New gate behaviour:

- researcher missing or stale → skip the entry (`allow_1h_only_if_research_stale` stays false)
- researcher side matches the aligned side and `arm` is true → confidence multiplier 1.10, size multiplier 1.0
- researcher side matches but `arm` is false → confidence multiplier 0.90, size multiplier 0.70
- researcher side contradicts the aligned side → skip the entry. Do not flip the side.
- remove the `pkt_armed and pkt_conf >= 0.70` override block entirely until the scorecard below is positive

Update the module docstring so it matches this behaviour.

### Scorecard before any override is allowed

File: `bot/research/scorer.py`

Persist, per setup and per symbol, over the last 50 followed packets:

- hit rate (15m/1h close finished on the predicted side)
- average trade PnL when the manager followed
- average trade PnL when the manager skipped a contradicting packet

An override may be reconsidered only if, for that setup, followed trades have higher net PnL than skipped trades and the sample is at least 30. Until then the researcher only scales size and blocks contradictions.

## 4. Sizing follows the scorecard

File: `bot/trading/sizing.py` and `bot/research/loop.py` (`_symbol_multiplier`)

- if the symbol's last 10 followed results are under 45% matches, size multiplier 0.50 (already partially implemented; keep it, and also block the entry if the multiplier is 0.50 and the packet is unarmed)
- never increase size because confidence is 0.85. That value is not evidence.
- one open position per symbol; no trade-switch close that locks a loss to free a slot (trade-switch was 71 closes, all losses, −$51 on the full file)

## 5. Acceptance check

After the change, run paper only. Re-export `trades_history.csv` and `researcher_history.csv`.

Pass:

- researcher confidence is not piled on a single value; armed share is well under 100%
- contradicting packets do not flip side (disagreement count in the join should be zero new entries, not 4%)
- `negative_timeout` is no longer the majority close reason
- over the next 300 closed trades, either win rate is above 35% at the current win/loss size, or average winner stays at least 3.5 times average loser
- net PnL after fees is positive, and profit factor is above 1.2

Fail any one of those and do not cut the entry floor again. The last cut (0.3% → 0.10% predicted, 1.0% → 0.5% take-profit) increased scratch trades; it did not create an edge.
