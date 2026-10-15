# InsideTrader — Fix Plan (Oct 14, 2026, branch FeaturesGrok)

**Source:** 24h review of 69 closed paper trades (Oct 13 19:20 → Oct 14 17:21 UTC), bot logs, 1m candles after each entry.
**Rule:** Phases run in order. Stop after each phase and wait for approval before starting the next one.

---

## What the review found

| Finding | Evidence | Impact |
|---|---|---|
| **Paper P&L counts leverage twice** | `pnl_usdt = notional * (raw% * leverage)`, and `notional = qty * price` already includes leverage. Trade #6740: $136.61 notional, −0.701% move → recorded −$4.79, real −$0.96 | Every $ figure in the DB, dashboard, Redis capital, daily-loss limit and $2 trailing gate is 5× too big |
| Only the close-side fee is recorded | `estimated_fee = notional * taker_fee_rate` is logged once per trade, but paper.py charges both open and close | Fees under-reported by about half |
| **Real 24h result** | Gross −$0.76, fees about −$4.7 round trip | **About −$5.4**, not +$1.62 |
| Instant `structure_invalidation` exits | 27 trades closed within 30s. The gate drops a contradicting packet, but `executor.py:430-451` reads `research:packet` from Redis anyway, so a BUY is checked against the SELL-side level | 27 scratch losses; also killed winners (ARC +6.4%, CSOPSKHYNIX2L +2.4% within 15m) |
| Oct 8 guards missing on this branch | No pre-entry invalidation check, no 5-min minimum hold, no 0.3% buffer (these exist on Features) | Same as above |
| Stops sit inside normal price noise | SL 0.5–0.86% vs ATR 1–5%. Average worst move against the trade within 15m: −1.39% | 13 stop-losses, the largest real loss source |
| TP never reachable | Zone TP often 20–55% away; R:R values like 81:1 | The R:R gate is meaningless; only the trailing stop takes profit |
| Fast re-entry after a loss | BTW, CSOPSKHYNIX2L, 龙虾 each traded 3× in about 30 min. Cooldown is 300s | Repeated losses on the same coin |
| ML has no direction edge | Right 43% after 15m, 45% after 60m. Confidence 0.70+ won 4/18. `predicted_change_pct` is always ±0.5 | No reason to increase size yet |
| What works | Trailing stop: 19/19 wins. TRENDING regime and BUY side did better than TRANSITION and SELL | Keep it |

---

## Phase 1 — Accounting bugs (P&L, fees) — ✅ verified Oct 15 (40 live trades: recorded P&L = qty × Δprice − fees, error 0; capital moved −$3.74 = recorded net)

Fix the numbers first. Every later phase depends on them being right.

**1.1 Remove the leverage double count**
- Files: `bot/trading/paper.py` (`simulate_sell`, `simulate_buy_back`) and `bot/trading/executor.py:306-311`, `:406-412`, `:1000-1005`.
- Change: `pnl_usdt = notional * raw_pnl_pct / 100` (equivalently `margin * effective_pnl_pct / 100`). Keep `effective_pnl_pct = raw * leverage` as the return on margin; the 3.8% max-loss cap is still correct in those terms.
- Put the formula in one helper (`bot/trading/pnl.py: compute_pnl(side, entry, exit, qty, leverage) -> (raw_pct, eff_pct, pnl_usdt)`) and call it from all five places so they cannot drift again.

**1.2 Record round-trip fees**
- `estimated_fee_usdt` = open fee + close fee (`entry_notional + exit_notional) * taker_fee_rate`), matching what paper.py takes from capital.
- `gross_pnl_usdt = pnl_usdt + estimated_fee_usdt`.

**1.3 Replace the $2 trailing-stop gate with a percentage**
- `_MIN_PROFIT_USD = 2.00` (`executor.py:139`) only worked because P&L was inflated 5×. With real P&L, $2 on an $85 position needs a 2.35% move, so the trail would almost never fire.
- Change it to a raw-move threshold, `trail_min_profit_raw_pct = 0.40` in `config.py`. That covers fees (0.08%) and exit slippage (0.10%), and is close to the old $2 gate's real effect (~0.47% at $85 notional, 5x).
- Also apply this to the TP follower retrace check (`executor.py:424`), which uses the same `$2` constant.

**1.4 Historical data (non-destructive)**
- Add `pnl_usdt_true` and `fee_usdt_true` columns computed from existing rows. Do not overwrite `pnl_usdt`.

**1.5 "Reset Paper Capital" modal (decided: you enter the amount)**
- Frontend: on the Settings page "Paper Capital" card (`frontend/app/settings/page.tsx:537`), replace the inline input with a **Reset Paper Capital** button. It opens `frontend/components/ResetCapitalModal.tsx`, styled like `StartupModal.tsx`.
- The modal shows:
  - the current capital, flagged as inflated by the old P&L bug;
  - a **Trade capital (USDT)** input that must be greater than 0;
  - the open-position count. If any positions are open, the confirm button stays disabled until they are closed.
  - On confirm it calls the existing `api.setCapital()`.
- API: `POST /api/v1/settings/capital` (`api/main.py:719`) already exists. Add:
  - return 409 when open positions exist;
  - store `paper:capital_reset_at` (UTC timestamp) so dashboard and stats can count from the reset.
- The endpoint sets `bot:command = restart`, so after a reset the modal hands over to the existing startup confirmation flow (`bot:command = confirmed`).

