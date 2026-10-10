"""InsideTrader API — Phase 2

Endpoints:
  GET  /health                        — DB, Redis, bot status
  GET  /api/v1/markets                — All scanned Binance markets
  GET  /api/v1/markets/top            — Top N pairs by volume
  GET  /api/v1/candles/{symbol}       — OHLCV candles for a symbol
  GET  /api/v1/prices                 — All current prices from Redis
  GET  /api/v1/prices/{symbol}        — Current price for one symbol
  GET  /api/v1/predictions            — Today's ML predictions (ranked)
  GET  /api/v1/predictions/{symbol}   — Latest prediction for a symbol
  GET  /api/v1/backtests              — Backtest results
  WS   /ws/prices                     — Real-time price stream
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import httpx
import ccxt.async_support as ccxt

from api.config import settings
from api.db import get_session, check_db, async_session as _db_session

_HEAT_LIMIT_PCT = 100.0

# re-export for inline use in endpoints
_starting_capital = settings.starting_capital_usdt

app = FastAPI(
    title="InsideTrader API",
    version="1.0.0",
    description="Auto trading bot management API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_cors_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        api_key = request.headers.get("X-Api-Key", "")
        if api_key != settings.dashboard_secret_key:
            from fastapi.responses import JSONResponse
            response = JSONResponse({"detail": "Invalid or missing API key"}, status_code=403)
            origin = request.headers.get("origin", "")
            if origin in _cors_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
            return response
    return await call_next(request)


async def get_redis() -> aioredis.Redis:
    if settings.redis_password:
        url = f"redis://:{settings.redis_password}@{settings.redis_host}:{settings.redis_port}/0"
    else:
        url = f"redis://{settings.redis_host}:{settings.redis_port}/0"
    return await aioredis.from_url(url, encoding="utf-8", decode_responses=True)


class HealthResponse(BaseModel):
    status: str
    db: str
    redis: str
    mode: str
    paper_trading: bool
    testnet: bool
    timestamp: str


class MarketResponse(BaseModel):
    symbol: str
    base: str
    quote: str
    market_type: str
    volume_24h_usdt: float
    active: bool
    enabled_for_trading: bool


class CandleResponse(BaseModel):
    symbol: str
    timeframe: str
    open_time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class PriceResponse(BaseModel):
    symbol: str
    price: float
    bid: float
    ask: float
    change_pct: float
    volume_24h: float
    ts: str


@app.get("/health", response_model=HealthResponse)
async def health(session: Annotated[AsyncSession, Depends(get_session)]):
    db_ok = await check_db()

    redis_ok = False
    try:
        redis = await get_redis()
        await redis.ping()
        await redis.aclose()
        redis_ok = True
    except Exception:
        pass

    return HealthResponse(
        status="ok" if (db_ok and redis_ok) else "degraded",
        db="connected" if db_ok else "error",
        redis="connected" if redis_ok else "error",
        mode="TESTNET" if settings.use_testnet else "LIVE",
        paper_trading=settings.paper_trading_mode,
        testnet=settings.use_testnet,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/v1/markets", response_model=list[MarketResponse])
async def get_markets(
    session: Annotated[AsyncSession, Depends(get_session)],
    market_type: str | None = Query(None, description="spot | future"),
    limit: int = Query(100, le=500),
):
    query = text("""
        SELECT symbol, base, quote, market_type, volume_24h_usdt, active, enabled_for_trading
        FROM scanned_markets
        WHERE active = true
        {type_filter}
        ORDER BY volume_24h_usdt DESC
        LIMIT :limit
    """.format(type_filter="AND market_type = :market_type" if market_type else ""))

    params: dict = {"limit": limit}
    if market_type:
        params["market_type"] = market_type

    result = await session.execute(query, params)
    rows = result.mappings().all()
    return [MarketResponse(**dict(r)) for r in rows]


@app.get("/api/v1/markets/top", response_model=list[MarketResponse])
async def get_top_markets(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, le=200),
):
    result = await session.execute(
        text("""
            SELECT symbol, base, quote, market_type, volume_24h_usdt, active, enabled_for_trading
            FROM scanned_markets
            WHERE active = true AND enabled_for_trading = true
            ORDER BY volume_24h_usdt DESC
            LIMIT :limit
        """),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return [MarketResponse(**dict(r)) for r in rows]


_CANDLE_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
_CANDLE_TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


@app.get("/api/v1/candles", response_model=list[CandleResponse])
async def get_candles(
    session: Annotated[AsyncSession, Depends(get_session)],
    symbol: str = Query(..., description="Trading pair, e.g. BTC/USDT"),
    timeframe: str = Query("1h"),
    limit: int = Query(200, le=1000),
):
    if timeframe not in _CANDLE_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {', '.join(_CANDLE_TIMEFRAMES)}",
        )
    result = await session.execute(
        text("""
            SELECT symbol, timeframe,
                   open_time AT TIME ZONE 'UTC' AS open_time,
                   open, high, low, close, volume
            FROM candles
            WHERE symbol = :symbol AND timeframe = :timeframe
            ORDER BY open_time DESC
            LIMIT :limit
        """),
        {"symbol": symbol, "timeframe": timeframe, "limit": limit},
    )
    rows = result.mappings().all()

    # Symbols outside the bot's tracked set have old rows in the DB; only trust
    # stored candles when the newest one is recent, otherwise use Binance below.
    if rows and rows[0]["open_time"]:
        newest = rows[0]["open_time"]
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        max_age = _CANDLE_TIMEFRAME_SECONDS[timeframe] * 3
        if (datetime.now(timezone.utc) - newest).total_seconds() > max_age:
            rows = []

    if rows:
        return [
            CandleResponse(
                symbol=r["symbol"],
                timeframe=r["timeframe"],
                open_time=(
                    r["open_time"].replace(tzinfo=timezone.utc).isoformat()
                    if r["open_time"] else ""
                ),
                open=r["open"],
                high=r["high"],
                low=r["low"],
                close=r["close"],
                volume=r["volume"],
            )
            for r in rows
        ]

    # Fallback: Binance public REST. USDM futures first (the bot trades futures-only
    # symbols that spot does not list), then spot.
    binance_symbol = symbol.replace("/", "")
    klines = None
    last_error = ""
    for base in (
        "https://fapi.binance.com/fapi/v1/klines",
        "https://api.binance.com/api/v3/klines",
    ):
        url = f"{base}?symbol={binance_symbol}&interval={timeframe}&limit={limit}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                klines = resp.json()
            if klines:
                break
        except Exception as e:
            last_error = str(e)
    if not klines:
        raise HTTPException(status_code=404, detail=f"No candles found for {symbol}: {last_error}")
    klines = list(reversed(klines))

    return [
        CandleResponse(
            symbol=symbol,
            timeframe=timeframe,
            open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        )
        for k in klines
    ]


@app.get("/api/v1/prices", response_model=list[PriceResponse])
async def get_all_prices():
    redis = await get_redis()
    try:
        keys = await redis.keys("price:*")
        if not keys:
            return []

        values = await redis.mget(keys)
        prices = []
        for raw in values:
            if raw:
                try:
                    prices.append(PriceResponse(**json.loads(raw)))
                except Exception:
                    pass
        return sorted(prices, key=lambda p: p.volume_24h, reverse=True)
    finally:
        await redis.aclose()


@app.get("/api/v1/prices/{symbol}", response_model=PriceResponse)
async def get_price(symbol: str):
    redis = await get_redis()
    try:
        raw = await redis.get(f"price:{symbol}")
        if not raw:
            raise HTTPException(status_code=404, detail=f"No price data for {symbol}")
        return PriceResponse(**json.loads(raw))
    finally:
        await redis.aclose()


class PredictionResponse(BaseModel):
    symbol: str
    prediction_date: str
    current_price: float
    target_price: float
    predicted_change_pct: float
    confidence: float
    mode_recommendation: str
    features: dict | None = None


class BacktestResponse(BaseModel):
    strategy: str
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    total_trades: int
    win_rate: float | None
    sharpe_ratio: float | None
    max_drawdown: float | None
    total_return_pct: float | None


@app.get("/api/v1/predictions", response_model=list[PredictionResponse])
async def get_predictions(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, le=200),
    mode: str | None = Query(None, description="SPOT | FUTURES"),
):
    """Return the latest prediction per symbol, sorted by predicted change descending."""
    where = "AND mode_recommendation = :mode" if mode else ""
    result = await session.execute(
        text(f"""
            SELECT DISTINCT ON (symbol)
                symbol,
                prediction_date AT TIME ZONE 'UTC' AS prediction_date,
                current_price, target_price,
                predicted_change_pct, confidence,
                mode_recommendation, features
            FROM predictions
            {where}
            ORDER BY symbol, prediction_date DESC
            LIMIT :limit
        """),
        {"limit": limit, **({"mode": mode} if mode else {})},
    )
    rows = result.mappings().all()
    return [
        PredictionResponse(
            symbol=r["symbol"],
            prediction_date=r["prediction_date"].isoformat() if r["prediction_date"] else "",
            current_price=r["current_price"],
            target_price=r["target_price"],
            predicted_change_pct=r["predicted_change_pct"],
            confidence=r["confidence"],
            mode_recommendation=r["mode_recommendation"],
            features=r["features"],
        )
        for r in sorted(rows, key=lambda x: x["predicted_change_pct"], reverse=True)
    ]


@app.get("/api/v1/predictions/{symbol}", response_model=PredictionResponse)
async def get_prediction(symbol: str, session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(
        text("""
            SELECT symbol,
                   prediction_date AT TIME ZONE 'UTC' AS prediction_date,
                   current_price, target_price,
                   predicted_change_pct, confidence,
                   mode_recommendation, features
            FROM predictions
            WHERE symbol = :symbol
            ORDER BY prediction_date DESC
            LIMIT 1
        """),
        {"symbol": symbol},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"No prediction found for {symbol}")
    return PredictionResponse(
        symbol=row["symbol"],
        prediction_date=row["prediction_date"].isoformat() if row["prediction_date"] else "",
        current_price=row["current_price"],
        target_price=row["target_price"],
        predicted_change_pct=row["predicted_change_pct"],
        confidence=row["confidence"],
        mode_recommendation=row["mode_recommendation"],
        features=row["features"],
    )


@app.get("/api/v1/backtests", response_model=list[BacktestResponse])
async def get_backtests(
    session: Annotated[AsyncSession, Depends(get_session)],
    symbol: str | None = Query(None),
    strategy: str | None = Query(None, description="spot_only | dynamic"),
    limit: int = Query(50, le=200),
):
    filters = []
    params: dict = {"limit": limit}
    if symbol:
        filters.append("symbol = :symbol")
        params["symbol"] = symbol
    if strategy:
        filters.append("strategy = :strategy")
        params["strategy"] = strategy

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    result = await session.execute(
        text(f"""
            SELECT strategy, symbol, timeframe,
                   start_date AT TIME ZONE 'UTC' AS start_date,
                   end_date AT TIME ZONE 'UTC' AS end_date,
                   total_trades, win_rate, sharpe_ratio, max_drawdown, total_return_pct
            FROM backtest_results
            {where}
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        params,
    )
    rows = result.mappings().all()
    return [
        BacktestResponse(
            strategy=r["strategy"],
            symbol=r["symbol"],
            timeframe=r["timeframe"],
            start_date=r["start_date"].isoformat() if r["start_date"] else "",
            end_date=r["end_date"].isoformat() if r["end_date"] else "",
            total_trades=r["total_trades"],
            win_rate=r["win_rate"],
            sharpe_ratio=r["sharpe_ratio"],
            max_drawdown=r["max_drawdown"],
            total_return_pct=r["total_return_pct"],
        )
        for r in rows
    ]


