"""Heat-based position sizing.

40% portfolio heat cap. Heat = cash (spot) + margin (futures).
Per-trade heat = heat_limit / max_simultaneous.
Single symbol capped at 10% of equity.
"""

from loguru import logger
from bot.config import settings

MIN_NOTIONAL_FUTURES = 35.0
MIN_NOTIONAL_SPOT = 10.0


def calculate_trade_heat(
    capital_usdt: float,
    open_heat_usdt: float,
    symbol_heat_usdt: float,
    params: dict,
) -> float:
    """Return the max USDT heat (cash/margin) this trade may use, or 0 if illegal."""
    heat_limit = capital_usdt * params["heat_limit_pct"] / 100
    free_heat = max(0.0, heat_limit - open_heat_usdt)

    max_sim = params["max_concurrent_trades"]
    per_trade_heat = heat_limit / max_sim if max_sim > 0 else free_heat

    max_sym_heat = capital_usdt * params["max_single_symbol_heat_pct"] / 100
    sym_remaining = max(0.0, max_sym_heat - symbol_heat_usdt)

    trade_heat = min(per_trade_heat, free_heat, sym_remaining)
    return trade_heat


def calculate_position_size(
    capital_usdt: float,
    price: float,
    mode: str,
    trade_heat: float,
    leverage: int | None = None,
) -> tuple[float, float]:
    """Return (quantity, notional_usdt) sized by heat budget.

    For SPOT: heat = notional (cash locked).
    For FUTURES: heat = margin = notional / leverage.
    """
    if trade_heat <= 0 or price <= 0:
        return 0.0, 0.0

    _leverage = leverage if leverage is not None else settings.futures_leverage

    if mode == "FUTURES":
        margin = trade_heat
        notional = margin * _leverage
    else:
        notional = trade_heat

    min_notional = MIN_NOTIONAL_FUTURES if mode == "FUTURES" else MIN_NOTIONAL_SPOT
    if notional < min_notional:
        logger.debug(
            f"Position notional ${notional:.2f} below minimum ${min_notional} — skipping"
        )
        return 0.0, 0.0

    quantity = notional / price
    return round(quantity, 8), round(notional, 4)


def calculate_sl_tp_prices(
    entry_price: float,
    mode: str,
    side: str = "BUY",
    sl_pct: float | None = None,
    tp_pct: float | None = None,
    leverage: int | None = None,
    atr_pct: float | None = None,
) -> tuple[float, float]:
    """Return (stop_loss_price, take_profit_price).

    TP stretches to 1.5x ATR when available, floored at 3% and capped at 6%.
    """
    _sl_pct = sl_pct if sl_pct is not None else settings.stop_loss_percent

    if atr_pct is not None and atr_pct > 0:
        _tp_pct = max(3.0, min(6.0, atr_pct * 1.5))
    else:
        _tp_pct = tp_pct if tp_pct is not None else settings.take_profit_percent

    if side == "BUY":
        stop_loss = entry_price * (1 - _sl_pct / 100)
        take_profit = entry_price * (1 + _tp_pct / 100)
    else:
        stop_loss = entry_price * (1 + _sl_pct / 100)
        take_profit = entry_price * (1 - _tp_pct / 100)

    return round(stop_loss, 8), round(take_profit, 8)
