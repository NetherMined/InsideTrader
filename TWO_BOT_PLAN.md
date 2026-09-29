# Two-Bot Plan — Manager (1h) + Researcher (15m)

**Branch:** `Features`  
**Repo:** `NetherMined/InsideTrader`  
**Date:** 2026-09-29  
**Status:** Phase 1–4 landed on this branch (packets, scorecards, entry gate, tighten-only patches)

This is the build spec. `plan.md` on this branch already called for the same split (P2-05, P3-01, P3-05). This file is what was actually wired.

---

## Why Features needed a fix first

`Features` already tried to *replace* 1h with 15m (`analysis_timeframe="15m"` in `bot/config.py`) and had to revert the fetch in commit `cfb513e` because startup pulled 365d × 96 bars × 100 pairs and never finished.

That left a split brain:

| File | What it said |
|---|---|
| `bot/config.py` | `analysis_timeframe = "15m"` (manager trained on 15m) |
| `.env.example` / `strat.md` / revert commit | 1h is the trading timeframe |
| `plan.md` P2-05 | 1h primary, 15m confirmation |
| `bot/data/fetcher.py` | 15m first-fetch capped at 30 days (good) |

**Fix applied:** manager trains and ranks on **1h**. Researcher fetches **15m** with the existing 30-day cap. They are two clocks, not one setting.

---

## Hard rules

1. Researcher never calls `orders.py`, `paper.py`, or `open_position`.
2. Manager never opens from a 15m flip alone.
3. 1h close / 1h model is permission to act. 15m is timing + error detection.
4. Forecast quality ≠ trade P&L. Score them separately.
5. One hour cannot rewrite rules. Rolling window only.
6. Loosening patches defer. Tighten-only may auto-accept.
7. Paper stays default.

---

## Mapping on this branch

| Piece | Role |
|---|---|
| `bot/trading/executor.py` `_try_open_trade` | Manager. New `should_enter()` gate after side is final. |
| `bot/trading/risk.py` `sizing.py` `journal.py` `orders.py` `paper.py` | Manager only. Unchanged exits (SL/TP, 2% cap, kill-switch). |
| `bot/analysis/trainer.py` | Still uses `settings.analysis_timeframe` — now forced to `1h`. |
| `bot/analysis/performance_tracker.py` | Manager outcome memory. Packet metadata stored in `Trade.extra.research`. |
| `bot/data/fetcher.py` | Dual fetch. 15m lookback cap already in place. |
| `bot/research/*` | Researcher process. |
| `bot/manager/gate.py` | Choke point in front of a new fill. |
| Redis | Shared bus. |

---

## Redis bus

```text
research:packet:{symbol}          latest 15m packet
research:packet:{symbol}:{hour}   frozen packet for that 1h
research:review:{symbol}:{hour}   scorecard
research:errors:rolling           last 200 label bundles
manager:rules                     live playbook + rule_version
manager:patches:pending           last proposed/accepted/deferred patch
```

---

## Packet (researcher)

Falsifiable against the **closed 1h candle**, not “buy now”.

v1 construction is indicator-only (no second XGBoost):

- 15m EMA21 vs close, RSI, ADX, hour-high/low sweep
- Side = 15m close vs 1h open, confirmed by EMA
- Range = remaining-hour ATR band
- Invalidation = swept hour extreme
- `arm=true` only if confidence ≥ 0.55 and invalidation not printed

v2 (not in this commit): 15m XGBoost → `pred_1h_close_side`. Do not start v2 until scorecards beat chance.

---

## Manager gate

After the existing Features logic (regime, funding override, SELL floor) and **before** an order:

1. No packet / stale packet → allow 1h-only if `allow_1h_only_if_research_stale`.
2. 15m side disagrees with final manager BUY/SELL → **skip**.
3. `require_15m_confirm` and not armed / invalidation hit / low confidence → **skip**.
4. Else enter with existing sizing. Attach packet summary to `entry_indicators`.

Disagreement defaults to skip. Do not fade the 1h model just because 15m flipped.

---

## Scorecard + patches

On each new hour the researcher scores the **previous** hour:

| Axis | Question |
|---|---|
| Forecast | Did 1h close side / range match? |
| Process | Did manager follow arm / skip advice? |
| Usefulness | helped / hurt / missed / neutral |

Labels: `wrong_bias`, `right_bias_bad_timing`, `stop_too_tight`, `ignored_good_packet`, `chased_bad_packet`, `ignored_invalidation`, `overfit_15m`.

If ≥ `patch_min_label_count` of last `patch_window_hours` share a label, write a patch. Tighten-only auto-accept bumps `rule_version` and may raise `research_min_confidence`. Looseners stay `deferred`.

---

## Config (this branch)

```python
analysis_timeframe = "1h"          # was "15m" on Features — fixed
research_enabled = True
research_timeframe = "15m"
research_min_confidence = 0.55
require_15m_confirm = True
allow_1h_only_if_research_stale = True
research_stale_minutes = 20
```

Trade-count floor is unchanged. If the gate starves `min_daily_trades`, **lower the floor** — do not disable research to hit 50–100 trades. `plan.md` already warns that floor is fee suicide.

---

## What this commit changed

- `bot/config.py` — 1h manager, research flags
- `.env.example` — `RESEARCH_*` keys
- `bot/db/models.py` — `archived` on Trade (was migrated but missing on the model), `ResearchPacket`, `HourReview`, `RulePatch`
- `bot/db/connection.py` — indexes for the new tables
- `bot/research/` — packets, scorer, patcher, 15m loop
- `bot/manager/gate.py` — entry choke point
- `bot/trading/executor.py` — gate + journal research blob
- `bot/main.py` — fetch both timeframes, start researcher task
- `bot/data/fetcher.py` — no logic change; 30d 15m cap is required so startup does not hang again

---

## Still not done (from Features `plan.md`)

Keep these out of the researcher:

- P1 safety bugs (kill-switch vs defensive mode, USDT daily loss, atomic close) — still valid, still manager-side
- P3-05 second XGBoost + meta-learner — Phase 5 of this plan
- Dashboard `/research` page
- Lowering `min_daily_trades` / `max_concurrent_trades` (docs still disagree with `bot/config.py`)

---

## Success after 7 paper days

- Forecast side accuracy logged per traded symbol
- `chased_bad_packet` rare
- `ignored_good_packet` visible (tells you whether to relax the gate)
- Fewer entries than the old 5s loop
- Net P&L after fees is the only money metric
