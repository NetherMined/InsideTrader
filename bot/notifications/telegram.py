"""Telegram notification sender.

Sends messages to the configured Telegram chat when key trading events occur.
No-ops silently if TELEGRAM_ENABLED=false or token/chat_id are not set.
"""

import aiohttp
from loguru import logger

from bot.config import settings

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def _fmt(redis, usd_value: float) -> str:
    if redis is None:
        return f"${usd_value:,.2f}"
    try:
        currency = (await redis.get("user:currency") or "USD")
        if currency == "ZAR":
            rate = float((await redis.get("user:zar_rate")) or 18.5)
            return f"R{usd_value * rate:,.2f}"
    except Exception:
        pass
    return f"${usd_value:,.2f}"


async def _get_capital(redis) -> float:
    if redis is None:
        return 0.0
    try:
        val = await redis.get("paper:capital_usdt")
        return float(val) if val else settings.starting_capital_usdt
    except Exception:
        return 0.0


async def send(message: str) -> None:
    if not settings.telegram_enabled:
        return
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    url = TELEGRAM_API.format(token=settings.telegram_bot_token)
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"Telegram send failed ({resp.status}): {body}")
    except Exception as e:
        logger.warning(f"Telegram notification error: {e}")


async def notify_trade_opened(symbol: str, side: str, mode: str, price: float, quantity: float, paper: bool) -> None:
    tag = "PAPER" if paper else "LIVE"
    mode_icon = "SPOT" if mode == "SPOT" else "FUTURES"
    await send(
        f"[{tag}] {mode_icon} Trade Opened\n"
        f"Symbol: {symbol}\n"
        f"Side: {side} | Mode: {mode}\n"
        f"Price: ${price:,.4f} | Qty: {quantity:.6f}"
    )


NOTIFY_COOLDOWN_KEY = "telegram:hourly_stats_cooldown"
NOTIFY_COOLDOWN_SECS = 3600


async def notify_hourly_stats(redis) -> None:
    if redis is None:
        return
    try:
        if await redis.get(NOTIFY_COOLDOWN_KEY):
            return
        await redis.set(NOTIFY_COOLDOWN_KEY, "1", ex=NOTIFY_COOLDOWN_SECS)

        capital = await _get_capital(redis)
        capital_fmt = await _fmt(redis, capital)

        daily_pnl_usdt = float((await redis.get("bot:daily_pnl_usdt")) or 0.0)
        daily_pnl_pct = float((await redis.get("bot:daily_pnl")) or 0.0)
        trade_count = int((await redis.get("bot:daily_trade_count")) or 0)
        open_count = int((await redis.get("bot:open_count")) or 0)

        pnl_fmt = await _fmt(redis, daily_pnl_usdt)
        sign = "+" if daily_pnl_usdt >= 0 else ""

        tag_raw = await redis.get("paper:capital_usdt")
        tag = "PAPER" if tag_raw is not None else "LIVE"

        await send(
            f"[{tag}] Hourly Update\n"
            f"Capital: {capital_fmt}\n"
            f"Today's P&amp;L: {sign}{pnl_fmt} ({sign}{daily_pnl_pct:.2f}%)\n"
            f"Trades today: {trade_count} | Open: {open_count}"
        )
    except Exception as e:
        logger.warning(f"notify_hourly_stats error: {e}")


async def notify_kill_switch(daily_loss_pct: float) -> None:
    await send(
        f"DEFENSIVE MODE ACTIVATED\n"
        f"Daily loss reached {daily_loss_pct:.2f}%\n"
        "Switched to conservative strategy - auto-recovery active."
    )


async def notify_daily_target(daily_pnl_pct: float) -> None:
    await send(
        f"Daily Target Reached!\n"
        f"P&L: +{daily_pnl_pct:.2f}%"
    )


async def notify_bot_started(paper: bool, testnet: bool) -> None:
    mode = "Paper" if paper else "Live"
    net = "Testnet" if testnet else "Mainnet"
    await send(f"InsideTrader Bot Started\nMode: {mode} | Exchange: {net}")
