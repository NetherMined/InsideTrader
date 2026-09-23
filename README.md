# InsideTrader

An ML-powered Binance auto-trading bot with a Next.js management dashboard. Supports paper trading and live trading, spot and futures, with real-time WebSocket price feeds and a full risk management system.

## Features

- **ML Predictions** — XGBoost model with 45 technical features (RSI, MACD, Bollinger Bands, Stochastic, OBV, regime detection, and more)
- **Performance Tracker** — Per-symbol win-rate history adjusts confidence multipliers per asset (0.90–1.10x), requires minimum 10 trades
- **Regime Filter** — ADX-based market regime detection; ranging markets (ADX<20) require RSI extremes, transitional markets raise threshold +0.05
- **Dynamic Mode** — Automatically switches between SPOT and FUTURES per trade based on confidence
- **Risk Management** — Per-trade stop-loss/take-profit, daily loss kill-switch, position sizing, negative trade timeout
- **Trading Levels** — Conservative / Balanced / Aggressive presets, live-switchable from the dashboard
- **Trading Goals** — Set a USDT target over a time period; bot adjusts confidence thresholds to pace toward the goal
- **P&L Analytics** — Period-based P&L stats (1h / 24h / 7d / 30d / ALL) split by realized and unrealized
- **Paper Trading** — Full paper simulation with realistic margin accounting for futures
- **Live Trading** — Direct Binance order execution via ccxt (spot + futures with 5x leverage)
- **Next.js Dashboard** — 9 pages with live WebSocket prices, bot controls, P&L charts, open positions, trade history, backtests, and settings
- **Production Ready** — Docker Compose, Nginx reverse proxy, one-command VPS deploy script

## Tech Stack

| Layer | Technology |
|---|---|
| Bot Engine | Python 3.12, ccxt, pandas-ta, XGBoost |
| API | FastAPI, SQLAlchemy 2.0 async, asyncpg |
| Database | PostgreSQL |
| Cache / State | Redis |
| Frontend | Next.js 15, TypeScript, Tailwind CSS, Recharts |
| Deployment | Docker Compose, Nginx |

## Project Structure

```
InsideTrader/
├── api/                  # FastAPI backend (REST + WebSocket)
├── bot/
│   ├── analysis/         # ML model, features, backtester, ranker, regime detection
│   ├── data/             # Market scanner, OHLCV fetcher, Redis stream
│   ├── db/               # SQLAlchemy models and connection
│   ├── notifications/    # Telegram alerts
│   └── trading/          # Executor, journal, orders, paper trading, risk, sizing
├── frontend/             # Next.js 15 dashboard
│   ├── app/              # Pages: dashboard, markets, predictions, positions, history, backtests, settings
│   ├── components/       # Shared UI components
│   └── lib/              # API client, types, utilities
├── nginx/                # Reverse proxy config
├── docker-compose.yml        # Dev (source volume mounts)
├── docker-compose.prod.yml   # Prod (standalone, no source mounts)
└── deploy.sh             # One-command VPS deploy
```

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Binance account with API key (spot; enable futures permission for futures trading)

### Setup

1. Copy the environment template and fill in your values:

```bash
cp .env.example .env
```

2. Set at minimum:

```env
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
POSTGRES_PASSWORD=changeme
SECRET_KEY=changeme
```

3. Start in development mode:

```bash
docker compose up --build
```

The dashboard will be available at `http://localhost:3000` and the API at `http://localhost:8000`.

### Production Deploy

```bash
# On your VPS, set PROD_URL in .env then run:
./deploy.sh
```

This uses `docker-compose.prod.yml` with Nginx on port 80.

## Dashboard Pages

| Page | Description |
|---|---|
| `/dashboard` | Bot controls, live stats, P&L chart, open positions |
| `/markets` | Searchable live market table |
| `/predictions` | ML forecasts with feature signal breakdown |
| `/positions` | Open positions with manual close |
| `/history` | Closed trade history and win rate stats |
| `/backtests` | Backtest results and performance charts |
| `/user-stats` | Profit on initial investment summary and user performance stats |
| `/settings` | Bot configuration, trading mode, Binance account |

## Configuration

Key settings are stored in Redis and can be changed live from the Settings page without restarting the bot:

| Setting | Default | Description |
|---|---|---|
| Trading Level | BALANCED | Conservative / Balanced / Aggressive |
| Max Concurrent Trades | 20 | Open positions cap |
| Min Daily Trades | 50 | Bot lowers confidence threshold if below target |
| Futures Leverage | 5x | Applied to all futures positions |
| Confidence Threshold | 0.55 | Minimum ML confidence to open a trade |
| Stop-Loss | 1.5% | Per-trade stop-loss |
| Take-Profit | 2.0% | Per-trade take-profit |
| Daily Loss Limit | 10% | Kill-switch threshold |

## Environment Variables

See `.env.example` for the full list. Never commit `.env`.

## License

Private repository. All rights reserved.
