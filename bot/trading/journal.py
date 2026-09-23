"""Trade journal — persists all positions and trades to PostgreSQL."""

import json
from datetime import datetime, timezone
from loguru import logger
from sqlalchemy import text
from bot.db.connection import async_session


async def open_position(
    symbol: str,
    side: str,
    mode: str,
    entry_price: float,
    quantity: float,
    stop_loss_price: float,
    take_profit_price: float,
    leverage: int,
    paper_trade: bool,
    binance_order_id: str | None = None,
    estimated_fee_usdt: float = 0.0,
    funding_rate: float = 0.0,
    regime: str = "UNKNOWN",
) -> int:
    """Insert a new open position. Returns the position ID."""
    async with async_session() as session:
        async with session.begin():
            trade_result = await session.execute(
                text("""
                    INSERT INTO trades
                        (symbol, side, mode, entry_price, quantity, leverage,
                         stop_loss_price, take_profit_price, status,
                         binance_order_id, paper_trade, estimated_fee_usdt,
                         funding_rate, regime)
                    VALUES
                        (:symbol, :side, :mode, :entry_price, :quantity, :leverage,
                         :stop_loss_price, :take_profit_price, 'OPEN',
                         :order_id, :paper_trade, :estimated_fee,
                         :funding_rate, :regime)
                    RETURNING id
                """),
                {
                    "symbol": symbol, "side": side, "mode": mode,
                    "entry_price": entry_price, "quantity": quantity,
                    "leverage": leverage, "stop_loss_price": stop_loss_price,
                    "take_profit_price": take_profit_price,
                    "order_id": binance_order_id, "paper_trade": paper_trade,
                    "estimated_fee": estimated_fee_usdt,
                    "funding_rate": funding_rate,
                    "regime": regime,
                },
            )
            trade_id = trade_result.scalar()
            if trade_id is None:
                raise ValueError(f"Failed to insert trade for {symbol}")

            result = await session.execute(
                text("""
                    INSERT INTO positions
                        (symbol, side, mode, entry_price, current_price, quantity,
                         leverage, stop_loss_price, take_profit_price,
                         unrealized_pnl, paper_trade, trade_id)
                    VALUES
                        (:symbol, :side, :mode, :entry_price, :entry_price, :quantity,
                         :leverage, :stop_loss_price, :take_profit_price,
                         0.0, :paper_trade, :trade_id)
                    RETURNING id
                """),
                {
                    "symbol": symbol, "side": side, "mode": mode,
                    "entry_price": entry_price, "quantity": quantity,
                    "leverage": leverage, "stop_loss_price": stop_loss_price,
                    "take_profit_price": take_profit_price, "paper_trade": paper_trade,
                    "trade_id": trade_id,
                },
            )
            position_id = result.scalar()

    logger.info(f"Opened position #{position_id}: {side} {symbol} @ ${entry_price:.4f} [{mode}]")
    return position_id


async def update_position_price(position_id: int, current_price: float, unrealized_pnl: float) -> None:
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    UPDATE positions
                    SET current_price = :price, unrealized_pnl = :pnl
                    WHERE id = :id
                """),
                {"price": current_price, "pnl": unrealized_pnl, "id": position_id},
            )


async def close_position(
    position_id: int,
    symbol: str,
    exit_price: float,
    pnl_usdt: float,
    pnl_pct: float,
    close_reason: str = "target",
    trade_id: int | None = None,
    estimated_fee_usdt: float = 0.0,
    gross_pnl_usdt: float = 0.0,
) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            if trade_id is None:
                row = await session.execute(
                    text("SELECT trade_id FROM positions WHERE id = :id"),
                    {"id": position_id},
                )
                trade_id = (row.scalar()) or None

            await session.execute(
                text("DELETE FROM positions WHERE id = :id"),
                {"id": position_id},
            )

            if trade_id is not None:
                await session.execute(
                    text("""
                        UPDATE trades
                        SET exit_price = :exit_price,
                            pnl_usdt = :pnl_usdt,
                            pnl_percent = :pnl_pct,
                            estimated_fee_usdt = :estimated_fee,
                            gross_pnl_usdt = :gross_pnl,
                            status = 'CLOSED',
                            closed_at = :closed_at,
                            extra = CAST(:extra AS jsonb)
                        WHERE id = :trade_id
                    """),
                    {
                        "exit_price": exit_price, "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct,
                        "estimated_fee": estimated_fee_usdt,
                        "gross_pnl": gross_pnl_usdt,
                        "closed_at": now, "trade_id": trade_id,
                        "extra": json.dumps({"close_reason": close_reason}),
                    },
                )
            else:
                await session.execute(
                    text("""
                        UPDATE trades
                        SET exit_price = :exit_price,
                            pnl_usdt = :pnl_usdt,
                            pnl_percent = :pnl_pct,
                            status = 'CLOSED',
                            closed_at = :closed_at,
                            extra = CAST(:extra AS jsonb)
                        WHERE id = (
                            SELECT id FROM trades
                            WHERE symbol = :symbol AND status = 'OPEN'
                            ORDER BY opened_at DESC
                            LIMIT 1
                        )
                    """),
                    {
                        "exit_price": exit_price, "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct,
                        "closed_at": now, "symbol": symbol,
                        "extra": json.dumps({"close_reason": close_reason}),
                    },
                )

    emoji = "✅" if pnl_usdt >= 0 else "❌"
    logger.info(
        f"Closed position #{position_id} {symbol}: {emoji} "
        f"${pnl_usdt:+.4f} ({pnl_pct:+.2f}%) [{close_reason}]"
    )


async def get_pnl_summary() -> dict:
    """Return total realized profit, total realized loss, win count, and loss count from all closed trades."""
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT
                    COALESCE(SUM(CASE WHEN pnl_usdt > 0 THEN pnl_usdt ELSE 0 END), 0) AS total_profit_usdt,
                    COALESCE(SUM(CASE WHEN pnl_usdt < 0 THEN pnl_usdt ELSE 0 END), 0) AS total_loss_usdt,
                    COUNT(CASE WHEN pnl_usdt > 0 THEN 1 END) AS win_count,
                    COUNT(CASE WHEN pnl_usdt < 0 THEN 1 END) AS loss_count
                FROM trades
                WHERE status = 'CLOSED'
            """)
        )
        row = result.mappings().first()
        if not row:
            return {"total_profit_usdt": 0.0, "total_loss_usdt": 0.0, "win_count": 0, "loss_count": 0}
        return {
            "total_profit_usdt": round(float(row["total_profit_usdt"]), 4),
            "total_loss_usdt": round(float(row["total_loss_usdt"]), 4),
            "win_count": int(row["win_count"]),
            "loss_count": int(row["loss_count"]),
        }


async def get_open_positions() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT id, symbol, side, mode, entry_price, current_price,
                       quantity, leverage, stop_loss_price, take_profit_price,
                       unrealized_pnl, paper_trade, trade_id,
                       opened_at AT TIME ZONE 'UTC' AS opened_at
                FROM positions
                ORDER BY opened_at DESC
            """)
        )
        return [dict(r) for r in result.mappings().all()]


async def update_position_tp(position_id: int, take_profit_price: float) -> None:
    async with async_session() as session:
        async with session.begin():
            await session.execute(
                text("UPDATE positions SET take_profit_price = :tp WHERE id = :id"),
                {"tp": take_profit_price, "id": position_id},
            )
