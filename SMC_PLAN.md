# Smart Money Concepts (SMC) Integration Plan

**Branch:** `Features`
**Date:** 2026-09-30
**Status:** spec — implement on `Features`
**Depends on:** TWO_BOT_PLAN.md (dual-bot architecture must be landed first)

---

## Principle

The 1h manager bot gains market-structure awareness: swing highs/lows, demand/supply zones, and a 2.5:1 R:R gate. The 15m researcher bot (which never trades) scores every closed trade against structure — was the entry at a valid zone? did structure confirm the direction? was R:R honoured? Those scores feed back into the patcher, shaping future ML and gate decisions.

---

## Hard rules (carry forward from TWO_BOT_PLAN.md)

1. Researcher never places orders.
2. Manager never opens from structure alone — ML confidence + structure must agree.
3. 1h structure defines trend permission. 15m structure refines timing analysis.
4. The 2.5:1 R:R gate is a hard reject — no trade may open below it.
5. Zone-based SL replaces fixed-% SL. The old 2% default becomes a fallback ceiling.
6. Heat cap (40%) and single-symbol cap (10%) remain unchanged.
7. All structure detection is deterministic (no ML) — swing pivots from OHLC data.

---

## Phase 1 — Structure Detection Module

**New file:** `bot/analysis/structure.py`

Swing detection from 1h OHLCV candles stored in the `candles` table.

### 1.1 Swing pivot identification

A swing high (SH) = a candle whose high is higher than the N candles on each side (N=3 default).
A swing low (SL) = a candle whose low is lower than the N candles on each side.

```
def find_swing_points(df: pd.DataFrame, lookback: int = 3) -> pd.DataFrame
    Returns df with columns: swing_high (bool), swing_low (bool), swing_high_price, swing_low_price
```

### 1.2 Market structure classification

Walk the swing points forward in time:

- **Uptrend**: latest SH > previous SH AND latest SL > previous SL
- **Downtrend**: latest SL < previous SL AND latest SH < previous SH
- **Ranging**: neither condition met (no new HH or LL)

Key rule: **a swing low is only validated (confirmed) once price breaks above the previous swing high.** Until that break, the low is provisional.

```
@dataclass
class StructureState:
    trend: str            # "UP", "DOWN", "RANGE"
    last_hh: float        # last confirmed higher high
    last_hl: float        # last confirmed higher low
    last_ll: float        # last confirmed lower low
    last_lh: float        # last confirmed lower high
    bos_price: float      # break-of-structure price (the high that validated the low, or vice versa)
    bos_side: str         # "BULL" or "BEAR"
    confirmed: bool       # True if the latest swing is validated by a BOS

def classify_structure(df: pd.DataFrame, lookback: int = 3) -> StructureState
```

### 1.3 Demand and supply zone detection

- **Demand zone** (uptrend): the candle body range of the last confirmed higher low (HL). Zone = [candle_low, max(candle_open, candle_close)].
- **Supply zone** (downtrend): the candle body range of the last confirmed lower high (LH). Zone = [min(candle_open, candle_close), candle_high].

Zones are invalidated when price closes through them (not just wicks).

```
@dataclass
class Zone:
    type: str         # "DEMAND" or "SUPPLY"
    top: float
    bottom: float
    origin_time: datetime
    invalidated: bool
    touches: int      # how many times price has revisited (fresh = 0)

def detect_zones(df: pd.DataFrame, structure: StructureState) -> list[Zone]
```

### Files touched
| File | Change |
|---|---|
| `bot/analysis/structure.py` | **NEW** — all of 1.1, 1.2, 1.3 |

### Tests
- Unit test with synthetic OHLCV: known uptrend (HH/HL sequence) must return `trend="UP"`.
- Zone detection: demand zone at the HL candle body.
- BOS validation: a swing low without a subsequent high break must have `confirmed=False`.

---

## Phase 2 — R:R Gate and Zone-Based SL/TP

### 2.1 Zone-aware SL placement

Replace the fixed-% SL with a zone-based SL. Fallback to fixed % if no zone is found.