class PositionResponse(BaseModel):
    id: int
    symbol: str
    side: str
    mode: str
    entry_price: float
    current_price: float | None
    quantity: float
    leverage: int
    stop_loss_price: float | None
    take_profit_price: float | None
    unrealized_pnl: float
    paper_trade: bool
    opened_at: str


class TradeResponse(BaseModel):
    id: int
    symbol: str
    side: str
    mode: str
    entry_price: float
    exit_price: float | None
    quantity: float
    leverage: int
    pnl_usdt: float | None
    pnl_percent: float | None
    status: str
    paper_trade: bool
    opened_at: str
    closed_at: str | None


class PnlPeriodResponse(BaseModel):
    period: str
    total_profit_usdt: float
    total_loss_usdt: float
    win_count: int
    loss_count: int
    net_usdt: float


class ModeStatsItem(BaseModel):
    total_profit_usdt: float
    total_loss_usdt: float
    win_count: int
    loss_count: int
    net_usdt: float


class ModeBreakdownResponse(BaseModel):
    spot: ModeStatsItem
    futures: ModeStatsItem


class BotStatusResponse(BaseModel):
    state: str
    daily_pnl_pct: float
    daily_pnl_usdt: float
    open_trades: int
    capital_usdt: float
    paper: bool
    kill_switch: bool
    heat_limit_pct: float = 40.0
    open_heat_usdt: float = 0.0
    free_heat_usdt: float = 0.0
    total_profit_usdt: float
    total_loss_usdt: float
    win_count: int
    loss_count: int
    defensive_mode: bool = False
    started_with_usdt: float = 0.0
    futures_usdt: float = 0.0
    loss_level: int = 0
    loss_consecutive: int = 0
    loss_pause_minutes_left: float = 0.0
    loss_size_multiplier: float = 1.0


@app.get("/api/v1/positions", response_model=list[PositionResponse])
async def get_positions(session: Annotated[AsyncSession, Depends(get_session)]):
    result = await session.execute(
        text("""
            SELECT id, symbol, side, mode, entry_price, current_price,
                   quantity, leverage, stop_loss_price, take_profit_price,
                   unrealized_pnl, paper_trade,
                   opened_at AT TIME ZONE 'UTC' AS opened_at
            FROM positions
            ORDER BY opened_at DESC
        """)
    )
    rows = result.mappings().all()
    return [
        PositionResponse(
            **{k: (v.isoformat() if hasattr(v, "isoformat") else v)
               for k, v in dict(r).items()}
        )
        for r in rows
    ]


