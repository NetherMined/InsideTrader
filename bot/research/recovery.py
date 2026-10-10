"""1m recovery researcher. Feedback only. Never places or blocks orders.

For every closed trade, watches the 1m candles for 60 minutes after the exit and
records whether price recovered to entry, reached the take-profit, and what the
trade would have made if it had been held longer.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import ccxt.async_support as ccxt
from loguru import logger
from sqlalchemy import text

from bot.db.connection import async_session

WINDOW_MINUTES = 60
LOOKBACK_HOURS = 168
BATCH_LIMIT = 60
POLL_SECONDS = 60
_EXCLUDED_REASONS = ("ghost_close",)

_UPSERT = text("""
    INSERT INTO trade_recovery (
        trade_id, symbol, side, close_reason, entry_price, exit_price, pnl_usdt,
        closed_at, hold_seconds, minutes_observed, final, best_fav_pct, worst_adv_pct,
        best_fav_usdt, recovered_to_entry, minutes_to_entry, hit_tp, minutes_to_tp,
        hold_pnl_5m, hold_pnl_15m, hold_pnl_60m, updated_at
    ) VALUES (
        :trade_id, :symbol, :side, :close_reason, :entry_price, :exit_price, :pnl_usdt,
        :closed_at, :hold_seconds, :minutes_observed, :final, :best_fav_pct, :worst_adv_pct,
        :best_fav_usdt, :recovered_to_entry, :minutes_to_entry, :hit_tp, :minutes_to_tp,
        :hold_pnl_5m, :hold_pnl_15m, :hold_pnl_60m, NOW()
    )
    ON CONFLICT (trade_id) DO UPDATE SET
        minutes_observed = EXCLUDED.minutes_observed, final = EXCLUDED.final,
        best_fav_pct = EXCLUDED.best_fav_pct, worst_adv_pct = EXCLUDED.worst_adv_pct,
        best_fav_usdt = EXCLUDED.best_fav_usdt,
        recovered_to_entry = EXCLUDED.recovered_to_entry,
        minutes_to_entry = EXCLUDED.minutes_to_entry, hit_tp = EXCLUDED.hit_tp,
        minutes_to_tp = EXCLUDED.minutes_to_tp, hold_pnl_5m = EXCLUDED.hold_pnl_5m,
        hold_pnl_15m = EXCLUDED.hold_pnl_15m, hold_pnl_60m = EXCLUDED.hold_pnl_60m,
        updated_at = NOW()
