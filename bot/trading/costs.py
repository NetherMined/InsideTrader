"""Live Binance futures order-book costs.

book_slippage() walks the book for a market order of a given size; paper fills use it, and
path-mode entries record a round-trip cost check (the idea comes from the Experiments
branch's cost model). FIX_PLAN_OCT14: in "log" mode the check only records whether it would
have skipped the entry, so the forward test isn't changed.
"""

from __future__ import annotations

import ccxt.async_support as ccxt
from loguru import logger

from bot.config import settings

BOOK_DEPTH = 100


def sweep_pct(levels: list, mid: float, notional: float) -> float | None:
    """Average % away from mid for a market fill of `notional` USDT; None if the book is too thin."""
    left, cost = notional, 0.0
    for price, qty in levels:
        price, qty = float(price), float(qty)
        take = min(left, price * qty)
        cost += take * abs(price / mid - 1)
        left -= take
        if left <= 0:
            return cost / notional * 100
    return None


async def book_slippage(symbol: str, notional: float) -> dict | None:
    """{"buy_pct", "sell_pct", "thin"} for a market order of `notional` USDT, or None if unreadable.

    thin = the top BOOK_DEPTH levels on a side can't absorb the size (that side's pct is None).
    """
    ex = ccxt.binance({"options": {"defaultType": "future"}})
    try:
        book = await ex.fetch_order_book(symbol if ":" in symbol else f"{symbol}:USDT", BOOK_DEPTH)
    except Exception as e:
        logger.debug(f"{symbol}: order book unavailable ({e})")
        return None
    finally:
        await ex.close()
    bids, asks = book.get("bids") or [], book.get("asks") or []
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2
    buy, sell = sweep_pct(asks, mid, notional), sweep_pct(bids, mid, notional)
    return {"buy_pct": buy, "sell_pct": sell, "thin": buy is None or sell is None}


async def entry_cost_check(symbol: str, notional: float) -> dict | None:
    """Round-trip slippage (entry + exit, both sides of the book) against entry_max_slippage_pct."""
    s = await book_slippage(symbol, notional)
    if s is None:
        return None
    limit = settings.entry_max_slippage_pct
    rt = None if s["thin"] else round(s["buy_pct"] + s["sell_pct"], 4)
    return {
        "roundtrip_slippage_pct": rt,
        "roundtrip_cost_pct": None if rt is None else round(rt + 2 * settings.taker_fee_rate * 100, 4),
        "thin": s["thin"],
        "max_slippage_pct": limit,
        "would_skip": s["thin"] or rt > limit,
    }
