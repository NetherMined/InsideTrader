import asyncio
import ccxt.async_support as ccxt
from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from bot.config import settings
from bot.db.connection import async_session
from bot.db.models import ScannedMarket

MIN_VOLUME_USDT = 1_000_000


def _make_public_exchange(market_type: str = "spot") -> ccxt.binance:
    """Unauthenticated exchange client for public market data."""
    return ccxt.binance({"options": {"defaultType": market_type}})


def _make_auth_exchange(market_type: str = "spot") -> ccxt.binance:
    """Authenticated exchange client for order operations."""
    exchange = ccxt.binance({
        "apiKey": settings.order_api_key,
        "secret": settings.order_api_secret,
        "options": {"defaultType": market_type},
    })
    if settings.use_testnet:
        exchange.set_sandbox_mode(True)
    return exchange


async def scan_markets() -> list[dict]:
    """Fetch all active USDT pairs from Binance spot and futures markets."""
    results: list[dict] = []

    for i, market_type in enumerate(["spot", "future"]):
        if i > 0:
            await asyncio.sleep(2)
        exchange = _make_public_exchange(market_type)
        try:
            await exchange.load_markets()
            tickers = await exchange.fetch_tickers()

            for symbol, ticker in tickers.items():
                if market_type == "spot":
                    if not symbol.endswith("/USDT"):
                        continue
                else:
                    # USDM futures symbols: BTC/USDT:USDT — must contain :USDT
                    if ":USDT" not in symbol or "/USDT" not in symbol:
                        continue
                market = exchange.markets.get(symbol)
                if not market or not market.get("active"):
                    continue

                volume = ticker.get("quoteVolume") or 0.0
                if volume < MIN_VOLUME_USDT:
                    continue

                # Normalise futures symbol BTC/USDT:USDT → BTC/USDT for storage
                stored_symbol = symbol.split(":")[0] if market_type == "future" else symbol

                results.append({
                    "symbol": stored_symbol,
                    "base": market["base"],
                    "quote": market["quote"],
                    "market_type": market_type,
                    "volume_24h_usdt": float(volume),
                })

            count = sum(1 for r in results if r["market_type"] == market_type)
            logger.info(f"Scanned {market_type}: {count} qualifying pairs (vol > ${MIN_VOLUME_USDT:,.0f})")

        except Exception as e:
            logger.error(f"Error scanning {market_type} markets: {e}")
        finally:
            await exchange.close()

    return results


async def save_scanned_markets(markets: list[dict]) -> None:
    if not markets:
        return

    async with async_session() as session:
        for market in markets:
            try:
                stmt = (
                    insert(ScannedMarket)
                    .values(
                        symbol=market["symbol"],
                        base=market["base"],
                        quote=market["quote"],
                        market_type=market["market_type"],
                        volume_24h_usdt=market["volume_24h_usdt"],
                        active=True,
                        enabled_for_trading=True,
                    )
                    .on_conflict_do_update(
                        index_elements=["symbol"],
                        set_={
                            "volume_24h_usdt": market["volume_24h_usdt"],
                            "active": True,
                            "last_scanned": func.now(),
                        },
                    )
                )
                await session.execute(stmt)
            except Exception as e:
                # Fallback: simple insert if ON CONFLICT not supported yet
                logger.warning(f"ON CONFLICT failed for {market['symbol']}: {e}")
                try:
                    await session.execute(
                        insert(ScannedMarket).values(
                            symbol=market["symbol"],
                            base=market["base"],
                            quote=market["quote"],
                            market_type=market["market_type"],
                            volume_24h_usdt=market["volume_24h_usdt"],
                            active=True,
                            enabled_for_trading=True,
                        )
                    )
                except Exception:
                    pass  # Duplicate or other error — skip
        await session.commit()

    logger.info(f"Saved {len(markets)} markets to database")


async def get_top_pairs(limit: int = 50, market_type: str | None = None) -> list[str]:
    """Return top pairs by 24h volume for analysis."""
    async with async_session() as session:
        query = (
            select(ScannedMarket)
            .where(ScannedMarket.active.is_(True), ScannedMarket.enabled_for_trading.is_(True))
        )
        if market_type:
            query = query.where(ScannedMarket.market_type == market_type)

        query = query.order_by(ScannedMarket.volume_24h_usdt.desc()).limit(limit)
        result = await session.execute(query)
        markets = result.scalars().all()
        return [m.symbol for m in markets]