@app.get("/api/v1/trades", response_model=list[TradeResponse])
async def get_trades(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = Query(None, description="OPEN | CLOSED | CANCELLED"),
    limit: int = Query(100, le=500),
):
    if status:
        status = status.upper()
    status_clause = "AND status = :status" if status else ""
    result = await session.execute(
        text(f"""
            SELECT id, symbol, side, mode, entry_price, exit_price,
                   quantity, leverage, pnl_usdt, pnl_percent, status, paper_trade,
                   opened_at AT TIME ZONE 'UTC' AS opened_at,
                   closed_at AT TIME ZONE 'UTC' AS closed_at
            FROM trades
            WHERE (archived = false OR archived IS NULL) {status_clause}
            ORDER BY opened_at DESC
            LIMIT :limit
        """),
        {**({"status": status} if status else {}), "limit": limit},
    )
    rows = result.mappings().all()
    return [
        TradeResponse(
            **{k: (v.isoformat() if hasattr(v, "isoformat") else v)
               for k, v in dict(r).items()}
        )
        for r in rows
    ]


@app.get("/api/v1/bot/status", response_model=BotStatusResponse)
async def get_bot_status():
    redis = await get_redis()
    try:
        raw = await redis.get("bot:status")
        state_data = json.loads(raw) if raw else {}
        daily_pnl = float(await redis.get("bot:daily_pnl") or 0.0)
        kill = (await redis.get("bot:kill_switch") or "0") == "1"
        defensive = (await redis.get("bot:defensive_mode") or "0") == "1"
        loss_consec = int(await redis.get("bot:loss_consec") or 0)
        loss_pause_raw = await redis.get("bot:loss_pause_until")
        loss_pause_left = max(0.0, (float(loss_pause_raw) - datetime.now(timezone.utc).timestamp()) / 60) if loss_pause_raw else 0.0
        loss_half = loss_consec >= 3
        loss_level = 2 if loss_pause_left > 0 else (1 if loss_half else 0)
        redis_open_count = int(await redis.get("bot:open_count") or 0)
        open_count = state_data.get("open_trades", redis_open_count)
        paper_raw = await redis.get(_LIVE_MODE_KEYS["paper_trading_mode"])
        is_paper = (paper_raw == "true") if paper_raw is not None else settings.paper_trading_mode
        if is_paper:
            capital = float(await redis.get("paper:capital_usdt") or _starting_capital)
        else:
            try:
                testnet_raw = await redis.get(_LIVE_MODE_KEYS["use_testnet"])
                use_testnet = (testnet_raw == "true") if testnet_raw is not None else settings.use_testnet
                exchange = _get_futures_exchange(use_testnet)
                try:
                    bal = await exchange.fetch_balance()
                    capital = float(bal.get("USDT", {}).get("free", 0.0))
                finally:
                    await exchange.close()
            except Exception:
                bot_capital = state_data.get("capital_usdt")
                capital = float(bot_capital) if bot_capital is not None else _starting_capital
        daily_pnl_usdt_raw = await redis.get(_DAILY_PNL_USDT_KEY)
        daily_pnl_usdt = float(daily_pnl_usdt_raw) if daily_pnl_usdt_raw else round(daily_pnl * capital / 100, 4)

        heat_limit_pct = float(await redis.get("bot:heat_limit_pct") or _HEAT_LIMIT_PCT)
        open_heat_usdt = float(await redis.get("bot:open_heat_usdt") or 0.0)
        free_heat_usdt = float(await redis.get("bot:free_heat_usdt") or 0.0)

        pnl_summary = {"total_profit_usdt": 0.0, "total_loss_usdt": 0.0, "win_count": 0, "loss_count": 0}
        try:
            async with _db_session() as session:
                result = await session.execute(
                    text("""
                        SELECT
                            COALESCE(SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END), 0) AS total_profit_usdt,
                            COALESCE(SUM(CASE WHEN pnl_usdt < 0 THEN pnl_usdt ELSE 0 END), 0) AS total_loss_usdt,
                            COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) AS win_count,
                            COUNT(CASE WHEN pnl_usdt < 0 THEN 1 END) AS loss_count
                        FROM trades
                        WHERE status = 'CLOSED' AND (archived = false OR archived IS NULL)
                    """)
                )
                row = result.mappings().first()
                if row:
                    pnl_summary = {
                        "total_profit_usdt": round(float(row["total_profit_usdt"]), 4),
                        "total_loss_usdt": round(float(row["total_loss_usdt"]), 4),
                        "win_count": int(row["win_count"]),
                        "loss_count": int(row["loss_count"]),
                    }
        except Exception:
            pass

        if is_paper:
            start_raw = await redis.get("paper:starting_capital_usdt")
        else:
            start_raw = await redis.get("bot:session_start_capital")
        started_with_usdt = float(start_raw) if start_raw else capital

        futures_usdt = 0.0
        if not is_paper:
            try:
                testnet_raw = await redis.get(_LIVE_MODE_KEYS["use_testnet"])
                use_testnet = (testnet_raw == "true") if testnet_raw is not None else settings.use_testnet
                fex = _get_futures_exchange(use_testnet)
                try:
                    fbal = await fex.fetch_balance()
                    futures_usdt = float(fbal.get("USDT", {}).get("free", 0.0))
                finally:
                    await fex.close()
            except Exception:
                pass

        return BotStatusResponse(
            state=state_data.get("state", "unknown"),
            daily_pnl_pct=daily_pnl,
            daily_pnl_usdt=round(daily_pnl_usdt, 4),
            open_trades=open_count,
            capital_usdt=capital,
            paper=is_paper,
            kill_switch=kill,
            heat_limit_pct=heat_limit_pct,
            open_heat_usdt=round(open_heat_usdt, 2),
            free_heat_usdt=round(free_heat_usdt, 2),
            total_profit_usdt=pnl_summary["total_profit_usdt"],
            total_loss_usdt=pnl_summary["total_loss_usdt"],
            win_count=pnl_summary["win_count"],
            loss_count=pnl_summary["loss_count"],
            defensive_mode=defensive,
            started_with_usdt=round(started_with_usdt, 2),
            futures_usdt=round(futures_usdt, 2),
            loss_level=loss_level,
            loss_consecutive=loss_consec,
            loss_pause_minutes_left=round(loss_pause_left, 1),
            loss_size_multiplier=0.5 if loss_half else 1.0,
        )
    finally:
        await redis.aclose()


@app.get("/api/v1/settings/goal")
async def get_goal():
    raise HTTPException(status_code=410, detail="Goal feature removed — settings are locked")


@app.post("/api/v1/settings/goal")
async def set_goal():
    raise HTTPException(status_code=410, detail="Goal feature removed — settings are locked")


@app.get("/api/v1/settings/goal-enabled")
async def get_goal_enabled():
    raise HTTPException(status_code=410, detail="Goal feature removed — settings are locked")


@app.post("/api/v1/settings/goal-enabled")
async def set_goal_enabled():
    raise HTTPException(status_code=410, detail="Goal feature removed — settings are locked")




_PAPER_CAPITAL_KEY = "paper:capital_usdt"


class CapitalResponse(BaseModel):
    capital_usdt: float
    starting_capital_usdt: float


class SetCapitalRequest(BaseModel):
    capital_usdt: float


@app.get("/api/v1/settings/capital", response_model=CapitalResponse)
async def get_capital():
    redis = await get_redis()
    try:
        paper_raw = await redis.get(_LIVE_MODE_KEYS["paper_trading_mode"])
        is_paper = (paper_raw == "true") if paper_raw is not None else settings.paper_trading_mode
        if is_paper:
            raw = await redis.get(_PAPER_CAPITAL_KEY)
            capital = float(raw) if raw else _starting_capital
        else:
            try:
                exchange = _get_futures_exchange()
                try:
                    bal = await exchange.fetch_balance()
                    capital = float(bal.get("USDT", {}).get("free", 0.0))
                finally:
                    await exchange.close()
            except Exception:
                raw = await redis.get(_PAPER_CAPITAL_KEY)
                capital = float(raw) if raw else _starting_capital
        start_raw = await redis.get("paper:starting_capital_usdt")
        starting = float(start_raw) if start_raw else _starting_capital
        return CapitalResponse(capital_usdt=capital, starting_capital_usdt=starting)
    finally:
        await redis.aclose()


