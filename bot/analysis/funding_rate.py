"""Funding rate monitor.

Fetches current funding rates from Binance for all scanned symbols.
Used to:
  1. Log funding rates for monitoring
  2. Prefer the funding-collecting side (short when funding > 0, long when < 0)
  3. Gate FUTURES mode — avoid paying funding on low-confidence signals

Binance funding rate is paid every 8 hours. Positive = longs pay shorts.
Negative = shorts pay longs.
"""

import ccxt.async_support as ccxt
from loguru import logger

from bot.config import settings


def _make_exchange() -> ccxt.binance:
    exchange = ccxt.binance({
        "apiKey": settings.order_api_key,
        "secret": settings.order_api_secret,
        "options": {"defaultType": "future"},
    })
    if settings.use_testnet:
        exchange.set_sandbox_mode(True)
    return exchange


async def fetch_funding_rates(symbols: list[str]) -> dict[str, dict]:
    """Fetch current funding rates for a list of symbols.

    Returns:
        dict mapping symbol -> {
            "funding_rate": float (e.g., 0.0001 = 0.01%),
            "next_funding_time": float (ms timestamp),
            "predicted_funding_rate": float or None,
            "side_to_collect": "LONG" or "SHORT" or None,
        }
    """
    exchange = _make_exchange()
    results = {}
    try:
        await exchange.load_markets()
        # Fetch funding rates in batches of 20 (Binance limit)
        for i in range(0, len(symbols), 20):
            batch = symbols[i:i + 20]
            try:
                funding_data = await exchange.fetch_funding_rates(batch)
                for symbol, data in funding_data.items():
                    rate = data.get("fundingRate", 0.0)
                    next_funding = data.get("nextFundingRate", 0.0)
                    predicted = data.get("predictedFundingRate", None)

                    # Determine which side collects funding
                    if rate > 0.0001:
                        side = "SHORT"  # longs pay shorts → short collects
                    elif rate < -0.0001:
                        side = "LONG"  # shorts pay longs → long collects
                    else:
                        side = None

                    results[symbol] = {
                        "funding_rate": rate,
                        "next_funding_rate": next_funding,
                        "predicted_funding_rate": predicted,
                        "side_to_collect": side,
                    }
            except Exception as e:
                logger.warning(f"Error fetching funding rates for batch {i}: {e}")
                for symbol in batch:
                    results[symbol] = {"funding_rate": 0.0, "side_to_collect": None}
    except Exception as e:
        logger.error(f"Error fetching funding rates: {e}")
    finally:
        await exchange.close()

    return results


FUNDING_SIGNAL_THRESHOLD = 0.0003   # 0.03% per 8h — meaningful funding signal
FUNDING_EXTREME_THRESHOLD = 0.0005  # 0.05% per 8h — arb opportunity, override ML direction


def get_funding_signal(funding_rate: float) -> str:
    """Return trading signal based on funding rate.

    Positive rate → SHORT collects funding
    Negative rate → LONG collects funding
    Near zero → no funding signal
    """
    if funding_rate > FUNDING_SIGNAL_THRESHOLD:
        return "SHORT"
    elif funding_rate < -FUNDING_SIGNAL_THRESHOLD:
        return "LONG"
    return "NONE"


def is_extreme_funding(funding_rate: float) -> bool:
    """Return True if funding rate is extreme enough to justify an arb trade.

    At this level (>=0.05% per 8h = ~54% annualised), the funding payment
    is large enough to enter the collecting side regardless of ML direction.
    """
    return abs(funding_rate) >= FUNDING_EXTREME_THRESHOLD


def should_avoid_futures(funding_rate: float, confidence: float, side: str = "BUY", threshold: float = 0.0005) -> bool:
    """Return True if FUTURES mode should be avoided due to unfavorable funding.

    Only avoids when the bot's side is the PAYING side:
    - Positive funding: longs pay shorts → avoid LONG (BUY)
    - Negative funding: shorts pay longs → avoid SHORT (SELL)
    """
    if confidence >= 0.80:
        return False
    if funding_rate > threshold and side == "BUY":
        return True
    if funding_rate < -threshold and side == "SELL":
        return True
    return False


def estimate_funding_cost(funding_rate: float, notional_usdt: float, hours_held: float = 8) -> float:
    """Estimate funding cost/payment for a position.

    Positive rate: cost to long, payment to short.
    Negative rate: cost to short, payment to long.
    """
    return funding_rate * notional_usdt * (hours_held / 8.0)
