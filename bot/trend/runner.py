"""Trend sleeve paper runner: hourly cycle at minute 5 (UTC), separate from the main bot.

Run: python -m bot.trend.runner            (loop)
     python -m bot.trend.runner --once     (one cycle, then exit)
     python -m bot.trend.runner --resume   (clear a kill-switch halt, then loop)

Each cycle, in order: charge funding settled since the last cycle, check stops on 1h bars, rebalance when the
last completed daily close is a rebalance close (at most once per signal day), apply the kill switch, persist
everything in one transaction, then write the day's equity snapshot for both bots.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

import aiohttp
from loguru import logger
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from bot.config import settings
from bot.redis_conn import make_redis
from bot.db.connection import async_session, engine
from bot.db.models import (
    Base, StrategyEquity, TrendAccount, TrendFill, TrendFunding, TrendPosition, TrendRebalance,
)
from bot.notifications import telegram
from bot.trend import engine as E
from bot.trend import market as M
from bot.trend import strategy as T

TABLES = [TrendAccount.__table__, TrendPosition.__table__, TrendFill.__table__, TrendFunding.__table__,
          TrendRebalance.__table__, StrategyEquity.__table__]
EQUITY_KEY = "trend:capital_usdt"
SUMMARY_KEY = "trend:summary"


async def ensure_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=TABLES))


async def current_bot_equity(session, redis, http) -> tuple[float, int]:
    """Main bot paper equity: free capital + margin locked in open trades + their unrealised P&L."""
    free = float(await redis.get("paper:capital_usdt") or settings.starting_capital_usdt)
    rows = (await session.execute(text(
        "SELECT symbol, side, mode, entry_price, quantity, leverage FROM trades WHERE status = 'OPEN'"))).all()
    marks = await M.prices(http, {r.symbol.replace("/", "").split(":")[0] for r in rows}) if rows else {}
    equity = free
    for r in rows:
        lev = max(int(r.leverage or 1), 1) if r.mode == "FUTURES" else 1
        price = marks.get(r.symbol.replace("/", "").split(":")[0], r.entry_price)
        sign = 1 if r.side == "BUY" else -1
        equity += r.quantity * r.entry_price / lev + sign * (price - r.entry_price) * r.quantity
    return equity, len(rows)


async def load(session) -> tuple[TrendAccount | None, E.Book | None, dict, set[str]]:
    acct = await session.get(TrendAccount, 1)
    if acct is None:
        return None, None, {}, set()
    positions = {p.symbol: E.Position(p.qty, p.entry_price, p.stop_price, p.opened_at)
                 for p in (await session.execute(select(TrendPosition))).scalars()}
    book = E.Book(acct.cash, acct.peak_equity, positions, acct.halted, acct.halted_reason)
    last_charged = dict((await session.execute(text(
        "SELECT symbol, MAX(time) FROM trend_funding GROUP BY symbol"))).all())
    done = set((await session.execute(select(TrendRebalance.signal_day))).scalars())
    return acct, book, last_charged, done


def live_summary(acct, book: E.Book, prices: dict, bot_equity: float, bot_open: int, now: datetime) -> dict:
    """Both bots valued at live prices plus the trend holdings, for the Test Results page."""
    equity = book.equity(prices)
    holdings = []
    for sym, p in sorted(book.positions.items()):
        price = prices.get(sym, p.entry_price)
        holdings.append({"symbol": sym, "qty": p.qty, "entry_price": p.entry_price, "price": price,
                         "value_usd": p.qty * price, "pnl_pct": (price / p.entry_price - 1) * 100,
                         "weight_pct": p.qty * price / equity * 100 if equity else 0.0,
                         "stop_price": p.stop_price, "opened": p.opened_at.strftime("%Y-%m-%d")})
    return {"as_of": now.isoformat(), "start_capital": acct.start_capital, "started_at": acct.started_at.isoformat()
            if acct.started_at else None, "halted": book.halted, "halted_reason": book.halted_reason,
            "trend": {"equity": equity, "open_positions": len(book.positions), "peak_equity": book.peak_equity},
            "current_bot": {"equity": bot_equity, "open_positions": bot_open},
            "holdings": holdings}


async def snapshot(session, day: str, strategy: str, equity: float, n_open: int) -> None:
    await session.execute(insert(StrategyEquity).values(day=day, strategy=strategy, equity=equity,
                                                         open_positions=n_open).on_conflict_do_nothing())


async def cycle(redis, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    async with aiohttp.ClientSession() as http, async_session() as session:
        acct, book, last_charged, done = await load(session)
        if acct is None:
            start, _ = await current_bot_equity(session, redis, http)
            acct = TrendAccount(id=1, start_capital=start, cash=start, peak_equity=start, halted=False)
            session.add(acct)
            book = E.Book(start, start)
            logger.info(f"Trend sleeve account opened with ${start:.2f} (current bot equity)")

        symbols = set(T.MAJORS) | set(book.positions)
        prices = await M.prices(http, symbols)

        charges = []
        for sym, pos in list(book.positions.items()):
            since = max(last_charged.get(sym, pos.opened_at), pos.opened_at)
            events = [(s, t, r, m or prices.get(s, pos.entry_price))
                      for s, t, r, m in await M.funding_events(http, sym, since) if t <= now]
            charges += E.apply_funding(book, events, last_charged)

        stop_fills = []
        for sym, pos in list(book.positions.items()):
            since = max(acct.last_stop_check or pos.opened_at, pos.opened_at)
            bars = await M.hourly_bars(http, sym, since)
            stop_fills += E.check_stops(book, {sym: bars}, now)
        acct.last_stop_check = now

        rebal_fills, rebal_row = [], None
        closes = await M.daily_closes(http, T.MAJORS)
        signal_day = max(rows[-1][0] for rows in closes.values() if rows)
        reason = E.rebalance_due(signal_day, done, book.halted)
        if reason:
            hist = {s: [c for d, c in rows if d <= signal_day] for s, rows in closes.items()}
            targets = T.target_weights(hist)
            equity_before = book.equity(prices)
            mins = await M.min_notional(http, T.MAJORS)
            rebal_fills = E.rebalance(book, targets, prices, mins, now, reason)
            rebal_row = TrendRebalance(signal_day=signal_day.isoformat(), executed_at=now, equity_before=equity_before,
                                       orders=len(rebal_fills), targets={k: round(v, 6) for k, v in targets.items()})

        was_halted = book.halted
        kill_fills = E.update_peak_and_kill(book, prices, now)
        newly_halted = book.halted and not was_halted

        acct.cash, acct.peak_equity = book.cash, book.peak_equity
        acct.halted, acct.halted_reason = book.halted, book.halted_reason
        await session.execute(delete(TrendPosition))
        session.add_all(TrendPosition(symbol=s, qty=p.qty, entry_price=p.entry_price, stop_price=p.stop_price,
                                      opened_at=p.opened_at) for s, p in book.positions.items())
        session.add_all(TrendFill(**f.__dict__) for f in stop_fills + rebal_fills + kill_fills)
        session.add_all(TrendFunding(**c.__dict__) for c in charges)
        if rebal_row:
            session.add(rebal_row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            logger.warning("Trend cycle skipped: another run already recorded this rebalance or funding")
            return {"skipped": True}

        equity = book.equity(prices)
        day = now.date().isoformat()
        bot_eq, bot_open = await current_bot_equity(session, redis, http)
        await snapshot(session, day, "trend", equity, len(book.positions))
        await snapshot(session, day, "current_bot", bot_eq, bot_open)
        await session.commit()
        await redis.set(EQUITY_KEY, f"{equity:.4f}")
        await redis.set("trend:last_cycle", now.isoformat())
        await redis.set(SUMMARY_KEY, json.dumps(live_summary(acct, book, prices, bot_eq, bot_open, now)))

    await _notify(equity, book, signal_day, rebal_fills, stop_fills, newly_halted)
    logger.info(f"Trend cycle {now:%Y-%m-%d %H:%M}: equity ${equity:.2f}, {len(book.positions)} positions, "
                f"{len(rebal_fills)} rebalance / {len(stop_fills)} stop / {len(kill_fills)} kill fills, "
                f"{len(charges)} funding charges")
    return {"equity": equity, "positions": len(book.positions), "rebalance_fills": len(rebal_fills),
            "stop_fills": len(stop_fills), "kill_fills": len(kill_fills), "funding_charges": len(charges)}


async def _notify(equity, book, signal_day, rebal_fills, stop_fills, newly_halted) -> None:
    if rebal_fills:
        held = ", ".join(sorted(book.positions)) or "none (all flat)"
        await telegram.send(f"[PAPER] Trend sleeve rebalanced ({signal_day} close)\n"
                            f"Orders: {len(rebal_fills)} | Holding: {held}\nEquity: ${equity:,.2f}")
    for f in stop_fills:
        await telegram.send(f"[PAPER] Trend sleeve stop hit: {f.symbol} sold at ${f.fill_price:,.4f}\n"
                            f"Equity: ${equity:,.2f}")
    if newly_halted:
        await telegram.send(f"[PAPER] Trend sleeve KILL SWITCH: {book.halted_reason}\n"
                            "All positions closed. Restart with: python -m bot.trend.runner --resume")


def _next_run(now: datetime) -> datetime:
    nxt = now.replace(minute=5, second=0, microsecond=0)
    return nxt if nxt > now else nxt + timedelta(hours=1)


async def main(once: bool = False, resume: bool = False) -> None:
    await ensure_tables()
    redis = make_redis(settings.redis_url)
    if resume:
        async with async_session() as session:
            acct = await session.get(TrendAccount, 1)
            if acct and acct.halted:
                acct.halted, acct.halted_reason = False, None
                await session.commit()
                logger.info("Trend sleeve halt cleared")
    logger.info("Trend sleeve runner started")
    while True:
        try:
            await cycle(redis)
        except Exception as exc:
            logger.exception(f"Trend cycle failed: {exc}")
        if once:
            break
        now = datetime.now(timezone.utc)
        await asyncio.sleep((_next_run(now) - now).total_seconds())
    await redis.aclose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(once=args.once, resume=args.resume))