**For BUY (long):**
- SL = nearest demand zone bottom - (ATR * 0.15 buffer)
- If no demand zone within 5% of entry: SL = entry * (1 - stop_loss_percent/100) (current behaviour)
- Hard ceiling: SL may never be wider than 3% from entry (prevents oversized losses)

**For SELL (short):**
- SL = nearest supply zone top + (ATR * 0.15 buffer)
- Same fallback and ceiling rules

```
def calculate_zone_sl(
    entry_price: float,
    side: str,
    zones: list[Zone],
    atr: float,
    fallback_sl_pct: float = 2.0,
    max_sl_pct: float = 3.0,
) -> float
```

### 2.2 Zone-aware TP placement

**For BUY:** TP = nearest unbroken supply zone bottom (the next resistance).
**For SELL:** TP = nearest unbroken demand zone top (the next support).
Fallback: current ATR-based TP if no opposing zone within range.

```
def calculate_zone_tp(
    entry_price: float,
    side: str,
    zones: list[Zone],
    atr_pct: float,
    fallback_tp_pct: float = 3.0,
) -> float
```

### 2.3 R:R gate (hard 2.5:1 minimum)

Before any order, compute:
```
rr_ratio = abs(tp - entry) / abs(entry - sl)
if rr_ratio < 2.5:
    skip trade
```

This is a **pre-order gate** in `_try_open_trade`, checked after SL/TP are calculated but before the order is placed.

### Files touched
| File | Change |
|---|---|
| `bot/analysis/structure.py` | Add `calculate_zone_sl`, `calculate_zone_tp` |
| `bot/trading/sizing.py` | `calculate_sl_tp_prices` gains optional `zones: list[Zone]` param; calls zone functions when zones are provided, falls back to current logic when not |
| `bot/trading/executor.py` `_try_open_trade` | After SL/TP calc, compute R:R ratio. If < 2.5, log and skip. Pass zones from structure analysis |
| `bot/config.py` | Add `min_rr_ratio: float = 2.5`, `zone_sl_max_pct: float = 3.0` |

### Conflict resolutions
| Current behaviour | New behaviour | Resolution |
|---|---|
| SL = fixed 2% | SL = zone-based, capped at 3% | Zone SL when available, fixed % as fallback |
| TP = max(3%, min(6%, ATR*1.5)) | TP = opposing zone or ATR-based | Zone TP when available, ATR TP as fallback |
| No R:R check | Hard 2.5:1 minimum | New gate — trades below 2.5:1 are rejected |
| Break-even at -1.5% | Break-even only if SL was fixed-% (no zone) | Zone-based SL trades skip break-even recovery (the zone IS the thesis) |

---

## Phase 3 — Structure-Aware Entry Filter in Manager

### 3.1 Structure state per symbol

On each 1h loop iteration, compute `StructureState` for the top-ranked symbols using their 1h candle history (already in DB). Cache in Redis for 1h.

```
Redis key: structure:{symbol}
TTL: 3600 (1 hour)
Value: JSON of StructureState + zones list
```

### 3.2 Direction alignment: ML + Structure must agree

Current `_determine_side()` returns BUY/SELL from ML prediction sign alone. Add a structure filter:

| ML says | Structure says | Result |
|---|---|---|
| BUY | UP (confirmed uptrend) | BUY — proceed |
| BUY | DOWN | SKIP — ML disagrees with structure |
| BUY | RANGE | BUY only if price is at demand zone |
| SELL | DOWN (confirmed downtrend) | SELL — proceed |
| SELL | UP | SKIP — ML disagrees with structure |
| SELL | RANGE | SELL only if price is at supply zone |

This replaces the EMA21 vs EMA50 trend check in `gate.py:62` (`compute_market_trend`). The EMA cross is a lagging proxy for what structure detection does directly.

### 3.3 Zone proximity filter

Only enter longs when price is within 1 ATR of a demand zone. Only enter shorts when price is within 1 ATR of a supply zone. This ensures entries happen near zones, not at random prices.