@app.post("/api/v1/settings/capital", response_model=CapitalResponse)
async def set_capital(request: SetCapitalRequest):
    if request.capital_usdt <= 0:
        raise HTTPException(status_code=400, detail="Capital must be greater than 0")
    redis = await get_redis()
    try:
        await redis.set(_PAPER_CAPITAL_KEY, str(request.capital_usdt))
        await redis.set("paper:starting_capital_usdt", str(request.capital_usdt))
        await redis.set("bot:kill_switch", "0")
        await redis.set("bot:defensive_mode", "0")
        await redis.set("bot:daily_pnl", "0.0")
        await redis.set("bot:daily_pnl_usdt", "0.0")
        await redis.set("bot:command", "restart")
        return CapitalResponse(capital_usdt=request.capital_usdt, starting_capital_usdt=request.capital_usdt)
    finally:
        await redis.aclose()


_DAILY_PNL_USDT_KEY = "bot:daily_pnl_usdt"
_MODE_KEY = "bot:trading_mode"
_FORCE_TRADE_MODE_KEY = "bot:force_trade_mode"


class TradeLimitsResponse(BaseModel):
    trading_mode: str
    max_concurrent_trades: int
    max_daily_trades: int
    confidence_threshold: float
    stop_loss_percent: float
    take_profit_percent: float
    daily_loss_limit_percent: float
    futures_leverage: int
    negative_trade_timeout_minutes: int
    heat_limit_pct: float
    open_heat_usdt: float
    free_heat_usdt: float


class SetTradeLimitsRequest(BaseModel):
    trading_mode: str


def _mode_limits(mode: str) -> dict:
    return {"max_daily_trades": 16, "max_simultaneous": 1}


async def _read_trade_limits(redis) -> TradeLimitsResponse:
    mode = "FUTURES"
    limits = _mode_limits(mode)
    open_heat = float(await redis.get("bot:open_heat_usdt") or 0.0)
    free_heat = float(await redis.get("bot:free_heat_usdt") or 0.0)
    return TradeLimitsResponse(
        trading_mode=mode,
        max_concurrent_trades=limits["max_simultaneous"],
        max_daily_trades=limits["max_daily_trades"],
        confidence_threshold=settings.confidence_threshold,
        stop_loss_percent=settings.stop_loss_percent,
        take_profit_percent=settings.take_profit_percent,
        daily_loss_limit_percent=settings.daily_loss_limit_percent,
        futures_leverage=settings.futures_leverage,
        negative_trade_timeout_minutes=settings.negative_trade_timeout_minutes,
        heat_limit_pct=_HEAT_LIMIT_PCT,
        open_heat_usdt=round(open_heat, 2),
        free_heat_usdt=round(free_heat, 2),
    )


@app.get("/api/v1/settings/trade-limits", response_model=TradeLimitsResponse)
async def get_trade_limits():
    redis = await get_redis()
    try:
        return await _read_trade_limits(redis)
    finally:
        await redis.aclose()


@app.post("/api/v1/settings/trade-limits", response_model=TradeLimitsResponse)
async def set_trade_limits(request: SetTradeLimitsRequest):
    if request.trading_mode != "FUTURES":
        raise HTTPException(status_code=400, detail="Spot and dynamic routing are disabled. trading_mode must be FUTURES")
    redis = await get_redis()
    try:
        await redis.set(_MODE_KEY, "FUTURES")
        await redis.set(_FORCE_TRADE_MODE_KEY, "FUTURES")
        await redis.delete("bot:disable_futures_buy")
        return await _read_trade_limits(redis)
    finally:
        await redis.aclose()


@app.get("/api/v1/bot/startup-status")
async def get_startup_status():
    """Return readiness checks for the startup confirmation modal."""
    redis = await get_redis()
    try:
        status_raw = await redis.get("bot:status")
        state = "unknown"
        if status_raw:
            try:
                state = json.loads(status_raw).get("state", "unknown")
            except Exception:
                pass

        stream_count_raw = await redis.get("bot:price_stream_active")
        price_feed_count = int(stream_count_raw) if stream_count_raw else 0
        prices_active = price_feed_count > 0

        paper_raw = await redis.get(_LIVE_MODE_KEYS["paper_trading_mode"])
        is_paper = (paper_raw == "true") if paper_raw is not None else settings.paper_trading_mode
        live_raw = await redis.get(_LIVE_MODE_KEYS["live_trading_enabled"])
        live_enabled = (live_raw == "true") if live_raw is not None else settings.live_trading_enabled
        testnet_raw = await redis.get(_LIVE_MODE_KEYS["use_testnet"])
        use_testnet = (testnet_raw == "true") if testnet_raw is not None else settings.use_testnet

        limits = await _read_trade_limits(redis)

        if is_paper:
            capital = float(await redis.get("paper:capital_usdt") or settings.starting_capital_usdt)
        else:
            try:
                exchange = _get_futures_exchange(use_testnet)
                try:
                    bal = await exchange.fetch_balance()
                    capital = float(bal.get("USDT", {}).get("free", 0.0))
                finally:
                    await exchange.close()
            except Exception:
                capital = float(await redis.get("paper:capital_usdt") or settings.starting_capital_usdt)

        return {
            "state": state,
            "awaiting_confirmation": state == "awaiting_confirmation",
            "data_feed_active": prices_active,
            "price_feeds_count": price_feed_count,
            "paper_mode": is_paper,
            "live_enabled": live_enabled,
            "use_testnet": use_testnet,
            "capital_usdt": round(capital, 2),
            "settings": {
                "trading_mode": limits.trading_mode,
                "heat_limit_pct": limits.heat_limit_pct,
                "max_concurrent_trades": limits.max_concurrent_trades,
                "max_daily_trades": limits.max_daily_trades,
                "futures_leverage": limits.futures_leverage,
            },
        }
    finally:
        await redis.aclose()


@app.post("/api/v1/bot/confirm-startup")
async def confirm_startup():
    """User confirms settings and starts trading."""
    redis = await get_redis()
    try:
        status_raw = await redis.get("bot:status")
        state = "unknown"
        if status_raw:
            try:
                state = json.loads(status_raw).get("state", "unknown")
            except Exception:
                pass

        if state != "awaiting_confirmation":
            raise HTTPException(
                status_code=400,
                detail=f"Bot is not awaiting confirmation (current state: {state})",
            )

        await redis.set("bot:command", "confirmed")
        return {"ok": True, "message": "Trading confirmed — bot starting"}
    finally:
        await redis.aclose()


@app.post("/api/v1/bot/command/{command}")
async def send_bot_command(command: str):
    if command not in ("stop", "pause", "resume"):
        raise HTTPException(status_code=400, detail="Command must be: stop | pause | resume")
    redis = await get_redis()
    try:
        if command == "resume":
            await redis.delete("bot:command")
        else:
            await redis.set("bot:command", command)
        return {"ok": True, "command": command}
    finally:
        await redis.aclose()


