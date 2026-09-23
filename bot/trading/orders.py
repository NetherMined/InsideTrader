"""ccxt order wrappers for Binance Spot and Futures.

All order functions are async and return a standardised dict.
Authentication uses the order-specific API key (testnet when USE_TESTNET=true).
"""

import ccxt.async_support as ccxt
from loguru import logger
from bot.config import settings


def _spot_exchange(use_testnet: bool | None = None) -> ccxt.binance:
    testnet = use_testnet if use_testnet is not None else settings.use_testnet
    api_key = settings.binance_testnet_api_key if testnet else settings.binance_api_key
    api_secret = settings.binance_testnet_api_secret if testnet else settings.binance_api_secret
    ex = ccxt.binance({
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "spot"},
    })
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


def _futures_exchange(use_testnet: bool | None = None) -> ccxt.binance:
    testnet = use_testnet if use_testnet is not None else settings.use_testnet
    api_key = settings.binance_testnet_api_key if testnet else settings.binance_api_key
    api_secret = settings.binance_testnet_api_secret if testnet else settings.binance_api_secret
    ex = ccxt.binance({
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "future"},
    })
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


async def get_account_balance(use_testnet: bool | None = None) -> float:
    """Return total USDT balance from Binance account."""
    exchange = _spot_exchange(use_testnet)
    try:
        balance = await exchange.fetch_balance()
        return float(balance.get("USDT", {}).get("free", 0.0))
    except Exception as e:
        logger.error(f"Failed to fetch balance: {e}")
        return settings.starting_capital_usdt
    finally:
        await exchange.close()


def _extract_fill_price(order: dict) -> float | None:
    """Extract fill price from ccxt order, returning None if unavailable."""
    avg = order.get("average")
    if avg is not None:
        price = float(avg)
        if price > 0:
            return price
    cost = order.get("cost")
    amount = order.get("filled") or order.get("amount")
    if cost and amount and float(amount) > 0:
        price = float(cost) / float(amount)
        if price > 0:
            return price
    return None


async def place_spot_market_buy(symbol: str, quantity: float, use_testnet: bool | None = None) -> dict:
    exchange = _spot_exchange(use_testnet)
    try:
        order = await exchange.create_market_buy_order(symbol, quantity)
        fill_price = _extract_fill_price(order)
        if fill_price is None:
            logger.error(f"SPOT BUY {symbol}: order {order.get('id')} has no valid fill price (status={order.get('status')})")
            return {"ok": False, "error": "Order placed but fill price unavailable"}
        logger.info(f"SPOT BUY {symbol}: qty={quantity} id={order['id']} fill=${fill_price:.6f}")
        return {"ok": True, "order_id": str(order["id"]), "fill_price": fill_price}
    except Exception as e:
        logger.error(f"SPOT BUY failed {symbol}: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


async def place_spot_market_sell(symbol: str, quantity: float, use_testnet: bool | None = None) -> dict:
    exchange = _spot_exchange(use_testnet)
    try:
        order = await exchange.create_market_sell_order(symbol, quantity)
        fill_price = _extract_fill_price(order)
        if fill_price is None:
            logger.error(f"SPOT SELL {symbol}: order {order.get('id')} has no valid fill price (status={order.get('status')})")
            return {"ok": False, "error": "Order placed but fill price unavailable"}
        logger.info(f"SPOT SELL {symbol}: qty={quantity} id={order['id']} fill=${fill_price:.6f}")
        return {"ok": True, "order_id": str(order["id"]), "fill_price": fill_price}
    except Exception as e:
        logger.error(f"SPOT SELL failed {symbol}: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


def _futures_symbol(symbol: str) -> str:
    """Convert BTC/USDT → BTC/USDT:USDT for Binance USDM futures."""
    return symbol if ":" in symbol else f"{symbol}:USDT"


async def place_futures_market_buy(symbol: str, quantity: float, leverage: int, use_testnet: bool | None = None) -> dict:
    exchange = _futures_exchange(use_testnet)
    fsym = _futures_symbol(symbol)
    try:
        try:
            await exchange.set_leverage(leverage, fsym)
        except Exception as e:
            logger.error(f"FUTURES set_leverage failed {symbol}: {e}")
            return {"ok": False, "error": f"leverage_error: {e}"}
        order = await exchange.create_market_buy_order(fsym, quantity)
        fill_price = _extract_fill_price(order)
        if fill_price is None:
            logger.error(f"FUTURES LONG {symbol}: order {order.get('id')} has no valid fill price")
            return {"ok": False, "error": "Order placed but fill price unavailable"}
        logger.info(f"FUTURES LONG {symbol}: qty={quantity} lev={leverage}x id={order['id']} fill=${fill_price:.6f}")
        return {"ok": True, "order_id": str(order["id"]), "fill_price": fill_price}
    except Exception as e:
        logger.error(f"FUTURES BUY failed {symbol}: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


async def place_futures_market_sell(symbol: str, quantity: float, leverage: int, use_testnet: bool | None = None) -> dict:
    """Open a SHORT position in futures."""
    exchange = _futures_exchange(use_testnet)
    fsym = _futures_symbol(symbol)
    try:
        try:
            await exchange.set_leverage(leverage, fsym)
        except Exception as e:
            logger.error(f"FUTURES set_leverage failed {symbol}: {e}")
            return {"ok": False, "error": f"leverage_error: {e}"}
        order = await exchange.create_market_sell_order(fsym, quantity)
        fill_price = _extract_fill_price(order)
        if fill_price is None:
            logger.error(f"FUTURES SHORT {symbol}: order {order.get('id')} has no valid fill price")
            return {"ok": False, "error": "Order placed but fill price unavailable"}
        logger.info(f"FUTURES SHORT {symbol}: qty={quantity} lev={leverage}x id={order['id']} fill=${fill_price:.6f}")
        return {"ok": True, "order_id": str(order["id"]), "fill_price": fill_price}
    except Exception as e:
        logger.error(f"FUTURES SHORT failed {symbol}: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


async def place_futures_market_close(symbol: str, quantity: float, use_testnet: bool | None = None) -> dict:
    exchange = _futures_exchange(use_testnet)
    fsym = _futures_symbol(symbol)
    try:
        order = await exchange.create_market_sell_order(
            fsym, quantity, params={"reduceOnly": True}
        )
        fill_price = _extract_fill_price(order)
        if fill_price is None:
            logger.error(f"FUTURES CLOSE {symbol}: order {order.get('id')} has no valid fill price")
            return {"ok": False, "error": "Order placed but fill price unavailable"}
        logger.info(f"FUTURES CLOSE {symbol}: qty={quantity} id={order['id']} fill=${fill_price:.6f}")
        return {"ok": True, "order_id": str(order["id"]), "fill_price": fill_price}
    except Exception as e:
        logger.error(f"FUTURES CLOSE failed {symbol}: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        try:
            await exchange.close()
        except Exception:
            pass


async def get_current_price(symbol: str) -> float | None:
    exchange = ccxt.binance({"options": {"defaultType": "spot"}})
    try:
        ticker = await exchange.fetch_ticker(symbol)
        return float(ticker["last"])
    except Exception as e:
        logger.warning(f"Price fetch failed for {symbol}: {e}")
        return None
    finally:
        await exchange.close()