**1.6 Tests**
- `tests/test_pnl.py`: BUY/SELL × win/loss × leverage 1/3/5, asserting `pnl_usdt == qty * Δprice`.
- Replay check: recompute the 69 trades and confirm real gross is −$0.76.

**Done when:** recorded P&L equals `qty × price change` for every new trade, and fees match capital changes. → **Pause for approval.**

---

## Phase 2 — Exit bugs (instant invalidation) — ✅ verified Oct 15 (0 structure_invalidation closes in 40 trades, was 37/69; 2 sub-60s closes were genuine stop-loss hits)

**2.1 Only use an invalidation level from a packet that agrees with the trade**
- At entry, if `decision.packet` is not None (gate accepted it, same side), store `entry_indicators.research_side` and `entry_indicators.invalidation` on the trade.
- In the exit loop (`executor.py:430-466`): use the live packet's invalidation only when `pkt.pred_1h_close_side` matches the trade side. Otherwise there is no packet-based exit; SL, trailing stop and timeouts still apply.

**2.2 Pre-entry guard** (the Oct 8 versions were never committed to any branch, so this was written fresh)
- In `_try_open_trade`, before placing the order: skip when a BUY's `current_price <= invalidation` or a SELL's `current_price >= invalidation`. Log reason `pre_entry_invalidated`.

**2.3 Minimum hold + buffer** (`invalidation_min_hold_minutes`, `invalidation_buffer_pct` in config)
- `structure_invalidation` fires only after 5 minutes open, with a 0.3% buffer (BUY: `price < inv * 0.997`, SELL: `price > inv * 1.003`). Make both values config settings.

**2.4 Fix the log text**
- The log says "below hour_low" even when the level came from the other side's packet. Log the packet side and the level instead.

**2.5 Tests**
- Unit tests for: contradicting packet → no invalidation exit; price past level at entry → entry skipped; held < 5 min → no exit.

**Done when:** in 12h of paper trading, zero trades close in under 60s. → **Pause for approval.**

---

## Phase 3 — Risk geometry (stops, TP, re-entry) — ✅ verified Oct 15 (3x; SL 0.90–1.35% incl. 0.1% entry slip; max entry ATR 2.37%; TP ≤ 3×SL)

Every value goes into `config.py` / Redis so it can be tuned without code changes.

**3.1 Leverage 5x → 3x, ATR-based stop** (approved Oct 14)
- Set `FUTURES_LEVERAGE=3` in `.env` and Redis `bot:futures_leverage=3`.
- The 3.8% leveraged-loss cap allows a stop up to 1.27% at 3x, versus 0.76% at 5x.
- SL = `clamp(1.0 × atr_pct, 0.8%, 1.25%)` in `bot/trading/sizing.py:calculate_sl_tp_prices` and `zone_sl_max_pct`.
- Margin per trade stays the same, so the position is 40% smaller in notional and the dollar loss at the stop is about the same as now.

**3.2 Volatility ceiling**
- Skip entries with `atr_pct > 2.5%` (new `max_entry_atr_pct`). 龙虾 at 4.9% lost three times.

**3.3 Realistic take-profit**
- TP = nearer of (zone TP, entry ± 3 × SL distance). Keep the 1.2% minimum TP distance check. The trailing stop stays the main exit.

**3.4 Cooldowns**
- After a losing close: 45 min cooldown on that symbol (currently 5 min). After a win: keep 5 min.
- Loss-streak block: 2 losses within 2h (currently 3).

**3.5 Trailing stop**
- No blind change. The trail distance (0.1% raw at 5x, 0.17% at 3x) is tuned in Phase 4 against the baseline data before it is touched.

**Done when:** 24h paper run, no stop-loss closes on trades with `atr_pct > 2.5%`, and SL distance is logged on every entry. → **Pause for approval.**

---

## Phase 4 — Hindsight baseline builder (deterministic) — ✅ running nightly (first automatic build: 2026-10-14)

This produces the "baseline list of possible profitable trades". It is computed from the data, not guessed by an AI, so it can serve as ground truth.

**4.1 Script:** `bot/tools/baseline.py` (runs inside the bot container; reads Postgres only)
- Input: the previous UTC day (or `--from/--to`).
- Data already stored: `predictions` (~8.7k/day), `research_packets`, `trades`, `candles` 1m/15m.

**4.2 Three outputs, written to `baseline/YYYY-MM-DD/`**
1. **`signals.csv`**: every prediction the bot produced (traded or not). For each one, simulate the bot's real exit rules (SL, trailing stop, invalidation, timeouts, fees, slippage) on the 1m candles that followed. Columns include symbol, time, side, confidence, regime, atr_pct, researcher side/arm, traded (y/n), simulated P&L, MFE/MAE at 15/60m.
2. **`opportunities.csv`**: the oracle list. For each symbol and 15m window, did a clean move happen? Clean means it reached +X% (fees + 2× SL buffer) before −SL. Records side, size of move and time to target. This is the list of trades that were available.
3. **`summary.json`**: hit rate and simulated P&L grouped by confidence bucket, regime, side, ATR band, researcher agreement, symbol and hour of day. Also lists how many opportunities the bot caught, missed or took the wrong way.

**4.3 Uses**
- Measure the ML properly: hit rate on all predictions, not just the 69 that were traded.
- Tune the trailing stop (3.5) and confidence threshold against simulated P&L.
- Gives Phase 5 a compact, secret-free dataset to review.