@app.post("/api/v1/bot/reset-killswitch")
async def reset_killswitch():
    """Exit defensive mode and resume normal strategy."""
    redis = await get_redis()
    try:
        await redis.set("bot:kill_switch", "0")
        await redis.set("bot:defensive_mode", "0")
        await redis.delete("bot:command", "bot:loss_consec", "bot:loss_pause_until")
        return {"ok": True, "message": "Kill switch and loss pause cleared — trading resuming"}
    finally:
        await redis.aclose()


@app.post("/api/v1/bot/reset-loss-response")
async def reset_loss_response():
    """Clear the loss-streak pause and size reduction without touching the kill switch."""
    redis = await get_redis()
    try:
        await redis.delete("bot:loss_consec", "bot:loss_pause_until")
        return {"ok": True, "message": "Loss pause cleared — entries and full size resume"}
    finally:
        await redis.aclose()

@app.post("/api/v1/bot/emergency-stop")
async def emergency_stop():
    """Hard stop all trading immediately."""
    redis = await get_redis()
    try:
        await redis.set("bot:command", "stop")
        await redis.set("bot:kill_switch", "1")
        await redis.set("bot:defensive_mode", "1")
        return {"ok": True, "message": "Emergency stop activated — all trading halted"}
    finally:
        await redis.aclose()

_LIVE_MODE_KEYS = {
    "paper_trading_mode": "bot:paper_trading_mode",
    "live_trading_enabled": "bot:live_trading_enabled",
    "use_testnet": "bot:use_testnet",
}


def _get_exchange(use_testnet: bool | None = None):
    testnet = use_testnet if use_testnet is not None else settings.use_testnet
    api_key = settings.binance_testnet_api_key if testnet else settings.binance_api_key
    api_secret = settings.binance_testnet_api_secret if testnet else settings.binance_api_secret
    ex = ccxt.binance({
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "spot"},
    })
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


def _get_futures_exchange(use_testnet: bool | None = None):
    testnet = use_testnet if use_testnet is not None else settings.use_testnet
    api_key = settings.binance_testnet_api_key if testnet else settings.binance_api_key
    api_secret = settings.binance_testnet_api_secret if testnet else settings.binance_api_secret
    ex = ccxt.binance({
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "future"},
    })
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


class AssetBalance(BaseModel):
    asset: str
    free: float
    used: float
    total: float
    usdt_value: float


class AccountBalancesResponse(BaseModel):
    total_usdt: float
    free_usdt: float
    assets: list[AssetBalance]
    is_testnet: bool


class LiveModeResponse(BaseModel):
    paper_trading_mode: bool
    live_trading_enabled: bool
    use_testnet: bool


@app.get("/api/v1/account/balances", response_model=AccountBalancesResponse)
async def get_account_balances():
    redis = await get_redis()
    try:
        use_testnet_raw = await redis.get(_LIVE_MODE_KEYS["use_testnet"])
        use_testnet = (use_testnet_raw == "true") if use_testnet_raw is not None else settings.use_testnet
    finally:
        await redis.aclose()

    exchange = _get_futures_exchange(use_testnet)
    try:
        balance = await exchange.fetch_balance()
        assets: list[AssetBalance] = []
        total_usdt = 0.0
        free_usdt = 0.0

        tickers = {}
        try:
            tickers = await exchange.fetch_tickers()
        except Exception:
            pass

        for asset_name, bal in balance.get("total", {}).items():
            if bal is None or float(bal) <= 0:
                continue
            total_val = float(bal)
            free_val = float(balance.get("free", {}).get(asset_name, 0) or 0)
            used_val = float(balance.get("used", {}).get(asset_name, 0) or 0)

            if asset_name == "USDT":
                usdt_value = total_val
            else:
                pair = f"{asset_name}/USDT"
                ticker = tickers.get(pair)
                if ticker and ticker.get("last"):
                    usdt_value = total_val * float(ticker["last"])
                else:
                    try:
                        t = await exchange.fetch_ticker(pair)
                        usdt_value = total_val * float(t["last"]) if t.get("last") else 0.0
                    except Exception:
                        usdt_value = 0.0

            total_usdt += usdt_value
            if asset_name == "USDT":
                free_usdt = free_val

            assets.append(AssetBalance(
                asset=asset_name,
                free=round(free_val, 8),
                used=round(used_val, 8),
                total=round(total_val, 8),
                usdt_value=round(usdt_value, 2),
            ))

        assets.sort(key=lambda a: a.usdt_value, reverse=True)

        return AccountBalancesResponse(
            total_usdt=round(total_usdt, 2),
            free_usdt=round(free_usdt, 2),
            assets=assets,
            is_testnet=use_testnet,
        )
    except Exception as e:
        logger.error(f"Failed to fetch balances: {e}")
        raise HTTPException(status_code=502, detail=f"Binance API error: {str(e)}")
    finally:
        await exchange.close()


@app.post("/api/v1/account/convert-all")
async def convert_all_to_usdt():
    redis = await get_redis()
    try:
        use_testnet_raw = await redis.get(_LIVE_MODE_KEYS["use_testnet"])
        use_testnet = (use_testnet_raw == "true") if use_testnet_raw is not None else settings.use_testnet
    finally:
        await redis.aclose()

    exchange = _get_futures_exchange(use_testnet)
    results = []
    try:
        balance = await exchange.fetch_balance()
        for asset_name, bal in balance.get("free", {}).items():
            if bal is None or float(bal) <= 0 or asset_name == "USDT":
                continue
            free_val = float(bal)
            pair = f"{asset_name}/USDT"
            try:
                markets = await exchange.load_markets()
                if pair not in markets:
                    results.append({"asset": asset_name, "ok": False, "error": "No USDT market"})
                    continue
                market = markets[pair]
                min_amount = float(market.get("limits", {}).get("amount", {}).get("min", 0) or 0)
                if free_val < min_amount:
                    results.append({"asset": asset_name, "ok": False, "error": f"Below min amount ({min_amount})"})
                    continue
                ticker = await exchange.fetch_ticker(pair)
                price = float(ticker["last"])
                notional = free_val * price
                min_cost = float(market.get("limits", {}).get("cost", {}).get("min", 0) or 0)
                if notional < max(min_cost, 5.0):
                    results.append({"asset": asset_name, "ok": False, "error": f"Notional too small (${notional:.2f})"})
                    continue
                order = await exchange.create_market_sell_order(pair, free_val)
                filled_usdt = float(order.get("cost") or 0)
                results.append({
                    "asset": asset_name,
                    "ok": True,
                    "quantity": free_val,
                    "usdt_received": round(filled_usdt, 2),
                    "order_id": str(order.get("id", "")),
                })
            except Exception as e:
                results.append({"asset": asset_name, "ok": False, "error": str(e)})

        return {"ok": True, "conversions": results}
    except Exception as e:
        logger.error(f"Convert all failed: {e}")
        raise HTTPException(status_code=502, detail=f"Binance API error: {str(e)}")
    finally:
        await exchange.close()


@app.get("/api/v1/settings/live-mode", response_model=LiveModeResponse)
async def get_live_mode():
    redis = await get_redis()
    try:
        paper_raw = await redis.get(_LIVE_MODE_KEYS["paper_trading_mode"])
        live_raw = await redis.get(_LIVE_MODE_KEYS["live_trading_enabled"])
        testnet_raw = await redis.get(_LIVE_MODE_KEYS["use_testnet"])

        paper = (paper_raw == "true") if paper_raw is not None else settings.paper_trading_mode
        live = (live_raw == "true") if live_raw is not None else settings.live_trading_enabled
        testnet = (testnet_raw == "true") if testnet_raw is not None else settings.use_testnet

        return LiveModeResponse(
            paper_trading_mode=paper,
            live_trading_enabled=live,
            use_testnet=testnet,
        )
    finally:
        await redis.aclose()


