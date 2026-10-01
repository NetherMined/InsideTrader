# InsideTrader — System Reference

## What It Is

InsideTrader is a fully automated cryptocurrency trading bot for Binance. It uses machine learning (XGBoost) to predict short-term price direction per symbol, ranks opportunities by risk-adjusted score, and executes trades autonomously across SPOT and FUTURES markets. A Next.js dashboard provides live monitoring and control. The entire stack runs in Docker Compose — locally or on a VPS.

---

## Directory Structure

```
InsideTrader/
├── bot/                          # Trading bot engine (Python 3.12)
│   ├── analysis/                 # ML pipeline and market analysis
│   │   ├── features.py           # Feature engineering (45 features from OHLCV)
│   │   ├── indicators.py         # Technical indicators via pandas-ta
│   │   ├── model.py              # XGBoost regressor + classifier wrappers
│   │   ├── trainer.py            # Analysis orchestrator (load → train → predict → rank)
│   │   ├── ranker.py             # Pair opportunity scorer and sorter
│   │   ├── regime_detector.py    # TRENDING/RANGING/TRANSITION classification
│   │   ├── mode_classifier.py    # SPOT vs FUTURES routing logic
│   │   ├── backtest.py           # Historical strategy backtesting
│   │   ├── funding_rate.py       # Binance futures funding rate fetcher
│   │   └── performance_tracker.py # Per-symbol 30-day win-rate tracker
│   ├── data/                     # Market data pipeline
│   │   ├── scanner.py            # Valid USDT pair scanner (~67 pairs)
│   │   ├── fetcher.py            # OHLCV candle fetcher (public Binance API)
│   │   └── stream.py             # Live price stream → Redis
│   ├── db/                       # Database layer
│   │   ├── models.py             # SQLAlchemy async models
│   │   └── connection.py         # PostgreSQL async connection pool
│   ├── trading/                  # Trade execution
│   │   ├── executor.py           # Main trading loop (~1190 lines)
│   │   ├── orders.py             # Live order placement via ccxt
│   │   ├── paper.py              # Paper trade simulator
│   │   ├── journal.py            # Trade persistence to PostgreSQL
│   │   ├── risk.py               # Risk checks, limits, Redis state helpers
│   │   └── sizing.py             # Position sizing and SL/TP price calculation
│   ├── notifications/
│   │   ├── telegram.py           # Telegram bot alerts
│   │   └── events.py             # Notification event types
│   ├── config.py                 # Pydantic settings (reads .env)
│   └── main.py                   # Bot entry point
├── api/                          # FastAPI REST API + WebSocket
│   ├── routers/                  # Route handlers (bot, trades, predictions, settings, account)
│   ├── config.py                 # API-specific settings
│   └── main.py                   # FastAPI app, WebSocket /ws/prices
├── frontend/                     # Next.js 15 management dashboard
│   ├── app/                      # App Router pages
│   │   ├── dashboard/            # Bot controls, stats, P&L chart, open positions
│   │   ├── markets/              # Searchable market table
│   │   ├── predictions/          # ML forecasts + feature signals panel
│   │   ├── positions/            # Open positions with close button
│   │   ├── history/              # Trade history + win rate stats
│   │   ├── backtests/            # Backtest results + bar chart
│   │   └── settings/             # Health, config, trading level, live mode
│   ├── lib/                      # API client, types, WebSocket hook
│   └── components/               # Shared UI components
├── nginx/
│   └── nginx.conf                # Reverse proxy: / → frontend, /api → FastAPI
├── docker-compose.yml            # Dev: source volume mounts, no nginx
├── docker-compose.prod.yml       # Prod: pre-built images, nginx on port 80
├── deploy.sh                     # One-command VPS deployment script
├── .env                          # Active configuration (not committed)
└── .env.example                  # Safe configuration template (committed)
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Bot engine | Python 3.12, asyncio |
| ML models | XGBoost (XGBRegressor + XGBClassifier) |
| Technical analysis | pandas-ta >= 0.4.67b0, pandas, numpy >= 2.2.6 |
| Exchange | Binance via ccxt >= 4.3.0 |
| Database | PostgreSQL — SQLAlchemy 2.0 async + asyncpg |
| Cache / state | Redis — aioredis |
| REST API | FastAPI (Python) |
| WebSocket | FastAPI WebSocket at `/ws/prices` |
| Dashboard | Next.js 15, TypeScript, Tailwind CSS, Recharts, Lucide |
| Logging | Loguru (structured JSON logs) |
| Deployment | Docker Compose, Nginx |

---

## Docker Services

| Service | Image | Role |
|---------|-------|------|
| `bot` | `insidetrader-bot` | Trading engine — analysis + executor |
| `api` | `insidetrader-api` | FastAPI REST + WebSocket |
| `frontend` | `insidetrader-frontend` | Next.js dashboard (standalone output) |
| `postgres` | `postgres:15` | Trade/candle/prediction storage |
| `redis` | `redis:7` | Live prices, bot state, cooldowns |
| `nginx` | `nginx:alpine` | Reverse proxy (prod only) |

**Inter-service networking:** All services communicate on the internal Docker bridge network. Redis is never exposed externally. PostgreSQL is exposed on port 5432 (dev only).

---

## Configuration

All runtime configuration is in `.env` and read by `bot/config.py` (Pydantic `BaseSettings`).

### Key Environment Variables

```
# Exchange
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
BINANCE_TESTNET_API_KEY=...
BINANCE_TESTNET_API_SECRET=...