```
def is_near_zone(price: float, zones: list[Zone], side: str, atr: float) -> bool
```

### Files touched
| File | Change |
|---|---|
| `bot/trading/executor.py` `_try_open_trade` | Load structure state from Redis. Add structure direction filter. Add zone proximity check. Pass zones to SL/TP calc |
| `bot/manager/gate.py` `compute_market_trend` | Replace EMA cross logic with structure-based trend. Keep 15m packet agreement check (researcher still confirms timing) |
| `bot/trading/executor.py` `_check_and_close_positions` | For zone-based SL trades: disable break-even recovery (the zone thesis must play out or hit SL) |

---

## Phase 4 — ML Feature Enrichment

Add structure-derived features to the XGBoost feature set so the ML model learns from structure over time.

### 4.1 New features (7 columns)

| Feature | Type | Description |
|---|---|---|
| `structure_trend` | int | 1=UP, 0=RANGE, -1=DOWN |
| `structure_confirmed` | int | 1 if latest swing is BOS-validated |
| `distance_to_demand_pct` | float | % distance from current price to nearest demand zone top (negative = inside zone) |
| `distance_to_supply_pct` | float | % distance from current price to nearest supply zone bottom |
| `zone_rr_potential` | float | theoretical R:R if entered now (TP at opposing zone / SL at zone edge) |
| `swings_since_bos` | int | number of swing points since last break of structure |
| `zone_touch_count` | int | how many times the nearest zone has been tested (fresh zones = stronger) |

### 4.2 Feature computation

Add to `bot/analysis/features.py`:
```
def add_structure_features(df: pd.DataFrame) -> pd.DataFrame
    Calls structure.classify_structure() and structure.detect_zones()
    Appends the 7 new columns to df
```

Called from `engineer_features()` after existing indicator features.

### Files touched
| File | Change |
|---|---|
| `bot/analysis/features.py` | Add 7 new entries to `FEATURE_COLS`. Add `add_structure_features()` call inside `engineer_features()` |
| `bot/analysis/structure.py` | Ensure `classify_structure` and `detect_zones` work on DataFrame input (already designed this way in Phase 1) |

### Retraining note
After Phase 4 lands, the ML model must be retrained to pick up the new features. Until retrained, these columns will be zero-filled and have no effect on predictions. The retrain happens naturally on the next scheduled training cycle.

---

## Phase 5 — Researcher Scores Trades Against Structure

This is the core feedback loop. The 15m researcher (which already scores packets against closed 1h candles) gains structure-aware scoring labels.

### 5.1 New scoring labels

Add to `bot/research/scorer.py` `score_packet()`:

| Label | Condition | Meaning |
|---|---|---|
| `entered_at_zone` | Trade entry was within 1 ATR of a demand/supply zone | Good: zone-aligned entry |
| `entered_away_from_zone` | Trade entry was >1 ATR from any zone | Bad: chasing price |
| `structure_agreed` | Trade side matched structure trend at entry time | Good: aligned with market structure |
| `structure_disagreed` | Trade side opposed structure trend | Bad: counter-trend without zone support |
| `rr_honoured` | Trade R:R at entry was >= 2.5:1 | Good: disciplined entry |
| `rr_violated` | Trade R:R at entry was < 2.5:1 | Bad: should not have entered (legacy trade before gate existed) |
| `zone_sl_held` | Zone-based SL was not hit — zone respected | Good: zone thesis correct |
| `zone_sl_failed` | Zone-based SL was hit — zone broke | Bad: zone invalidated |
| `bos_confirmed_after_entry` | Break of structure occurred in trade direction after entry | Good: structure confirmed the move |
| `bos_against_after_entry` | Break of structure occurred against trade direction | Bad: structure reversed |

### 5.2 Researcher loop changes

In `bot/research/loop.py` `_score_closed_hour`:
1. Load 1h candles for the symbol.
2. Run `classify_structure()` to get the structure state at the time the trade was opened.
3. Run `detect_zones()` to get the zones that existed at entry time.
4. Check each trade opened during that hour against the structure labels above.
5. Append labels to the existing review.