@app.post("/api/v1/settings/live-mode", response_model=LiveModeResponse)
async def set_live_mode(body: dict = Body(...)):
    redis = await get_redis()
    try:
        if "paper_trading_mode" in body:
            await redis.set(_LIVE_MODE_KEYS["paper_trading_mode"], str(body["paper_trading_mode"]).lower())
        if "live_trading_enabled" in body:
            await redis.set(_LIVE_MODE_KEYS["live_trading_enabled"], str(body["live_trading_enabled"]).lower())
        if "use_testnet" in body:
            await redis.set(_LIVE_MODE_KEYS["use_testnet"], str(body["use_testnet"]).lower())

        await redis.set("bot:command", "restart")

        paper_raw = await redis.get(_LIVE_MODE_KEYS["paper_trading_mode"])
        live_raw = await redis.get(_LIVE_MODE_KEYS["live_trading_enabled"])
        testnet_raw = await redis.get(_LIVE_MODE_KEYS["use_testnet"])

        return LiveModeResponse(
            paper_trading_mode=(paper_raw == "true") if paper_raw is not None else settings.paper_trading_mode,
            live_trading_enabled=(live_raw == "true") if live_raw is not None else settings.live_trading_enabled,
            use_testnet=(testnet_raw == "true") if testnet_raw is not None else settings.use_testnet,
        )
    finally:
        await redis.aclose()


@app.get("/api/v1/settings/currency")
async def get_currency_pref():
    redis = await get_redis()
    try:
        currency = (await redis.get("user:currency") or "USD")
        zar_rate = float(await redis.get("user:zar_rate") or 18.5)
        return {"currency": currency, "zar_rate": round(zar_rate, 4)}
    finally:
        await redis.aclose()

@app.post("/api/v1/settings/currency")
async def set_currency_pref(body: dict):
    currency = body.get("currency", "USD")
    zar_rate = float(body.get("zar_rate", 18.5))
    if currency not in ("USD", "ZAR"):
        raise HTTPException(status_code=400, detail="currency must be USD or ZAR")
    redis = await get_redis()
    try:
        await redis.set("user:currency", currency)
        await redis.set("user:zar_rate", str(zar_rate))
        return {"ok": True, "currency": currency, "zar_rate": zar_rate}
    finally:
        await redis.aclose()

@app.post("/api/v1/admin/hard-reset")
async def hard_reset(session: Annotated[AsyncSession, Depends(get_session)]):
    """Archive all trades and reset paper capital/daily stats. ML data is preserved for learning."""
    redis = await get_redis()
    try:
        await redis.set("bot:command", "stop")
        raw_start = await redis.get("paper:starting_capital_usdt")
        restore_capital = float(raw_start) if raw_start else _starting_capital

        # Archive all current trades (close open ones first) — not deleted, ML still learns from them
        await session.execute(text("""
            UPDATE trades
            SET archived = true,
                status = CASE WHEN status = 'OPEN' THEN 'CLOSED' ELSE status END,
                closed_at = CASE WHEN status = 'OPEN' AND closed_at IS NULL THEN NOW() ELSE closed_at END
            WHERE archived = false OR archived IS NULL
        """))
        await session.execute(text("DELETE FROM positions"))
        await session.commit()

        reset_keys = {
            "bot:daily_pnl": "0.0",
            "bot:daily_pnl_usdt": "0.0",
            "bot:daily_trade_count": "0",
            "bot:kill_switch": "0",
            "bot:defensive_mode": "0",
            "bot:open_count": "0",
            "paper:capital_usdt": str(restore_capital),
        }
        await redis.mset(reset_keys)
        await redis.delete("bot:recovery_mode", "bot:loss_consec", "bot:loss_pause_until")
        await redis.delete("bot:session_start_capital")

        # Clear per-symbol cooldowns, TP trail anchors, and regime cache
        for pattern in ("bot:cooldown:*", "bot:tp_trail:*", "bot:regime:*", "bot:correlation_matrix"):
            keys = await redis.keys(pattern)
            for k in keys:
                await redis.delete(k)

        return {"ok": True, "message": f"Reset complete — {restore_capital:,.2f} USDT restored. Trade history archived for ML learning."}
    finally:
        await redis.aclose()


