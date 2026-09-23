"""Position sizing calculator.

Determines how much capital to allocate per trade, respecting:
  - Max risk % of portfolio per trade
  - Max single-coin exposure %
  - Binance minimum order notional ($10 USDT)
  - Futures leverage (capital is margin, not notional)
"""

from loguru import logger
from bot.config import settings

MIN_NOTIONAL_USDT = 10.0


def calculate_position_size(
    capital_usdt: float,
    price: float,
    mode: str,
    existing_exposure_usdt: float = 0.0,
    leverage: int | None = None,
    max_risk_per_trade_pct: float | None = None,
) -> tuple[float, float]:
    """Return (quantity_base_asset, notional_usdt) for a trade.

    Args:
        capital_usdt:            Total portfolio value in USDT.
        price:                   Current asset price in USDT.
        mode:                    'SPOT' or 'FUTURES'.
        existing_exposure_usdt:  Already allocated to this asset.
        leverage:                Futures leverage override (uses settings default if None).
        max_risk_per_trade_pct:  Per-trade risk % override (uses settings default if None).

    Returns:
        (quantity, notional_usdt) — both 0.0 if trade is not viable.
    """
    risk_pct = max_risk_per_trade_pct if max_risk_per_trade_pct is not None else settings.max_risk_per_trade_percent
    max_notional = capital_usdt * risk_pct / 100
    max_coin_exposure = capital_usdt * settings.max_single_coin_exposure_percent / 100

    available = max_coin_exposure - existing_exposure_usdt
    notional = min(max_notional, available)

    _leverage = leverage if leverage is not None else settings.futures_leverage
    if mode == "FUTURES":
        margin = notional
        notional = margin * _leverage

    if notional < MIN_NOTIONAL_USDT:
        # Bump up to minimum rather than rejecting — ensures trades open even when
        # risk_pct formula gives a tiny slice (e.g. 100 trades / $189 capital = $1.89)
        floored = min(MIN_NOTIONAL_USDT, available if mode == "SPOT" else capital_usdt * 0.15)
        if floored < MIN_NOTIONAL_USDT:
            logger.warning(
                f"Insufficient capital for minimum position: ${available:.2f} available, ${MIN_NOTIONAL_USDT} minimum"
            )
            return 0.0, 0.0
        logger.debug(f"Position bumped from ${notional:.2f} to minimum ${floored:.2f}")
        notional = floored

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

    When atr_pct is provided, take-profit is set dynamically to 1.5× ATR,
    floored at 2.5% and capped at 6.0%, giving larger targets on volatile moves.
    For futures, stop-loss is tighter due to leverage magnifying losses.
    """
    _sl_pct = sl_pct if sl_pct is not None else settings.stop_loss_percent
    _leverage = leverage if leverage is not None else settings.futures_leverage

    if atr_pct is not None and atr_pct > 0:
        _tp_pct = max(2.5, min(6.0, atr_pct * 1.5))
    else:
        _tp_pct = tp_pct if tp_pct is not None else settings.take_profit_percent

    if mode == "FUTURES":
        _sl_pct = _sl_pct / _leverage

    if side == "BUY":
        stop_loss = entry_price * (1 - _sl_pct / 100)
        take_profit = entry_price * (1 + _tp_pct / 100)
    else:
        stop_loss = entry_price * (1 + _sl_pct / 100)
        take_profit = entry_price * (1 - _tp_pct / 100)

    return round(stop_loss, 8), round(take_profit, 8)