**4.4 Scheduling:** the bot's `daily_baseline` task runs at **02:15 UTC**, building the previous UTC day. Starting at 02:15 rather than 00:15 gives the day's last signals their full 2h to play out. Outputs go to `./baseline/` (bind-mounted in both compose files) and are kept for 30 days. 1m candles are only kept for about 48h, so a missed day can be rebuilt only within roughly a day.

**4.5 Validation result (Oct 13 19:20 → Oct 14 17:22, the 69 real trades replayed under their own old rules):** simulated net −$4.59 vs real −$4.35 (within 6%). Exit reason matches on 90% of trades; mean error is $0.20 per trade. By exit type, the errors partly cancel: invalidation exits come out −$2.80, stop-losses +$3.53.

**4.6 First findings (same window, current rules, 814 directional predictions, $100 per simulated trade):**
- Net −0.21% per trade, profit factor 0.60. Direction right 47% after 15m, 43% after 60m.
- Confidence works backwards: ≥0.80 is right only 35% / 25% of the time (profit factor 0.25), while <0.50 has profit factor 1.48 (n=37).
- Exits are lopsided: trailing-stop wins average +0.52%, stop-losses −1.26%.
- A sweep of trail distance (0.5–3.0% on margin), trail activation (1–3%) and stop size (0.6–1.0× ATR) never beats −0.20% per trade. Exit tuning cannot fix this; the entry signal has no edge after the ~0.28% round-trip cost.
- 1,202 clean moves were available. The bot traded 31, signalled the opposite direction on 255, and had no signal on 720.

**Done when:** a run over Oct 13 reproduces the 69 real trades within ±10% P&L, so the simulator is validated. → **Pause for approval.**

---

## Phase 5 — AI review with OpenCode + Muse Spark 1.3 (free) — ✅ running nightly (first automatic reviews Oct 15 04:45, 100% verified, 7 pending suggestions)

**What's installed (checked Oct 14):**
- `opencode` 1.19.0 at `%APPDATA%\npm\opencode`.
- The model is available as **`opencode/muse-spark-1.3-contributor-free`** (`opencode models` lists it).
- Ollama is also installed with local models: `muse-glimmer:latest` (18 GB), `glm-4.7-flash`, `ornith-1.5:9b`, `granite4.1:3b`.

**Important — this is not local.** Muse Spark 1.3 Free runs on OpenCode's hosted endpoint. The free "contributor" tier lets Meta use prompts and responses to train future models, and the free offer is reported to be time-limited. So:
- Send only the Phase 4 output files (trade stats, prices, indicators). Never send `.env`, API keys, the repo, or passwords.
- Run OpenCode in an isolated folder that holds only the export, so the agent cannot read the repo.
- Keep a truly local fallback: `ollama` + `muse-glimmer` (or `glm-4.7-flash`), using the same prompt and the same outputs.

**5.0 Local model choice (hardware: RTX 4060 8 GB VRAM, 64 GB RAM)**
- No local model on 8 GB VRAM matches Muse Spark 1.3 (hosted frontier model, 1M context). Local models have much smaller context and weaker reasoning.
- This job is a nightly batch. Phase 4 does the maths, so the model only reads a summary and explains patterns. Speed hardly matters, and mixture-of-experts (MoE) models running partly from system RAM are fine.
- Candidates, all MoE with about 3–4B active parameters so they run with CPU offload:
  1. `gpt-oss:20b` (~13–16 GB, adjustable reasoning, good at structured JSON output). Needs `ollama pull gpt-oss:20b`.
  2. `glm-4.7-flash` (already installed, 19 GB).
  3. `qwen3:30b` (~18 GB), as a third option.
- Avoid the installed `granite4.1:3b` and `ornith-1.5:9b` (too small for this). Avoid dense 27B+ models: they don't fit in 8 GB VRAM and would be very slow.
- **Mini bake-off (Oct 14).** Same 150-row CSV (~4k tokens), question "highest total pnl symbol + total", true answer SYM5 = 0.66:

  | Model | Correct | Time | Notes |
  |---|---|---|---|
  | Muse Spark 1.3 (hosted) | ✅ | 28 s | Default |
  | gpt-oss:20b (local) | ✅ | 5 m 36 s | Reads input at 192 tok/s, writes at 29 tok/s; 58% CPU / 42% GPU; most of the time spent reasoning. **Fallback.** |
  | glm-4.7-flash (local) | — | >15 min on 25k tokens, stopped | Writes at 31 tok/s, but 70% of the model is on CPU so reading large inputs is too slow. Dropped. |

- Pick the model by running Muse Spark and both local candidates on the same day's inbox. Score each report on whether its numbers match `summary.json`, whether it invents rows, and whether its JSON is valid. Keep the best scorer.
- Local runs get `summary.json` plus the top 200 rows of `signals.csv` and `opportunities.csv` (by |simulated P&L|), not the full 8.7k rows, to stay inside the context window.
- Make the provider a setting: `AI_REVIEW_PROVIDER=opencode` (default) with `AI_REVIEW_MODEL=opencode/muse-spark-1.3-contributor-free`. `AI_REVIEW_FALLBACK=ollama:gpt-oss:20b` (installed Oct 14) is used automatically if OpenCode fails. Each report records which model wrote it.

**5.1 Folder layout**
```
D:\Personal\InsideTrader-ai-review\        (outside the repo)
  opencode.json                            (model + permissions, below)
  AGENTS.md                                (reviewer instructions + output schema)
  inbox\YYYY-MM-DD\                        (copied from baseline/: signals.csv, opportunities.csv, summary.json)
  reports\YYYY-MM-DD.md + .json            (AI output)
```

