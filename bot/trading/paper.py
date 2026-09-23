"""Paper trading simulator.

Executes all the same logic as live trading but simulates fills
using current Redis prices. No real orders are placed.
Records everything to DB identically to live trading.
"""

import json
from datetime import datetime, timezone
from loguru import logger
import redis.asyncio as aioredis

from bot.config import settings

PAPER_CAPITAL_KEY = "paper:capital_usdt"


async def get_paper_capital(redis: aioredis.Redis) -> float:
    val = await redis.get(PAPER_CAPITAL_KEY)
    if val is None:
        start_raw = await redis.get("paper:starting_capital_usdt")
        default = float(start_raw) if start_raw else settings.starting_capital_usdt
        await redis.set(PAPER_CAPITAL_KEY, str(default))
        return default
    return float(val)


async def update_paper_capital(redis: aioredis.Redis, delta_usdt: float) -> float:
    current = await get_paper_capital(redis)
    new_val = current + delta_usdt
    if new_val < 0:
        logger.warning(f"Paper capital would go negative (${new_val:.2f}), clamping to 0")
        new_val = 0.0
    await redis.set(PAPER_CAPITAL_KEY, str(new_val))
    return new_val


async def simulate_buy(
    redis: aioredis.Redis,
    symbol: str,
    quantity: float,
    price: float,
    mode: str,
    leverage: int = 1,
) -> dict:
    notional = quantity * price
    capital = await get_paper_capital(redis)
    margin = notional / leverage if mode == "FUTURES" else notional

    if margin > capital:
        return {"ok": False, "error": f"Insufficient paper capital (${capital:.2f} < ${margin:.2f})"}

    await update_paper_capital(redis, -margin)

    result = {
        "ok": True,
        "order_id": f"PAPER-{int(datetime.now(timezone.utc).timestamp())}",
        "fill_price": price,
        "notional": notional,
        "margin": margin,
        "paper": True,
    }

    logger.info(
        f"[PAPER] {'FUTURES LONG' if mode == 'FUTURES' else 'SPOT BUY'} "
        f"{symbol}: qty={quantity:.6f} @ ${price:.4f} = ${notional:.2f} | "
        f"margin locked: ${margin:.2f} | capital remaining: ${capital - margin:.2f}"
    )
    return result


async def simulate_sell(
    redis: aioredis.Redis,
    symbol: str,
    quantity: float,
    entry_price: float,
    exit_price: float,
    mode: str,
    leverage: int = 1,
) -> dict:
    raw_pnl_pct = (exit_price / entry_price - 1) * 100
    effective_pnl_pct = raw_pnl_pct * leverage
    notional = quantity * entry_price
    pnl_usdt = notional * effective_pnl_pct / 100
    margin = notional / leverage if mode == "FUTURES" else notional

    await update_paper_capital(redis, margin + pnl_usdt)

    result = {
        "ok": True,
        "order_id": f"PAPER-CLOSE-{int(datetime.now(timezone.utc).timestamp())}",
        "fill_price": exit_price,
        "pnl_usdt": pnl_usdt,
        "pnl_pct": effective_pnl_pct,
        "paper": True,
    }

    emoji = "✅" if pnl_usdt >= 0 else "❌"
    logger.info(
        f"[PAPER] CLOSE {symbol}: {emoji} P&L ${pnl_usdt:+.4f} ({effective_pnl_pct:+.2f}%) "
        f"entry=${entry_price:.4f} exit=${exit_price:.4f} margin={margin:.2f}"
    )
    return result


async def simulate_sell_short(
    redis: aioredis.Redis,
    symbol: str,
    quantity: float,
    price: float,
    mode: str,
    leverage: int = 1,
) -> dict:
    notional = quantity * price
    margin = notional / leverage if mode == "FUTURES" else notional
    capital = await get_paper_capital(redis)

    if margin > capital:
        return {"ok": False, "error": f"Insufficient paper capital (${capital:.2f} < ${margin:.2f})"}

    await update_paper_capital(redis, -margin)

    result = {
        "ok": True,
        "order_id": f"PAPER-SHORT-{int(datetime.now(timezone.utc).timestamp())}",
        "fill_price": price,
        "notional": notional,
        "margin": margin,
        "paper": True,
    }

    logger.info(
        f"[PAPER] {'FUTURES SHORT' if mode == 'FUTURES' else 'SPOT SELL'} "
        f"{symbol}: qty={quantity:.6f} @ ${price:.4f} = ${notional:.2f} | "
        f"margin locked: ${margin:.2f} | capital remaining: ${capital - margin:.2f}"
    )
    return result


async def simulate_buy_back(
    redis: aioredis.Redis,
    symbol: str,
    quantity: float,
    entry_price: float,
    exit_price: float,
    mode: str,
    leverage: int = 1,
) -> dict:
    raw_pnl_pct = (entry_price / exit_price - 1) * 100
    effective_pnl_pct = raw_pnl_pct * leverage
    notional = quantity * entry_price
    pnl_usdt = notional * effective_pnl_pct / 100
    margin = notional / leverage if mode == "FUTURES" else notional

    await update_paper_capital(redis, margin + pnl_usdt)

    result = {
        "ok": True,
        "order_id": f"PAPER-BUYBACK-{int(datetime.now(timezone.utc).timestamp())}",
        "fill_price": exit_price,
        "pnl_usdt": pnl_usdt,
        "pnl_pct": effective_pnl_pct,
        "paper": True,
    }

    emoji = "✅" if pnl_usdt >= 0 else "❌"
    logger.info(
        f"[PAPER] CLOSE SHORT {symbol}: {emoji} P&L ${pnl_usdt:+.4f} ({effective_pnl_pct:+.2f}%) "
        f"entry=${entry_price:.4f} exit=${exit_price:.4f}"
    )
    return result
