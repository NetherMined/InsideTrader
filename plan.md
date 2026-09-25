# InsideTrader — Experiments Branch: Testing Plan

**Branch:** `Experiments`
**Date:** September 25, 2026
**Purpose:** Validate all Features branch changes in paper trading before merging to `main`

---

## What's Under Test

The Experiments branch contains the following changes from Features:
- FUTURES-only mode (`force_trade_mode=FUTURES`)
- Sortable table columns across all 5 frontend pages
- Trade archiving + hard reset (658 trades archived, capital restored)
- SL cap direction fix (`safe_exit` inverted logic corrected for BUY/SELL)
- `profitable_timeout` removed (time limits now SL-side only)
- FUTURES BUY learning gate fallback fix (was falling back to SPOT)

---

## Phase 1 — Smoke Tests (run immediately on fresh bot start)

### T1-01: FUTURES-Only Mode Holds
**Test:** Start bot. Monitor logs for 10 minutes.
**Pass:** Zero SPOT trades opened. All trades are `FUTURES BUY` or `FUTURES SELL`.
**Fail:** Any `SPOT BUY` appears in logs or positions page.
**Check:** `redis-cli GET bot:force_trade_mode` → must be `FUTURES`

### T1-02: SL Cap Direction — SELL Trade
**Test:** Let a FUTURES SELL trade open. Observe price movement past SL threshold.
**Pass:** Trade closes at or near SL price, loss capped at expected %.
**Fail:** Trade stays open past SL, or closes in wrong direction.
**Check:** `bot/trading/executor.py` safe_exit — SELL SL is `entry * (1 + sl_pct)`, not `entry * (1 - sl_pct)`.

### T1-03: SL Cap Direction — BUY Trade
**Test:** Let a FUTURES BUY trade open. Observe price movement below SL threshold.
**Pass:** Trade closes at or near SL, loss capped.
**Fail:** Trade stays open past SL.

### T1-04: Profitable Trade Not Timed Out
**Test:** Let a FUTURES BUY trade open and go positive. Leave it open for 30+ minutes.
**Pass:** Trade does NOT close due to timeout while in profit.
**Fail:** Trade closes early while positive with any timeout-related log message.
**Note:** Only `negative_timeout` should fire (losing trades only).

### T1-05: Negative Timeout Still Works
**Test:** Open a trade that goes underwater. Wait past `negative_trade_timeout_minutes`.
**Pass:** Trade closes automatically after timeout period while losing.
**Fail:** Trade stays open indefinitely while losing.

---

## Phase 2 — Frontend Tests

### T2-01: Column Sorting — History Page
**Test:** Open `/history`. Click each column header.
**Pass:** Table sorts ascending on first click, descending on second. Sort arrow indicator visible.
**Fail:** No visual change, no sort, or JS error in console.

### T2-02: Column Sorting — Positions Page
**Test:** Open `/positions`. Click each column header.
**Pass:** Same as T2-01.

### T2-03: Column Sorting — Predictions Page
**Test:** Open `/predictions`. Click each column header.
**Pass:** Same as T2-01.

### T2-04: Hard Reset Modal
**Test:** Open Settings page. Click "Hard Reset" button.
**Pass:** Modal shows checklist of archive behavior. Confirmation required before executing.
**Fail:** Reset fires immediately without confirmation, or modal missing details.

### T2-05: Post-Reset State
**Test:** Execute hard reset.
**Pass:** All active positions archived. Capital restored to configured starting value. Open trade count = 0. Frontend reflects reset state immediately.
**Fail:** Trades deleted instead of archived, or capital not reset, or UI shows stale data.

### T2-06: Archived Trade Persistence
**Test:** After hard reset, query DB.
**Pass:** `SELECT COUNT(*) FROM trades WHERE archived = true` > 0. `SELECT COUNT(*) FROM trades WHERE archived = false AND status = 'OPEN'` = 0.
**Fail:** Trades deleted from DB (ML training data lost).

---

## Phase 3 — Safety Critical Bugs to Fix (from audit)

These are the P1 items identified in the full audit. Confirm repro in Experiments, then fix.

### T3-01: Kill Switch Bypass
**Repro:** Activate kill switch while defensive mode is also active.
**Bug:** `if kill_switch and not defensive_mode` — both active → check passes → kill switch ineffective.
**Fix:** `bot/trading/risk.py:196` — change to `if await self.is_kill_switch_active():`.