@app.delete("/api/v1/positions/{position_id}")
async def close_position_manually(
    position_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
):
    """Mark a position for immediate manual close on next bot loop cycle."""
    result = await session.execute(
        text("SELECT id, symbol FROM positions WHERE id = :id"),
        {"id": position_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Position {position_id} not found")

    redis = await get_redis()
    try:
        await redis.set(f"close_position:{position_id}", "1", ex=300)
        return {"ok": True, "message": f"Position {position_id} ({row['symbol']}) queued for close"}
    finally:
        await redis.aclose()


class SymbolOverviewResponse(BaseModel):
    symbol: str
    price: PriceResponse | None
    prediction: PredictionResponse | None
    suggested_side: str | None
    positions: list[PositionResponse]
    trades: list[TradeResponse]
    paper: bool


@app.get("/api/v1/symbol/overview", response_model=SymbolOverviewResponse)
async def get_symbol_overview(
    session: Annotated[AsyncSession, Depends(get_session)],
    symbol: str = Query(..., description="Trading pair, e.g. BTC/USDT"),
):
    """Everything the live-price modal needs for one symbol."""
    redis = await get_redis()
    try:
        raw_price = await redis.get(f"price:{symbol}")
        paper_raw = await redis.get("bot:paper_trading_mode")
    finally:
        await redis.aclose()
    price = None
    if raw_price:
        try:
            price = PriceResponse(**json.loads(raw_price))
        except Exception:
            price = None

    pred_row = (
        await session.execute(
            text("""
                SELECT symbol,
                       prediction_date AT TIME ZONE 'UTC' AS prediction_date,
                       current_price, target_price,
                       predicted_change_pct, confidence,
                       mode_recommendation, features
                FROM predictions
                WHERE symbol = :symbol
                ORDER BY prediction_date DESC
                LIMIT 1
            """),
            {"symbol": symbol},
        )
    ).mappings().first()
    prediction = None
    suggested_side = None
    if pred_row:
        prediction = PredictionResponse(
            symbol=pred_row["symbol"],
            prediction_date=pred_row["prediction_date"].isoformat() if pred_row["prediction_date"] else "",
            current_price=pred_row["current_price"],
            target_price=pred_row["target_price"],
            predicted_change_pct=pred_row["predicted_change_pct"],
            confidence=pred_row["confidence"],
            mode_recommendation=pred_row["mode_recommendation"],
            features=pred_row["features"],
        )
        suggested_side = "BUY" if pred_row["predicted_change_pct"] > 0 else "SELL"

    pos_rows = (
        await session.execute(
            text("""
                SELECT id, symbol, side, mode, entry_price, current_price,
                       quantity, leverage, stop_loss_price, take_profit_price,
                       unrealized_pnl, paper_trade,
                       opened_at AT TIME ZONE 'UTC' AS opened_at
                FROM positions
                WHERE symbol = :symbol
                ORDER BY opened_at DESC
            """),
            {"symbol": symbol},
        )
    ).mappings().all()
    trade_rows = (
        await session.execute(
            text("""
                SELECT id, symbol, side, mode, entry_price, exit_price,
                       quantity, leverage, pnl_usdt, pnl_percent, status, paper_trade,
                       opened_at AT TIME ZONE 'UTC' AS opened_at,
                       closed_at AT TIME ZONE 'UTC' AS closed_at
                FROM trades
                WHERE symbol = :symbol AND (archived = false OR archived IS NULL)
                ORDER BY opened_at DESC
                LIMIT 20
            """),
            {"symbol": symbol},
        )
    ).mappings().all()

    def _iso(r):
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(r).items()}

    return SymbolOverviewResponse(
        symbol=symbol,
        price=price,
        prediction=prediction,
        suggested_side=suggested_side,
        positions=[PositionResponse(**_iso(r)) for r in pos_rows],
        trades=[TradeResponse(**_iso(r)) for r in trade_rows],
        paper=(paper_raw == "true") if paper_raw is not None else settings.paper_trading_mode,
    )


class ManualOpenRequest(BaseModel):
    symbol: str
    side: str


@app.post("/api/v1/positions/manual-open")
async def manual_open_position(req: ManualOpenRequest):
    """Queue a manual entry. The bot executor opens it through the normal trade path."""
    side = req.side.upper()
    if side not in ("BUY", "SELL"):
        raise HTTPException(status_code=422, detail="side must be BUY or SELL")

    redis = await get_redis()
    try:
        if not await redis.exists(f"price:{req.symbol}"):
            raise HTTPException(status_code=404, detail=f"No live price for {req.symbol}")
        request_id = uuid.uuid4().hex
        payload = {
            "id": request_id,
            "symbol": req.symbol,
            "side": side,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        await redis.set(
            f"manual_open:result:{request_id}",
            json.dumps({"status": "pending"}),
            ex=300,
        )
        await redis.rpush("manual_open:queue", json.dumps(payload))
        return {"ok": True, "request_id": request_id, "message": f"{side} {req.symbol} queued"}
    finally:
        await redis.aclose()


@app.get("/api/v1/positions/manual-open/{request_id}")
async def manual_open_result(request_id: str):
    redis = await get_redis()
    try:
        raw = await redis.get(f"manual_open:result:{request_id}")
        if not raw:
            raise HTTPException(status_code=404, detail="Request not found or expired")
        return json.loads(raw)
    finally:
        await redis.aclose()


@app.get("/api/v1/stats/pnl", response_model=PnlPeriodResponse)
async def get_pnl_stats(period: str = Query("all", description="1h | 24h | 7d | 30d | all")):
    from datetime import timedelta
    period_map = {
        "1h": timedelta(hours=1),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    since = None
    if period in period_map:
        since = datetime.now(timezone.utc) - period_map[period]

    profit, loss, wins, losses = 0.0, 0.0, 0, 0
    try:
        async with _db_session() as session:
            if since is not None:
                sql = text("""
                    SELECT
                        COALESCE(SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END), 0) AS total_profit_usdt,
                        COALESCE(SUM(CASE WHEN pnl_usdt < 0 THEN pnl_usdt ELSE 0 END), 0) AS total_loss_usdt,
                        COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) AS win_count,
                        COUNT(CASE WHEN pnl_usdt < 0 THEN 1 END) AS loss_count
                    FROM trades
                    WHERE status = 'CLOSED' AND (archived = false OR archived IS NULL) AND closed_at >= :since
                """)
                result = await session.execute(sql, {"since": since})
            else:
                sql = text("""
                    SELECT
                        COALESCE(SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END), 0) AS total_profit_usdt,
                        COALESCE(SUM(CASE WHEN pnl_usdt < 0 THEN pnl_usdt ELSE 0 END), 0) AS total_loss_usdt,
                        COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) AS win_count,
                        COUNT(CASE WHEN pnl_usdt < 0 THEN 1 END) AS loss_count
                    FROM trades
                    WHERE status = 'CLOSED' AND (archived = false OR archived IS NULL)
                """)
                result = await session.execute(sql)
            row = result.mappings().first()
            if row:
                profit = round(float(row["total_profit_usdt"]), 4)
                loss = round(float(row["total_loss_usdt"]), 4)
                wins = int(row["win_count"])
                losses = int(row["loss_count"])
    except Exception as e:
        logger.error(f"pnl stats error: {e}")

    return PnlPeriodResponse(
        period=period,
        total_profit_usdt=profit,
        total_loss_usdt=loss,
        win_count=wins,
        loss_count=losses,
        net_usdt=round(profit + loss, 4),
    )

@app.get("/api/v1/stats/mode-breakdown", response_model=ModeBreakdownResponse)
async def get_mode_breakdown():
    def empty() -> ModeStatsItem:
        return ModeStatsItem(total_profit_usdt=0.0, total_loss_usdt=0.0, win_count=0, loss_count=0, net_usdt=0.0)

    spot, futures = empty(), empty()
    try:
        async with _db_session() as session:
            sql = text("""
                SELECT
                    mode,
                    COALESCE(SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END), 0) AS total_profit_usdt,
                    COALESCE(SUM(CASE WHEN pnl_usdt < 0 THEN pnl_usdt ELSE 0 END), 0) AS total_loss_usdt,
                    COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) AS win_count,
                    COUNT(CASE WHEN pnl_usdt < 0 THEN 1 END) AS loss_count
                FROM trades
                WHERE status = 'CLOSED' AND (archived = false OR archived IS NULL) AND mode IS NOT NULL
                GROUP BY mode
            """)
            result = await session.execute(sql)
            for row in result.mappings():
                mode_val = (row["mode"] or "").upper()
                item = ModeStatsItem(
                    total_profit_usdt=round(float(row["total_profit_usdt"]), 4),
                    total_loss_usdt=round(float(row["total_loss_usdt"]), 4),
                    win_count=int(row["win_count"]),
                    loss_count=int(row["loss_count"]),
                    net_usdt=round(float(row["total_profit_usdt"]) + float(row["total_loss_usdt"]), 4),
                )
                if "FUTURES" in mode_val:
                    futures = item
                else:
                    spot = item
    except Exception as e:
        logger.error(f"mode breakdown error: {e}")

    return ModeBreakdownResponse(spot=spot, futures=futures)


@app.get("/api/v1/stats/recovery")
async def get_recovery_stats(hours: int = Query(24, ge=1, le=720)):
    """Post-exit recovery data from the 1m researcher, grouped by close reason."""
    by_reason: list[dict] = []
    recent: list[dict] = []
    try:
        async with _db_session() as session:
            rows = (await session.execute(text("""
                SELECT COALESCE(close_reason, 'unknown') AS close_reason,
                       COUNT(*) AS trades,
                       COUNT(*) FILTER (WHERE recovered_to_entry) AS recovered,
                       COUNT(*) FILTER (WHERE hit_tp) AS hit_tp,
                       COUNT(*) FILTER (WHERE hold_pnl_60m IS NOT NULL AND hold_pnl_60m > pnl_usdt) AS held_better_60m,
                       COUNT(*) FILTER (WHERE hold_pnl_60m IS NOT NULL) AS with_60m,
                       AVG(best_fav_pct) AS avg_best_fav_pct,
                       AVG(worst_adv_pct) AS avg_worst_adv_pct,
                       SUM(pnl_usdt) AS actual_pnl,
                       SUM(hold_pnl_5m) AS hold_pnl_5m,
                       SUM(hold_pnl_15m) AS hold_pnl_15m,
                       SUM(hold_pnl_60m) AS hold_pnl_60m,
                       SUM(pnl_usdt) FILTER (WHERE hold_pnl_60m IS NOT NULL) AS actual_pnl_60m
                FROM trade_recovery
                WHERE closed_at >= NOW() - make_interval(hours => :hours)
                GROUP BY 1
                ORDER BY trades DESC
            """), {"hours": hours})).mappings().all()
            for r in rows:
                by_reason.append({
                    k: (round(float(v), 4) if isinstance(v, float) or hasattr(v, "as_tuple") else v)
                    for k, v in r.items()
                })
            recent_rows = (await session.execute(text("""
                SELECT trade_id, symbol, side, close_reason, pnl_usdt, closed_at, hold_seconds,
                       minutes_observed, final, best_fav_pct, worst_adv_pct, recovered_to_entry,
                       minutes_to_entry, hit_tp, hold_pnl_5m, hold_pnl_15m, hold_pnl_60m
                FROM trade_recovery
                WHERE closed_at >= NOW() - make_interval(hours => :hours)
                ORDER BY closed_at DESC
                LIMIT 40
            """), {"hours": hours})).mappings().all()
            for r in recent_rows:
                item = dict(r)
                item["closed_at"] = item["closed_at"].isoformat() if item["closed_at"] else None
                recent.append(item)
    except Exception as e:
        logger.error(f"recovery stats error: {e}")
    return {"hours": hours, "by_reason": by_reason, "recent": recent}



class MarketSentimentResponse(BaseModel):
    sentiment: str
    strength: str
    advance_ratio: float
    advancing: int
    declining: int
    neutral_count: int
    total_symbols: int
    avg_change_pct: float
    top_gainers: list[str]
    top_losers: list[str]
    strategy: str
    estimated_daily_profit_usdt: float
    estimated_daily_profit_pct: float


@app.get("/api/v1/market/sentiment", response_model=MarketSentimentResponse)
async def get_market_sentiment():
    redis = await get_redis()
    try:
        # Prefer the bot's pre-computed sentiment (updated every 10s by price stream)
        # Fall back to computing from individual price keys if bot hasn't populated it yet
        cached = await redis.get("market:sentiment")
        if cached:
            try:
                cached_data = json.loads(cached)
                advance_ratio = float(cached_data.get("advance_ratio", 0.5))
                advancing = int(cached_data.get("advancing", 0))
                declining = int(cached_data.get("declining", 0))
                total_symbols = int(cached_data.get("total", 0))
                neutral_count = total_symbols - advancing - declining
            except Exception:
                cached = None

        if not cached:
            keys = await redis.keys("price:*")
            prices_data = []
            if keys:
                values = await redis.mget(keys)
                for raw in values:
                    if raw:
                        try:
                            prices_data.append(json.loads(raw))
                        except Exception:
                            pass
            advancing = sum(1 for p in prices_data if p.get("change_pct", 0) > 0)
            declining = sum(1 for p in prices_data if p.get("change_pct", 0) < 0)
            total_symbols = len(prices_data)
            neutral_count = total_symbols - advancing - declining
            advance_ratio = advancing / max(total_symbols, 1)

        # Top gainers/losers from price keys
        keys = await redis.keys("price:*")
        prices_data = []
        if keys:
            values = await redis.mget(keys)
            for raw in values:
                if raw:
                    try:
                        prices_data.append(json.loads(raw))
                    except Exception:
                        pass

        sorted_by_change = sorted(prices_data, key=lambda p: p.get("change_pct", 0), reverse=True)
        top_gainers = [p["symbol"] for p in sorted_by_change[:3]]
        top_losers = [p["symbol"] for p in sorted_by_change[-3:]][::-1]
        avg_change = sum(p.get("change_pct", 0) for p in prices_data) / max(len(prices_data), 1)

        if advance_ratio > 0.65:
            sentiment, strength = "BULLISH", "Strong Bullish"
            strategy = "Strong momentum — favor trend-following longs and breakouts. High-confidence futures signals are well-suited to current conditions."
        elif advance_ratio > 0.55:
            sentiment, strength = "BULLISH", "Mild Bullish"
            strategy = "Mild upside bias — lean toward long setups on pullbacks. Keep position sizes moderate and confirm signals before entry."
        elif advance_ratio < 0.35:
            sentiment, strength = "BEARISH", "Strong Bearish"
            strategy = "Risk-off environment — reduce exposure significantly. Focus on defensive shorts on overextended pairs and tighten stop-losses."
        elif advance_ratio < 0.45:
            sentiment, strength = "BEARISH", "Mild Bearish"
            strategy = "Mild downside pressure — avoid aggressive longs. Consider selective shorts on high-volume pairs with clear breakdown signals."
        else:
            sentiment, strength = "NEUTRAL", "Neutral"
            strategy = "Range-bound market — target mean-reversion setups at extremes. Prioritize high-confidence signals and keep position sizes conservative."

        capital_raw = await redis.get("paper:capital_usdt")
        capital = float(capital_raw) if capital_raw else 100.0
        mode_raw = await redis.get("bot:trading_mode")
        mode = "FUTURES"
        mlimits = _mode_limits(mode)
        max_concurrent = mlimits["max_simultaneous"]
        max_daily = mlimits["max_daily_trades"]
        sl_pct = settings.stop_loss_percent / 100
        tp_pct = settings.take_profit_percent / 100

        heat_limit = capital * _HEAT_LIMIT_PCT / 100
        notional_per_trade = heat_limit / max(max_concurrent, 1)
        win_rate = 0.55
        expected_pnl_per_trade = notional_per_trade * (win_rate * tp_pct - (1 - win_rate) * sl_pct)
        expected_trades = max_daily * 0.5
        base_profit = expected_pnl_per_trade * expected_trades
        multiplier = {"BULLISH": 1.25, "NEUTRAL": 1.0, "BEARISH": 0.65}[sentiment]
        estimated_profit = round(base_profit * multiplier, 2)
        estimated_profit_pct = round((estimated_profit / capital * 100) if capital > 0 else 0, 2)

        return MarketSentimentResponse(
            sentiment=sentiment,
            strength=strength,
            advance_ratio=round(advance_ratio, 4),
            advancing=advancing,
            declining=declining,
            neutral_count=neutral_count,
            total_symbols=total_symbols,
            avg_change_pct=round(avg_change, 4),
            top_gainers=top_gainers,
            top_losers=top_losers,
            strategy=strategy,
            estimated_daily_profit_usdt=estimated_profit,
            estimated_daily_profit_pct=estimated_profit_pct,
        )
    finally:
        await redis.aclose()


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """Streams trading events (trade opened/closed, kill switch, daily target) in real-time."""
    await websocket.accept()
    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe("bot:events")

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Events WebSocket error: {e}")
    finally:
        await pubsub.unsubscribe("bot:events")
        await pubsub.aclose()
        await redis.aclose()


@app.websocket("/ws/prices")
async def websocket_prices(websocket: WebSocket):
    await websocket.accept()
    redis = await get_redis()

    try:
        while True:
            keys = await redis.keys("price:*")
            if keys:
                values = await redis.mget(keys)
                prices = [json.loads(v) for v in values if v]
                await websocket.send_json(prices)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await redis.aclose()