### 5.3 New patch rules from structure labels

Add to `bot/research/patcher.py` `tighten_map`:

```python
"entered_away_from_zone": "raise confidence threshold +0.03 when no zone within 1 ATR",
"structure_disagreed": "block entry when structure trend opposes ML prediction",
"rr_violated": "enforce 2.5:1 minimum R:R on all new trades",
"zone_sl_failed": "widen zone SL buffer by 0.05 ATR (was 0.15, now 0.20)",
"bos_against_after_entry": "add structure_confirmed=True requirement before entry",
```

These are all tighten-only patches and will auto-accept per existing `patch_auto_accept_tighten_only=True`.

### Files touched
| File | Change |
|---|---|
| `bot/research/scorer.py` `score_packet` | Import structure module. Load structure state for the trade's hour. Compute and append structure labels |
| `bot/research/patcher.py` `tighten_map` | Add 5 new tighten rules for structure labels |
| `bot/research/patcher.py` `apply_tighten_if_allowed` | Handle new patch actions (adjust config values, add gate flags) |

---

## Phase 6 — Frontend: Structure Visualization (optional, low priority)

### 6.1 API endpoint

`GET /api/v1/structure/{symbol}` — returns current `StructureState` + zones + swing points for charting.

### 6.2 Dashboard

Add a "Structure" badge to the Predictions page showing UP/DOWN/RANGE per symbol, plus zone levels. This is display-only — no user controls.

### Files touched
| File | Change |
|---|---|
| `api/main.py` | New endpoint |
| `frontend/app/predictions/page.tsx` | Structure badge + zone price levels |

---

## Phase execution order

| Phase | Depends on | What it delivers |
|---|---|---|
| 1 | Nothing | `structure.py` — swing detection, structure classification, zone detection |
| 2 | Phase 1 | Zone-based SL/TP + 2.5:1 R:R gate |
| 3 | Phase 1+2 | Structure-aware entry filter in manager |
| 4 | Phase 1 | ML features for retraining |
| 5 | Phase 1+3 | Researcher scores trades against structure, feeds back via patches |
| 6 | Phase 1 | Frontend display (optional) |

Phases 1 is the foundation. Phase 2 and 4 can run in parallel after Phase 1. Phase 3 needs Phase 2 (for zone SL/TP). Phase 5 needs Phase 3 (structure must be in the entry path before scoring makes sense).

---

## What this plan removes or replaces

| Old component | Status |
|---|---|
| Fixed 2% SL in `sizing.py` | Becomes fallback — zone SL is primary |
| EMA21 vs EMA50 trend in `gate.py:62` | Replaced by structure trend (HH/HL/LL/LH) |
| `regime_detector.py` ADX-based regime | Kept as secondary signal — structure trend is primary, ADX adds confidence weighting |
| Break-even recovery at -1.5% (`executor.py:293`) | Disabled for zone-based SL trades (zone thesis must play out) |
| Mean-reversion signals from `regime_detector.py` | Kept for RANGING structure — demand zone entries in ranges are the same concept |

## What this plan does NOT change

| Component | Reason |
|---|---|
| Heat cap (40%) | Portfolio-level risk — orthogonal to entry method |
| Single-symbol cap (10%) | Same |
| Kill switch / defensive mode | Same |
| Correlation filter | Same |
| Funding rate awareness | Same |
| 15m researcher never trades | Hard rule — structure scoring is read-only analysis |
| Paper/live mode toggle | Same |
| Trailing stop (after TP hit) | Compatible — triggers after R:R target is reached |
| Negative trade timeout | Kept — zone trades that time out without moving are still stale |

---

## Config additions

```env
# SMC / Structure settings
MIN_RR_RATIO=2.5
ZONE_SL_MAX_PCT=3.0
ZONE_SL_BUFFER_ATR=0.15
ZONE_PROXIMITY_ATR=1.0
SWING_LOOKBACK=3
STRUCTURE_CACHE_TTL=3600
```

All added to `bot/config.py` as `Settings` fields with the defaults above.
