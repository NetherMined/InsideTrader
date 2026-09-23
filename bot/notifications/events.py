"""Publishes trading events to a Redis pub/sub channel.

The API subscribes to this channel and forwards events to the browser
via the /ws/events WebSocket so the dashboard can show toast notifications.
"""

import json
from datetime import datetime, timezone

import redis.asyncio as aioredis

EVENTS_CHANNEL = "bot:events"


async def publish(redis: aioredis.Redis, event_type: str, data: dict) -> None:
    payload = {
        "type": event_type,
        "ts": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    try:
        await redis.publish(EVENTS_CHANNEL, json.dumps(payload))
    except Exception:
        pass


async def publish_trade_opened(
    redis: aioredis.Redis, symbol: str, side: str, mode: str,
    price: float, quantity: float, paper: bool
) -> None:
    await publish(redis, "trade_opened", {
        "symbol": symbol, "side": side, "mode": mode,
        "price": price, "quantity": quantity, "paper": paper,
    })


async def publish_trade_closed(
    redis: aioredis.Redis, symbol: str, mode: str,
    pnl_usdt: float, pnl_pct: float, reason: str, paper: bool
) -> None:
    await publish(redis, "trade_closed", {
        "symbol": symbol, "mode": mode,
        "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct,
        "reason": reason, "paper": paper,
    })


async def publish_kill_switch(redis: aioredis.Redis, daily_loss_pct: float) -> None:
    await publish(redis, "kill_switch", {"daily_loss_pct": daily_loss_pct})


async def publish_daily_target(redis: aioredis.Redis, daily_pnl_pct: float) -> None:
    await publish(redis, "daily_target", {"daily_pnl_pct": daily_pnl_pct})
