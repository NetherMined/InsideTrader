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


def max_stop_pct(leverage: int) -> float:
    """Widest allowed stop: sl_max_pct, and never more than a 3.8% leveraged loss."""
    return min(settings.sl_max_pct, 3.8 / max(leverage, 1))


def stop_distance_pct(atr_pct: float | None, leverage: int) -> float:
    """Stop distance in price %: ATR x sl_atr_mult, clamped to [sl_min_pct, max_stop_pct]."""
    cap = max_stop_pct(leverage)
    floor = min(settings.sl_min_pct, cap)
    base = atr_pct * settings.sl_atr_mult if atr_pct and atr_pct > 0 else floor
    return max(floor, min(cap, base))


def clamp_stop(entry_price: float, side: str, stop_loss: float, leverage: int) -> float:
    """Move a stop (e.g. zone-based) so its distance sits within [sl_min_pct, max_stop_pct]."""
    cap = max_stop_pct(leverage)
    floor = min(settings.sl_min_pct, cap)
    dist_pct = abs(entry_price - stop_loss) / entry_price * 100
    dist_pct = max(floor, min(cap, dist_pct))
    if side == "BUY":
        return round(entry_price * (1 - dist_pct / 100), 8)
    return round(entry_price * (1 + dist_pct / 100), 8)


def cap_take_profit(entry_price: float, side: str, stop_loss: float, take_profit: float) -> float:
    """Keep TP within tp_max_sl_multiple stop distances of entry."""
    max_dist = abs(entry_price - stop_loss) * settings.tp_max_sl_multiple
    if side == "BUY":
        return round(min(take_profit, entry_price + max_dist), 8)
    return round(max(take_profit, entry_price - max_dist), 8)


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

    SL distance comes from stop_distance_pct (ATR-scaled, clamped).
    TP: 1.5x ATR floored at config default, capped at 8%. The caller caps it
    further with cap_take_profit.
    """
    _lev = leverage if leverage is not None else settings.futures_leverage
    _default_tp = tp_pct if tp_pct is not None else settings.take_profit_percent
    _sl_pct = stop_distance_pct(atr_pct, _lev)

    if atr_pct is not None and atr_pct > 0:
        _tp_pct = max(_default_tp, min(8.0, atr_pct * 1.5))
    else:
        _tp_pct = _default_tp

    if side == "BUY":
        stop_loss = entry_price * (1 - _sl_pct / 100)
        take_profit = entry_price * (1 + _tp_pct / 100)
    else:
        stop_loss = entry_price * (1 + _sl_pct / 100)
        take_profit = entry_price * (1 - _tp_pct / 100)

    return round(stop_loss, 8), round(take_profit, 8)
