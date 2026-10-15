"""Single source of truth for trade P&L.

quantity * entry_price is the full position notional — leverage is already in it.
Leverage only scales the return on margin (effective_pct), never the USDT amount.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PnL:
    raw_pct: float        # price move in the trade's favour, %
    effective_pct: float  # return on margin, % (raw_pct * leverage)
    gross_usdt: float     # quantity * price change, before fees
    fee_usdt: float       # open + close taker fees
    net_usdt: float       # gross_usdt - fee_usdt


def compute_pnl(
    side: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
    leverage: int = 1,
    fee_rate: float = 0.0,
) -> PnL:
    direction = 1.0 if side == "BUY" else -1.0
    raw_pct = (exit_price / entry_price - 1) * 100 * direction if entry_price > 0 else 0.0
    gross_usdt = quantity * (exit_price - entry_price) * direction
    fee_usdt = abs(quantity) * (entry_price + exit_price) * fee_rate
    return PnL(
        raw_pct=raw_pct,
        effective_pct=raw_pct * max(leverage, 1),
        gross_usdt=gross_usdt,
        fee_usdt=fee_usdt,
        net_usdt=gross_usdt - fee_usdt,
    )