**5.2 `opencode.json` (sandboxed)**
```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "opencode/muse-spark-1.3-contributor-free",
  "permission": { "edit": "allow", "bash": "deny", "webfetch": "deny" }
}
```
- Edits are allowed only so it can write into `reports\`; bash and web access are denied.
- Do not use `--auto`.

**5.3 Run command (called by `bot/tools/ai_review.ps1` after the baseline job)**
```
opencode run --dir D:\Personal\InsideTrader-ai-review ^
  -m opencode/muse-spark-1.3-contributor-free ^
  -f inbox\2026-10-13\summary.json -f inbox\2026-10-13\signals.csv -f inbox\2026-10-13\opportunities.csv ^
  "Review yesterday per AGENTS.md. Write reports\2026-10-13.md and reports\2026-10-13.json."
```
- Put the prompt **before** `-f`. `-f` takes a list, so a prompt placed after it is read as a file name.
- **Where it runs.** OpenCode and Ollama are on Windows, not in Docker. The bot container writes the baseline to a bind-mounted `./baseline` folder, and Windows Task Scheduler runs `ai_review.ps1` 30 min after the baseline job. If the PC was off at that time, the job runs at the next boot.
- Fallback when the free endpoint is unavailable: the same files and prompt sent to `ollama run muse-glimmer`. `signals.csv` may need trimming to fit its smaller context.

**5.4 What the AI produces (schema in `AGENTS.md`)**
- `baseline_trades`: the top ~20 opportunities the bot could realistically have taken. Each one comes from `opportunities.csv` and gives the conditions that marked it (regime, ATR band, confidence, researcher agreement, hour).
- `patterns`: what separated winning signals from losing ones, each with sample size.
- `rule_suggestions`: proposed changes (e.g. "skip TRANSITION when confidence > 0.70"), each with the simulated P&L effect taken from `summary.json`.
- Every claim must cite rows or groups from the files. Suggestions with fewer than 30 samples are labelled low confidence.

**5.5 Guardrails**
- The AI is advisory only. Nothing it writes changes bot settings. Suggestions go into the existing `rule_patches` table with status `pending` (human approval, same as the researcher patcher's "human defer").
- Before any suggestion is accepted, `baseline.py --what-if` re-simulates it on the last 7 days.

**Done when:** one full daily cycle (baseline → AI report → pending patches) runs unattended, and the report's numbers match `summary.json`. → **Pause for approval.**

---

**5.6 As built (Oct 14)**
- Code: `bot/tools/ai_review/` holds `ai_review.py` (stdlib only, runs on Windows), `AGENTS.md`, `opencode.json`, `ai_review.ps1` and `register_task.ps1`. Each run copies the templates into `D:\Personal\InsideTrader-ai-review\`.
- **Permissions are `ask`, not `deny`.** Any `deny` in `opencode.json` makes the free tier refuse with "OpenCode's free tier can only be used from within OpenCode". In headless `opencode run`, `ask` is auto-rejected, which was tested: bash, write and reads outside the folder were all blocked. `external_directory: ask` is set explicitly.
- The model writes nothing. The script reads its JSON reply from stdout.
- Every cited pattern is checked against `summary.json`, and every trade against `opportunities.csv`. Each filter is re-run as a what-if on up to 7 days of `signals.csv`. Only suggestions that improve the average per trade over ≥30 remaining signals are inserted into `rule_patches` as `pending`, with scope `ai_review`. Nothing reads that table, so they are advisory only.
- Scheduled task "InsideTrader AI Review": daily at 04:45 SAST (02:45 UTC), runs after a missed start, 3h limit. Logs go to `...\InsideTrader-ai-review\logs\`.

- **Bake-off (Oct 14, same baseline for every model):**

  | Model | Valid JSON | Time | Patterns correct | Trades correct | Valid suggestions |
  |---|---|---|---|---|---|
  | Muse Spark 1.3 (default) | ✅ | 48 s | 6/6 | 15/15 | 3/3 |
  | gpt-oss:20b (fallback) | ✅ (2nd try; 1st failed after 21 min, cause not logged) | 8 m 22 s | 6/6 | 20/20 | 2/3 |
  | Qwen3.8-27B-Uncensored q5_K_M | ❌ empty reply after 52 min; with thinking off, timed out at 60 min | — | — | — | — |
  | glm-4.7-flash | dropped earlier (too slow at reading input), replaced by Qwen in the bake-off list | | | | |

---

## Phase 6 — Order size (only after the bot has an edge)

Don't increase size until **all** of these hold on corrected P&L, over at least 300 trades or 7 days:
- Net P&L after fees > 0.
- Baseline hit rate ≥ 52% at the traded confidence level.
- Profit factor ≥ 1.2.
- Max drawdown < 5% of capital.

Then switch to risk-based sizing, with risk per trade as a % of capital (start at 0.25%, so notional = risk ÷ SL%). Raise it in steps of 0.05% per week while the criteria still hold. Heat limit and max concurrent trades stay as hard caps.

---

## Decisions
1. ~~Capital reset amount~~ → resolved: modal popup asks for trade capital (Phase 1.5).
2. ~~Leverage 5x → 3x~~ → approved.
3. ~~AI provider~~ → resolved Oct 14: **Muse Spark 1.3 (hosted, via OpenCode) is the default.** Only the Phase 4 trade statistics are sent; no secrets, no repo. Ollama (`gpt-oss:20b`, or the installed `glm-4.7-flash`) is the automatic fallback when the free endpoint fails or the offer ends. The 5.0 bake-off becomes a one-time check that the fallback output is good enough.

---

## Phase 7 — Entry signal (why confidence is inverted)

Phases 1–5 fixed the plumbing. Live (40 trades, Oct 14–15) and baseline (2 days) agree: the entry signal has no edge, and higher confidence does *worse* (≥0.8: profit factor 0.24; <0.5: profit factor 1.59, n=42).

**7.1 Investigate (read-only, no bot changes).** Code review of `bot/analysis/ensemble.py`, `trainer.py` and `features.py` found:
- **(a) Early stopping uses the test set.** XGB and CatBoost stop on `eval_set=(X_test, y_test)`, and accuracy is then scored on that same set. The reported accuracy is optimistic, and model selection has seen the "future" slice.
- **(b) The label doesn't match the trade.** The label is one 15m bar, close-to-close, beyond ±0.28%. The live trade enters up to 10 min after the candle closes and exits on the trailing stop or stop-loss, up to 2h later.
- **(c) Feature windows assume 1h candles.** On 15m, `ret_24` is 6h, `ret_7d` is ~1.75 days, `ret_30d` is 7.5 days, and the "1y" features are all filler (0 or 1).
- **(d) Confidence = p_side / (p_side + p_opp).** This only compares the two directions. It ignores how likely "flat" is, so a row where the models think a move is very unlikely can still show 0.9.
- **(e) The 24-bar `fwd_ret` dropna removes the newest 6h of 15m rows from training.**

Then measure, on held-out data the models never saw:
- hit rate by confidence bucket, out of sample vs on the early-stopping set;
- whether high confidence lines up with chasing the last bar's move (`ret_1`).

**7.1 Result (Oct 15).** The live ensemble was retrained on 26 symbols, keeping the newest 20% of 15m history that the models never saw. 1,891 directional predictions were scored on the next 15m bar:
- **No skill.** The direction was right 50.4% of the time, and the average move in the predicted direction was −0.0006% before costs (the threshold to beat is 0.28%). Even on the slice used for early stopping, it was only 52.6% / −0.02%.
- **Confidence is noise, and overconfident on new data.** Only 19 in-sample predictions were ≥0.8, against 231 out of sample. Out-of-sample hit rate by bucket: ≥0.8 → 49.4%, 0.7–0.8 → 48.4%, 0.6–0.7 → 53.5%, <0.5 → 55.9% (n=152, ±8%). So "inverted" is mostly noise around 50%.
- **High confidence means fading the last bar.** At ≥0.8 the model goes against the previous bar's move 72% of the time, and its own P(flat) is highest there (0.33). Fading the last bar as a control does no better (50.0%, −0.05%).
- **Conclusion:** there is no sign bug to flip. The next-15m-bar target with these features carries no information. The fixes for (a)–(e) would make the numbers honest, but they cannot create an edge.

**7.2 Fix (after 7.1 findings are approved).** Candidate fixes, chosen by what 7.1 shows:
- a proper train / validation / test split;
- a label that matches the trade (path-based: does the trailing stop or stop-loss outcome win);
- confidence that includes P(flat);
- correct period constants for 15m.

**7.2 Research result (Oct 15): is any target predictable beyond the 0.28% cost?** Method: pooled LightGBM over 30 symbols, split by date (60/20/20) with a gap at each boundary. Early stopping uses the validation slice only. Structure features were stubbed out for speed.

| Data | Target | AUC | Most confident 10%: gross → net per trade |
|---|---|---|---|
| 1h candles, 1 year (250k rows) | 1h direction | 0.529 | +0.02% → −0.26% |
| | 4h direction | 0.509 | −0.18% → −0.46% |
| | 24h direction | 0.526 | −1.76% → −2.04% |
| 15m candles, 50 days (105k rows) | 15m direction | 0.498 (1 tree) | +0.02% → −0.26% |
| | 1h direction | 0.499 (1 tree) | +0.14% → −0.14% |
| | 4h direction | 0.508 (1 tree) | −0.23% → −0.51% |
| | **Path label: would the bot's real exits make money on this side?** (costs included) | **0.72 buy / 0.70 sell** | random side −0.23%; model side −0.22%; top 10% **−0.11%**; top 5% −0.10% |

- **Direction is unpredictable at every horizon** from these technical features. Early stopping often kept 1 tree, meaning the model found nothing that generalised.
- **The path label is the only target with real skill (AUC ~0.71).** But it mostly predicts *when* trading conditions are good, not *which way*: model side ≈ random side. Its best 10% still loses 0.11% per trade, about +0.17% before the 0.28% cost.
- **Costs are now the binding constraint.** The ~0.28% assumed round trip is 0.08% fees plus 0.2% simulated slippage. At ~0.1% total cost, the path-label top decile would be roughly break-even to slightly positive. Untested.

**7.2b Real costs (Oct 15, order-book snapshot of the 32 coins traded in the last 3 days).** At our ~$50 size, crossing the spread costs a median **0.018%** per fill (mean 0.022%; worst APR at 0.10%, KORU 0.047%). The paper simulator assumes **0.10%** per fill, about 5× too high. The fee side may be slightly low: the code uses 0.04% taker, and Binance USDT-M VIP0 is 0.05% (confirm your tier / BNB discount). That gives a realistic round trip of about **0.14%**: 0.10% fees + ~0.04% spread, plus extra slippage on stop-loss exits in fast moves. The simulator uses 0.28%. Caveat: one snapshot at a quiet hour. Spreads widen during the fast moves that trigger stops.
- With realistic costs, the path-label top decile (+0.17% gross) would be about **break-even to slightly positive**. The current live signals (−0.21% at 0.28% cost) would be about −0.07%, so still negative.

**7.2c Costs calibrated (Oct 15).** Taker fee 0.05%. Paper fills walk the live futures order book for the actual size, with a 0.03% fallback; stop-loss exits add 0.05% (an assumption). The baseline uses a flat 0.03% plus the stop extra. Validation is unchanged (old-trade replay still −$4.59 vs −$4.35). The same signals now lose −0.10% per trade (Oct 13–14) and −0.13% (Oct 14), against −0.21% / −0.33% before.

**7.2d Path-label model re-tested at realistic costs.** 30 symbols, test slice Oct 7 → Oct 15 (21k bars, never seen in training or early stopping):

| Most confident | Trades | Win | Avg net / trade (95% CI) | Positive days |
|---|---|---|---|---|
| 20% | 4,214 | 53.5% | −0.057% ± 0.030 | 4/9 |
| 10% | 2,107 | 53.9% | −0.049% ± 0.044 | 4/9 |
| 5% | 1,054 | 56.5% | +0.017% ± 0.065 | 5/9 |
| 2% | 422 | 60.2% | +0.073% ± 0.101 | 5/8 |

- It decides *when* to trade, not *which way*: the model's side does no better than a random side (−0.122% vs −0.117%).
- Results improve steadily as the cut gets more selective, crossing zero around the top 5%. **The confidence intervals still include zero, so no edge is proven.** Only ~8 days of test data exist; it needs a forward test.

**7.3 Path-model entry decider — paper forward test (spec, Oct 15; awaiting approval).**

*Goal:* replace the no-skill ensemble, on paper only, with the path-label model from 7.2d. Forward-test it until its edge is proven or disproven. Everything sits behind `settings.entry_model = "path" | "ensemble"`, so rollback is one setting.

*What the model is.*
- Two pooled LightGBM classifiers trained across all symbols, P(BUY profitable) and P(SELL profitable).
- "Profitable" = the bot's real exits (Phase 3 stops, trailing stop, timeouts) simulated on 15m bars with realistic costs, net > 0. This is the same `baseline.simulate`, so labels and live rules can't drift apart.
- Score = max(p_buy, p_sell); side = whichever is higher.
- Features: `FEATURE_COLS` minus the structure features. That matches the research and keeps nightly training to minutes.

**7.3a — Model module + nightly training (pause after).**
- New `bot/analysis/path_model.py` with:
  - `build_labels(candles)`: simulated net for BUY and SELL entries at the next bar's open;
  - `train()`: chronological split 60/20/20 with a 2h gap at each boundary, early stopping on the validation slice only;
  - `score(X)`;
  - `save()` / `load()` in the `ml_models` volume.
- Saved with the model:
  - **entry threshold** = the 97th percentile of the validation slice's scores (≈ top 3%);
  - the test-slice report (n, win rate, avg net and 95% CI at top 2/5/10%).
- New `daily_path_training` task at **01:30 UTC** (before the 02:15 baseline). Runs in a worker thread; wall-clock polled like the other daily jobs. Symbols: every symbol with ≥1,000 closed 15m candles.
- Training report written to `baseline/<day>/path_model.json`, so the nightly AI review can see it.
- Tests: labels equal `baseline.simulate` for a fixed candle set; threshold round-trips through save/load; no training row's label window crosses the split gaps.
- Done when: a training run reproduces the 7.2d test numbers within noise (top 5% avg net within ±0.065% of +0.017%).

**7.3a Result (Oct 15) — built, and the held-out test fails.** `bot/analysis/path_model.py`, the nightly task and 3 tests are in. The first full training run covered 295 symbols, 837k rows, test slice Oct 4–15 (168k rows, never seen in training or early stopping): AUC 0.655 / 0.663.

| Most confident | All 295 symbols | Bot's 134-symbol universe (trained on all) | Universe only (trained and scored) |
|---|---|---|---|
| 10% | −0.017% ± 0.016 | −0.021% ± 0.018 | −0.008% ± 0.023 |
| 5% | −0.029% ± 0.022 | −0.038% ± 0.025 | −0.048% ± 0.032 |
| 2% | −0.057% ± 0.034 | −0.066% ± 0.038 | −0.071% ± 0.049 |
| at live threshold | −0.060% ± 0.052 | | |

- The 7.2d result (+0.073% at top 2%, 30 symbols, 8 days) **did not reproduce** on a sample ~8× larger. More selective now means *worse*, and at top 2% the whole 95% CI is below zero. That is the 7.3c fail condition, reached on held-out data before any forward test.
- The 7.3a "done when" check (top 5% within ±0.065% of +0.017%) passes numerically at −0.029%. **It's still not a success**: the larger test is significantly negative where it matters.
- **7.3b (wiring into trading) is not recommended.** The nightly training task is registered in `main.py` but not yet live (no bot restart since). It can stay as a cheap (~1 min) nightly monitor of whether any edge appears as 15m history grows, or be removed.

**7.3b — Wire into the bot, paper only (pause after).**
- `trainer._analyse_symbol`: with `entry_model == "path"`, score the latest closed 15m row.
  - **Below the threshold:** `predicted_change_pct = 0` (no trade).
  - **At or above:** ±0.5 by side, with confidence = the score's percentile mapped onto [0.60, 0.92] so existing ranking and logging still work.
  - Save `entry_model`, `path_score`, `p_buy`, `p_sell` and `path_threshold` in the prediction's `features` snapshot.
- Turned **off** in path mode, because the research didn't include them and they'd change what is being tested. Each is still logged as a "would have" note:
  - the `run_analysis` per-symbol, per-direction and per-regime confidence factors;
  - the gate's 1h trend veto;
  - the researcher's confidence and size adjustments;
  - the confidence-threshold check (the model's own threshold replaces it).
- Kept, as risk limits: heat and position caps, the ATR ceiling, stop/TP geometry, cooldowns, loss-streak block, symbol viability, kill switch and daily loss limit.
- Live trading stays refused in path mode until Phase 6: if `paper_trading_mode` is false and `entry_model == "path"`, no entries.
- Tests: below-threshold rows emit no direction; path mode skips the veto and factors; a live-mode guard blocks entries.

**7.3c — Forward-test tracking (runs daily, then a decision).**
- Baseline summary gains an `entry_model` group, and `path_model.json` sits beside it. The AI review prompt asks for the path model's daily result against its CI.
- **Pass** (after ≥14 days and ≥500 path entries): baseline-simulated avg net per trade > 0 with the 95% CI lower bound > 0, **and** live paper net > 0. Then Phase 6 sizing can be considered.
- **Fail:** the CI upper bound < 0 at any point after 7 days, or 14 days pass without a pass. Then switch back to `entry_model = "ensemble"` (or pause entries) and stop pursuing a TA-feature edge.

**7.4 Optional: limit-order entries** at FVG or structure levels, to cut the ~0.28% round-trip cost.

Each step pauses for approval.

---

## Phase 8 — New data sources (does derivatives / order-flow data carry an edge?)

Phase 7 found nothing in price/volume features after costs. Phase 8 tests data the bot has never used, all from Binance public futures endpoints (no keys):
- **Open interest** (`/futures/data/openInterestHist`, 15m, ~30 days of history);
- **Taker buy/sell volume ratio** (`/futures/data/takerlongshortRatio`, 15m);
- **Long/short ratios:** global accounts, top-trader accounts and top-trader positions (15m);
- **Taker buy share per candle** from klines (`taker buy quote / quote volume`);
- **Funding rate** (last settled, 8h).

**8.1 Research (read-only).** For the bot's universe:
- Fetch the last ~30 days at 15m and align every series to closed 15m bars, lagged one extra bar so nothing can leak.
- Train pooled LightGBM on two targets: the path label (the bot's exits, realistic costs) and 1h / 4h direction.
- Compare **price-only** vs **price + new data** on a chronological 60/20/20 split with gaps.
- Success: the new data lifts the top-decile average net per trade above zero, with the 95% CI lower bound > 0. Caveat: ~30 days of history means a test slice of only ~6 days, so CIs will be wide. A pass would need a forward test before any build.

**8.1 Result (Oct 15) — passes, with caveats.** Data: 80 most liquid symbols in the universe, 29 days (Sep 17 → Oct 15), 214k bars; 81% of rows have every new series. Costs: 0.16% round trip (0.05% fee + 0.03% slippage per fill, +0.05% on stop exits).
- **Direction stays unpredictable** with the new data: 1h / 4h AUC 0.50–0.52, all top deciles negative after costs.
- **Path label:** walk-forward over three consecutive, non-overlapping test windows (~4.3 days each), each trained only on earlier data. Avg net per trade ± 95% CI:

| Features | Fold 1 (Oct 2–6) top 10% | Fold 2 (Oct 6–11) top 10% | Fold 3 (Oct 11–15) top 10% | Top 2%, folds 1/2/3 |
|---|---|---|---|---|
| Price only | +0.017 ± 0.036 | +0.020 ± 0.036 | −0.023 ± 0.038 | +0.053 / +0.177 / −0.127 |
| Price + new | +0.056 ± 0.037 | +0.039 ± 0.036 | −0.025 ± 0.040 | +0.055 / +0.050 / −0.009 |
| **Price + new, no hour-of-day** | **+0.073 ± 0.039** | **+0.069 ± 0.037** | **+0.061 ± 0.041** | +0.081 / +0.102 / +0.085 |

- **Price + new data without the hour feature is positive in all three folds,** with the top-10% CI lower bound above zero each time; 14 of 16 test days were positive. Hour-of-day was overfitting a 3-week sample, and dropping it removed the fold-3 failure.
- The top features in the earlier single split included `funding_z`, `top_pos_ratio`, `gl_ratio` and `funding`, so the derivatives data is doing real work.
- **Caveats:**
  - Many configurations were tested across Phases 7–8, so some chance of a false positive remains.
  - The windows are only ~13 test days in total.
  - Simulated on 15m bars, with assumed stop slippage.
  - Top 10% would be ~750 trades/day, far above the bot's capacity (8 concurrent). At a realistic top ~2%, the point estimates are positive (+0.08 to +0.10%), but the CIs are ±0.09%.
- **The forward test is the decider.** Binance only keeps ~30 days of these series, so collection should start now; history not collected is lost.

**8.2 Build (spec, Oct 15; awaiting approval).** Each step pauses for approval.
- **8.2a Collector (start ASAP; history beyond 30 days only exists if we store it).**
  - New table `derivs_15m`: symbol, open_time, oi_value, taker_ratio, gl_ratio, top_pos_ratio, taker_buy_quote, quote_volume, funding; unique on (symbol, open_time).
  - A bot task runs 2 min after each 15m close. For the 80 most liquid universe symbols it fetches the latest bars from the 4 `futures/data` endpoints and klines (taker buy volume), plus funding hourly. Throttled to ≤180 requests/min (~400 per cycle, ~2.5 min).
  - On first run it backfills the last 29 days (~20 min, throttled).
  - Tests: timestamp alignment, upsert idempotency, throttle.
- **8.2a status (Oct 15): ✅ built, running.**
  - Code: `bot/data/derivs.py` (collector), `DerivsBar` → `derivs_15m`, the `derivs_collector` task in `main.py`, and 5 tests (`bot/tests/test_derivs.py`).
  - Found while testing: past the newest period, the taker-ratio endpoint returns its latest row again, which made paging loop forever. Paging now stops when a page makes no progress (regression test added).
  - Verified on 2 symbols: 29 days = 2,785 bars, ~100% coverage on every series; an incremental pass took 7s.
  - Full 80-symbol backfill started with the bot restart (~20 min); each later cycle takes ~5 min, 2 min after every 15m close.
- **8.2b Path model v2.** Features = price set without hour (`ret_1/4/16/96`, `atr_pct`, `vol_z`) + the 17 derivatives features from `derivs_15m`, using the same one-bar extra lag. Universe = top-80 liquid. Nightly training writes a **3-fold walk-forward report** to `path_model.json`. The entry threshold is sized to bot capacity (~top 2–3%).
- **8.2b status (Oct 15): ✅ built.**
  - `bot/analysis/path_model.py` v2: price features without hour + 17 derivs features, lagged one bar; 3-fold walk-forward report; live model refit with the median fold trees; threshold = top 2%. 4 tests.
  - The structure switch added to `features.py` in 7.3a was reverted (unused).
  - First production training: 83 symbols, 219k rows (Sep 17 → Oct 15), trees 132/112, threshold 0.6213. Walk-forward, avg net per trade ± 95% CI:

| Window | Top 10% | Top 5% | Top 2% |
|---|---|---|---|
| Fold 1 (Oct 2–7) | +0.035 ± 0.039 | +0.061 ± 0.055 | +0.081 ± 0.085 |
| Fold 2 (Oct 7–11) | +0.063 ± 0.038 | +0.096 ± 0.055 | +0.043 ± 0.088 |
| Fold 3 (Oct 11–15) | +0.037 ± 0.040 | +0.047 ± 0.058 | +0.113 ± 0.089 |
| **Pooled (14 days)** | **+0.045 ± 0.023** | **+0.069 ± 0.032** | **+0.079 ± 0.051** |

  - All 9 fold cells are positive. In the pooled view every CI lower bound is > 0 (top 2%: +0.028), win rate 61–63%, 11 of 14 days positive.
  - It is still a backtest on ~14 days, with 15m-bar exit simulation and assumed stop slippage. The forward test (8.2d) decides.
- **8.2c Wire into the bot (paper only)** exactly as specced in 7.3b, behind `entry_model = "path"`.
- **8.2c status (Oct 15): ✅ built; `ENTRY_MODEL=path` set in `.env` (paper).**
  - **Trainer:** in path mode the ensemble isn't trained or consulted. `latest_signal()` scores the newest closed 15m bar; below threshold = no trade. ATR is the 15m value the labels used. Path scores are saved in the prediction snapshot. `run_analysis` skips the old confidence factors.
  - **Executor, path mode:**
    - live entries refused;
    - researcher learning block, regime/funding confidence tweaks, SELL floor, gate veto, and researcher size scaling all skipped (logged as "would have");
    - fixed ATR stop/TP instead of zone stops;
    - researcher invalidation / structure-flip exits skipped for path trades (`entry_indicators.entry_model`).
  - **Baseline:** gains an `entry_model` group.
  - **Two live-data issues found before go-live, both fixed, with a retrain each time:**
    1. **Newest bar timing.** The collector stores a bar ~2 min after close and analysis runs ~1 min after. `vol_z` now uses candle volume, the join keeps the newest bar, and derivs features are lagged **2 bars**, because some periods publish >15 min late.
    2. **Open interest arrives ~2h late** on Binance for ~30 symbols (e.g. XAU, TradFi-linked). The OI features were complete in training but missing at decision time, so they were **dropped**.
  - **Final model** (no OI, 2-bar lag): 13 derivs + 6 price features, threshold 0.6185. Walk-forward pooled: top 10% +0.049 ± 0.023, top 5% +0.072 ± 0.032, **top 2% +0.073 ± 0.051 (low +0.022)**; every fold positive. Live coverage: 78/100 analysed symbols score (the rest are outside the collector's top 80 or have gaps).
  - 33 tests pass.
- **8.2d started 2026-10-15 13:49 UTC** (first path trades: CRCL and COIN SELL, 0.80% stop / 1.20% TP, ~$75 notional). Check-ins:
  - Windows task "InsideTrader Forward Check" runs `bot/tools/ai_review/forward_check.ps1` on **Oct 22 and Oct 29 at 09:07**. It shows live paper stats with a pass/fail hint (pop-up plus `...\InsideTrader-ai-review\logsorward_check.log`).
  - In-session reminders are set for the same days at 09:11, if this Claude session is still open.
- **8.2d Forward test** with the 7.3c pass/fail rules (≥14 days, ≥500 entries, CI lower bound > 0 in both the baseline sim and live paper P&L).