### T3-02: Daily Loss Limit Inaccuracy
**Repro:** Simulate 10 trades each losing 1% of capital.
**Bug:** Total loss reported ~9.56% (compounding %) not 10%. Kill switch triggers late or never.
**Fix:** Track daily P&L in USDT (`DAILY_PNL_USDT_KEY`), compare against `capital * daily_loss_limit / 100`.

### T3-03: Fill Price Zero on Partial Fill
**Repro:** Mock a partial fill returning `average: null`.
**Bug:** `float(order.get("average") or 0)` → 0.0 fill price. Position with $0 entry never closes.
**Fix:** `bot/trading/orders.py` — return `{"ok": False, "error": "Fill price unavailable"}` if fill_price ≤ 0.

### T3-04: Goal Progress Uses All-Time P&L
**Repro:** Check goal progress % on Settings page with any historical trades in DB.
**Bug:** Shows inflated % from all-time trade history, not period P&L.
**Fix:** `api/main.py:606` — use `bot:daily_pnl_usdt` Redis key instead of lifetime DB query.

### T3-05: MIN_NOTIONAL Too Low for Live
**Check:** `bot/trading/sizing.py` — confirm `MIN_NOTIONAL_USDT` value.
**Bug:** Set to 2.0 USDT. Binance minimum is 10 USDT. Live orders will reject.
**Fix:** Change to `MIN_NOTIONAL_USDT = 10.0`.

### T3-06: P&L Fallback Uses Wrong Capital
**Check:** `api/main.py:558` — `daily_pnl_usdt = round(daily_pnl * _starting_capital / 100, 4)`.
**Bug:** Uses hardcoded `_starting_capital = 100.0` instead of actual capital variable.
**Fix:** Use `capital` variable (already available in scope).

---

## Phase 4 — Configuration Audit (pre-live checklist)

Verify these are consistent before any live trading:

| Setting | bot/config.py | api/config.py | .env | Target |
|---------|--------------|---------------|------|--------|
| max_concurrent_trades | ? | ? | ? | 3–5 |
| confidence_threshold | ? | ? | ? | 0.75 |
| futures_leverage | ? | ? | ? | 2 |
| stop_loss_percent | ? | ? | ? | 2.0 |
| analysis_timeframe | ? | ? | ? | 1h |
| min_daily_trades | ? | ? | ? | 20–30 |

---

## Phase 5 — Extended Paper Trading Observation (2+ weeks)

Run Experiments branch in paper mode and collect these metrics daily:

| Metric | Target | Day 1 | Day 3 | Day 7 | Day 14 |
|--------|--------|-------|-------|-------|--------|
| Trades/day | 20–30 | | | | |
| Win rate overall | >50% | | | | |
| Win rate TRENDING | >55% | | | | |
| Fee drag (% of gross P&L) | <5% | | | | |
| Avg holding time | 15–60 min | | | | |
| Max drawdown/day | <5% | | | | |
| Kill switch triggers | 0 | | | | |

---

## Phase 6 — Regime Gating Experiment (highest-impact test)

**What:** Block ALL trades when `regime == RANGING` (currently only reduces position size).

**How:**
1. In `executor.py _try_open_trade()`, add: `if regime == "RANGING": return False`
2. Run 48h paper trading
3. Compare trades blocked vs total attempted

**Pass:** 30–50% of attempted trades filtered. Net P&L improves despite fewer trades.
**Fail:** <10% filtered — ADX threshold needs adjustment in `regime_detector.py`.

---

## Go-Live Gate

Before merging Experiments → main and enabling live trading:

- [ ] T1-01 through T1-05 all pass
- [ ] T2-01 through T2-06 all pass
- [ ] T3-01 kill switch bypass fixed
- [ ] T3-02 daily loss USDT tracking fixed
- [ ] T3-03 fill price validation fixed
- [ ] T3-04 goal progress uses period P&L
- [ ] T3-05 MIN_NOTIONAL raised to 10.0
- [ ] T3-06 P&L fallback capital fixed
- [ ] Phase 4 config unified across all files
- [ ] 2+ weeks paper trading: positive net P&L after fees
- [ ] Regime gating experiment completed and result logged
- [ ] Emergency stop tested end-to-end
- [ ] Docker images rebuilt with all fixes
