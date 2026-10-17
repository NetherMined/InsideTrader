# Test Results page in the app

## Goal

A "Test Results" page in the Next.js dashboard that holds all testing history and results (Phases 1–6 from
Oct 16–17, 2026 and every future review), plus the live side-by-side test of the current bot vs the trend strategy.

## Data

Two kinds of data, kept apart:

1. **Research results (fixed once a test is done).** Stored in a new table `research_results`:
   `key` (PK, e.g. `exit_replay`), `title`, `description`, `phase`, `rows` (JSON array), `updated_at`.
   - Source files are committed in `bot/results/*.json` (inside `bot/` so the bot image and container can read them). These are the outputs already produced: phases, bot
     exits, bot daily P&L, exit replay, signal scan, horizon scan, daily strategies, robustness, equity curves,
     day-trading setups.
   - `python -m bot.tools.publish_research` loads every file in `bot/results/` into the table (upsert), so a new test result
     is added by dropping a JSON file in and running it. The testing history (`phases.json`) is updated the same way.
2. **Live side-by-side test (changes hourly).**
   - Daily balances come from the existing `strategy_equity` table, trades from `trend_fills` and `trades`.
   - The trend runner also writes a live summary to Redis key `trend:summary` every cycle: both bots' equity at live
     prices, open positions, and trend holdings with current price and P&L. The API then needs no Binance calls.

## API (FastAPI, `api/main.py`)

- `GET /api/v1/research`: list of result sets (key, title, description, phase, updated_at, row count).
- `GET /api/v1/research/{key}`: one result set with its rows.
- `GET /api/v1/research/comparison`: live side-by-side data: summary per bot (start, equity now, return, worst
  drawdown, trades, open positions, as-of time), balance per day for both bots, trend holdings.
- Pydantic response models like the other endpoints; read-only, so no API key needed.

## Frontend (`frontend/app/test-results/page.tsx`)

Same look as the other pages (dark cards, `#f0b90b` accent, Recharts). Sidebar entry "Test Results" (FlaskConical
icon) after Backtests. Tabs:

| Tab | Content |
|---|---|
| Overview | Headline cards (trend strategy return/drawdown, robustness, current bot last 48h, profitable day-trading setups); growth-of-$1 chart for the daily strategies; every strategy ranked by Sharpe with a verdict |
| Testing history | Timeline of phases: question, method, finding, decision, status |
| Current bot | Exits table, P&L per day chart, exit replay table, signal scan and horizon scan explorers (pick a breakdown) |
| Strategy research | Head-to-head table with yearly returns, robustness table |
| Day-trading setups | Before/after cost chart per setup vs random entries, searchable table of all runs |
| Side-by-side test | Live cards for both bots, balance per day chart, trend holdings table; refreshes every 60 seconds |

Formatting: percentages signed and colour-coded (green up / red down), dollars with 2 decimals, Sharpe 2 decimals.
Works at phone width (cards stack, tables scroll sideways inside their card).

## Phases (pause after each for confirmation)

### Phase 1: Storage and API (done Oct 17)
10 result sets in `bot/results/` published to `research_results`; trend runner writes `trend:summary` each cycle;
`api/research.py` router with the three endpoints (API on port 8800); tests: `bot/tests/test_publish_research.py` (3),
`api/tests/test_research.py` (3).

- Copy the result JSON files into `bot/results/` (normalised: tidy table names, row ids where needed).
- `ResearchResult` model + table; `bot/tools/publish_research.py`; run it once.
- Trend runner writes `trend:summary` each cycle (restart the `trend` container).
- API endpoints + response models. Tests for the publish tool (file → rows) and the comparison builder.
- Check: curl each endpoint returns the expected row counts.

### Phase 2: Page (done Oct 17)
`app/test-results/page.tsx` + `components/test-results/` (ui, OverviewTab, HistoryTab, BotTab, StrategiesTab, DayTradeTab,
SideBySideTab), API types/client, sidebar link. `next build` with type check passes. No ESLint config exists in the
project, so lint was not run.

- `lib/types.ts` + `lib/api.ts` additions, `app/test-results/page.tsx` (split into components under
  `components/test-results/` if it grows past ~400 lines), sidebar link.
- `npm run build` and `npm run lint` pass.

### Phase 3: Ship and verify (done Oct 17)
Frontend rebuilt; every tab checked in headless Edge against the running app; old export tool removed.

- Rebuild the `frontend` container (and restart `api` if needed). Open the page in the running app and check each tab
  loads with data.
- Remove the old export tool and outdated references from the plans.

## Out of scope
- Editing results from the page (read-only).
- Changes to either bot's trading logic.