# Trading mode
USE_TESTNET=false
PAPER_TRADING_MODE=false
LIVE_TRADING_ENABLED=true
TRADING_MODE=DYNAMIC           # SPOT_ONLY | FUTURES_ONLY | DYNAMIC
FUTURES_LEVERAGE=5
MAX_CONCURRENT_TRADES=20
MIN_DAILY_TRADES=50
MIN_CONCURRENT_TRADES=5

# Risk
CONFIDENCE_THRESHOLD=0.55
RISK_PER_TRADE_PCT=2.0
STOP_LOSS_PCT=1.5
TAKE_PROFIT_PCT=2.0
DAILY_LOSS_KILL_SWITCH_PCT=5.0
NEGATIVE_TRADE_TIMEOUT_MINUTES=15

# Infrastructure
POSTGRES_URL=postgresql+asyncpg://...
REDIS_URL=redis://redis:6379
```

### Runtime Overrides (Redis)
Many config values can be overridden at runtime via Redis without restarting the bot. The executor reads these each loop tick:

| Redis Key | What It Controls |
|-----------|-----------------|
| `bot:trading_level` | CONSERVATIVE / BALANCED / AGGRESSIVE preset |
| `bot:paper_trading_mode` | Paper vs live mode switch |
| `bot:live_trading_enabled` | Live trading on/off |
| `bot:use_testnet` | Testnet vs mainnet |
| `bot:futures_leverage` | Futures leverage multiplier |
| `bot:max_concurrent_trades` | Hard cap on open positions |
| `bot:min_daily_trades` | Minimum daily trade target |
| `bot:min_concurrent_trades` | Minimum concurrent trade target |
| `bot:command` | `start` / `stop` bot signal |

---

## Database Schema (Key Tables)

### `candles`
OHLCV data per symbol and timeframe. Populated by the fetcher. Source data for ML training.

| Column | Type | Description |
|--------|------|-------------|
| symbol | varchar | e.g. `BTC/USDT` |
| timeframe | varchar | e.g. `1h` |
| open_time | timestamptz | Candle open time |
| open/high/low/close | numeric | Price OHLC |
| volume | numeric | Trade volume |

### `predictions`
ML prediction output per symbol per analysis run.

| Column | Type | Description |
|--------|------|-------------|
| symbol | varchar | Pair symbol |
| prediction_date | timestamptz | When prediction was made |
| current_price | numeric | Price at time of prediction |
| predicted_change_pct | numeric | ML regressor output (% change) |
| confidence | numeric | ML classifier probability (0–1) |
| mode_recommendation | varchar | SPOT or FUTURES |
| features | jsonb | All 45 feature values used |

### `positions`
All currently open positions. Rows exist only for live open trades (no status column — a row's presence means the position is open).

| Column | Type | Description |
|--------|------|-------------|
| id | serial | Position ID |
| trade_id | int | FK → trades |
| symbol | varchar | Pair symbol |
| side | varchar | BUY or SELL |
| mode | varchar | SPOT or FUTURES |
| entry_price | numeric | Fill price |
| quantity | numeric | Asset quantity held |
| notional_usdt | numeric | Position value in USDT |

### `trades`
Completed (closed) trade history.

| Column | Type | Description |
|--------|------|-------------|
| id | serial | Trade ID |
| symbol | varchar | Pair symbol |
| side | varchar | BUY or SELL |
| mode | varchar | SPOT or FUTURES |
| entry_price / exit_price | numeric | Fill prices |
| pnl_usdt | numeric | Realized P&L in USDT |
| opened_at / closed_at | timestamptz | Trade duration |
| close_reason | varchar | TP / SL / timeout / manual |

### `backtests`
Historical strategy test results stored per run.

---

## Build and Run

### Development
```bash
docker compose up --build
```
- Source code is volume-mounted into containers — code changes take effect on restart
- No nginx; frontend runs on port 3000, API on port 8000
- PostgreSQL accessible on localhost:5432

### Production
```bash
./deploy.sh
```
- Reads `.env` for `PROD_URL`
- Uses `docker-compose.prod.yml` — standalone/pre-built images, no source mounts
- Nginx serves everything on port 80 (`/` → frontend, `/api` → FastAPI, `/ws` → WebSocket)
- ML models persist in named Docker volume `ml_models`

### Rebuild Bot Only
```bash
docker compose build bot
docker compose up -d bot
```

---

## API Endpoints (Key Routes)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/bot/status` | Bot state, daily stats, open count |
| POST | `/api/v1/bot/start` | Start trading engine |
| POST | `/api/v1/bot/stop` | Stop trading engine |
| GET | `/api/v1/trades` | Trade history |
| GET | `/api/v1/positions` | Open positions |
| GET | `/api/v1/predictions` | Latest ML predictions |
| GET | `/api/v1/stats/pnl?period=1h\|24h\|7d\|30d\|all` | P&L summary |
| GET/POST | `/api/v1/settings/level` | Trading level (CONSERVATIVE/BALANCED/AGGRESSIVE) |
| GET/POST | `/api/v1/settings/live-mode` | Paper/live mode switch |
| GET/POST | `/api/v1/settings/goal` | Trading goal (amount + period) |
| GET/POST | `/api/v1/settings/trade-limits` | min_daily_trades, min_concurrent |
| GET | `/api/v1/account/balances` | Live Binance spot wallet |
| POST | `/api/v1/account/convert-all` | Convert all assets to USDT |
| WS | `/ws/prices` | Live price stream to dashboard |

---

## Branch Rules

| Merge | Allowed? |
|-------|---------|
| `Experiments` → `main` | Never |
| `Features` → `main` | Only when explicitly requested |
| `Experiments` ↔ `Features` | Only when explicitly requested |

Active development happens on `features` or `experiments`. Never merge experiments directly to main.