""")


def analyze_recovery(trade: dict, candles: list[list], now: datetime) -> dict:
    """Pure analysis of post-exit 1m candles ([ms, o, h, l, c, v]) for one closed trade."""
    side = trade["side"]
    entry = float(trade["entry_price"])
    tp = trade.get("take_profit_price")
    tp = float(tp) if tp else None
    notional = float(trade["quantity"]) * entry
    closed_at: datetime = trade["closed_at"]
    first_open_ms = (int(closed_at.timestamp() // 60) + 1) * 60_000
    now_ms = int(now.timestamp() * 1000)

    def raw_pct(price: float) -> float:
        return (price / entry - 1) * 100 if side == "BUY" else (entry - price) / entry * 100

    best_fav = None
    worst_adv = None
    recovered = False
    minutes_to_entry = None
    hit_tp = False
    minutes_to_tp = None
    hold: dict[int, float] = {}
    observed = 0

    for c in candles:
        open_ms = int(c[0])
        if open_ms < first_open_ms or open_ms + 60_000 > now_ms:
            continue
        minute = (open_ms - first_open_ms) // 60_000 + 1
        if minute > WINDOW_MINUTES:
            break
        observed = minute
        high, low, close = float(c[2]), float(c[3]), float(c[4])
        fav_price, adv_price = (high, low) if side == "BUY" else (low, high)
        fav, adv = raw_pct(fav_price), raw_pct(adv_price)
        best_fav = fav if best_fav is None else max(best_fav, fav)
        worst_adv = adv if worst_adv is None else min(worst_adv, adv)
        if not recovered and fav >= 0:
            recovered, minutes_to_entry = True, minute
        if tp and not hit_tp and ((side == "BUY" and high >= tp) or (side == "SELL" and low <= tp)):
            hit_tp, minutes_to_tp = True, minute
        if minute in (5, 15, 60):
            hold[minute] = notional * raw_pct(close) / 100

    return {
        "minutes_observed": observed,
        "final": observed >= WINDOW_MINUTES,
        "best_fav_pct": best_fav,
        "worst_adv_pct": worst_adv,
        "best_fav_usdt": notional * best_fav / 100 if best_fav is not None else None,
        "recovered_to_entry": recovered,
        "minutes_to_entry": minutes_to_entry,
        "hit_tp": hit_tp,
        "minutes_to_tp": minutes_to_tp,
        "hold_pnl_5m": hold.get(5),
        "hold_pnl_15m": hold.get(15),
        "hold_pnl_60m": hold.get(60),
    }


async def _pending_trades() -> list[dict]:
    async with async_session() as session:
        rows = (await session.execute(
            text("""
                SELECT t.id, t.symbol, t.side, t.entry_price, t.exit_price, t.quantity,
                       t.take_profit_price, t.pnl_usdt, t.opened_at, t.closed_at,
                       t.extra->>'close_reason' AS close_reason
                FROM trades t
                LEFT JOIN trade_recovery r ON r.trade_id = t.id
                WHERE t.status = 'CLOSED'
                  AND t.closed_at IS NOT NULL
                  AND t.closed_at > NOW() - make_interval(hours => :hours)
                  AND COALESCE(t.extra->>'close_reason', '') <> ALL(:excluded)
                  AND (r.trade_id IS NULL OR r.final = FALSE)
                ORDER BY t.closed_at DESC
                LIMIT :lim
            """),
            {"hours": LOOKBACK_HOURS, "excluded": list(_EXCLUDED_REASONS), "lim": BATCH_LIMIT},
        )).mappings().all()
    return [dict(r) for r in rows]


async def _process_trade(exchange: ccxt.binance, trade: dict, now: datetime) -> bool:
    since_ms = (int(trade["closed_at"].timestamp() // 60) + 1) * 60_000
    if since_ms + 60_000 > int(now.timestamp() * 1000):
        return False  # first post-exit candle not closed yet
    try:
        candles = await exchange.fetch_ohlcv(
            f"{trade['symbol']}:USDT", "1m", since=since_ms, limit=WINDOW_MINUTES + 2
        )
    except ccxt.BadSymbol:
        logger.debug(f"{trade['symbol']}: no 1m futures data for recovery tracking")
        return False
    analysis = analyze_recovery(trade, candles, now)
    if analysis["minutes_observed"] == 0:
        return False
    hold_seconds = None
    if trade.get("opened_at") and trade.get("closed_at"):
        hold_seconds = int((trade["closed_at"] - trade["opened_at"]).total_seconds())
    params = {
        "trade_id": trade["id"], "symbol": trade["symbol"], "side": trade["side"],
        "close_reason": trade.get("close_reason"), "entry_price": trade["entry_price"],
        "exit_price": trade.get("exit_price"), "pnl_usdt": trade.get("pnl_usdt"),
        "closed_at": trade["closed_at"], "hold_seconds": hold_seconds, **analysis,
    }
    async with async_session() as session:
        await session.execute(_UPSERT, params)
        await session.commit()
    return True


async def run_recovery_loop(stop_event: asyncio.Event) -> None:
    logger.info(f"Recovery researcher started — 1m candles, {WINDOW_MINUTES}m post-exit window")
    exchange = ccxt.binance({"options": {"defaultType": "future"}})
    try:
        while not stop_event.is_set():
            try:
                now = datetime.now(timezone.utc)
                trades = await _pending_trades()
                updated = 0
                for trade in trades:
                    if await _process_trade(exchange, trade, now):
                        updated += 1
                    await asyncio.sleep(0.2)
                if updated:
                    logger.info(f"Recovery researcher updated {updated}/{len(trades)} trades")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"Recovery loop error: {exc}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        await exchange.close()
