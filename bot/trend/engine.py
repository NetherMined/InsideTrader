"""Trend sleeve paper engine on an in-memory book. No I/O: the runner loads the book, calls these, persists the result.

Accounting is cash-based: a BUY pays qty x fill price + fee from cash, a SELL adds qty x fill price - fee,
funding moves cash by qty x mark x rate (a long pays positive funding). Equity = cash + sum(qty x price).
Costs match the Phase 1 backtest: taker 0.05% + slippage 0.03% per fill, plus 0.05% extra on stop exits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping

from bot.trend import strategy as T

TAKER = 0.0005
SLIP = 0.0003
STOP_SLIP = 0.0005


@dataclass
class Position:
    qty: float
    entry_price: float
    stop_price: float
    opened_at: datetime


@dataclass
class Book:
    cash: float
    peak_equity: float
    positions: dict[str, Position] = field(default_factory=dict)
    halted: bool = False
    halted_reason: str | None = None

    def equity(self, prices: Mapping[str, float]) -> float:
        return self.cash + sum(p.qty * prices.get(s, p.entry_price) for s, p in self.positions.items())

    def quantities(self) -> dict[str, float]:
        return {s: p.qty for s, p in self.positions.items()}


@dataclass(frozen=True)
class Fill:
    time: datetime
    symbol: str
    side: str
    qty: float
    ref_price: float
    fill_price: float
    fee_usdt: float
    reason: str


@dataclass(frozen=True)
class FundingCharge:
    time: datetime
    symbol: str
    rate: float
    qty: float
    mark_price: float
    amount_usdt: float


def execute(book: Book, symbol: str, side: str, qty: float, ref_price: float, reason: str, now: datetime,
            stop: bool = False) -> Fill:
    """Fill a market order on the book and return the record."""
    slip = SLIP + (STOP_SLIP if stop else 0.0)
    fill_price = ref_price * (1 + slip) if side == "BUY" else ref_price * (1 - slip)
    fee = qty * fill_price * TAKER
    pos = book.positions.get(symbol)
    if side == "BUY":
        book.cash -= qty * fill_price + fee
        if pos:
            total = pos.qty + qty
            pos.entry_price = (pos.qty * pos.entry_price + qty * fill_price) / total
            pos.qty = total
            pos.stop_price = T.stop_price(pos.entry_price)
        else:
            book.positions[symbol] = Position(qty, fill_price, T.stop_price(fill_price), now)
    else:
        if not pos:
            raise ValueError(f"no {symbol} position to sell")
        qty = min(qty, pos.qty)
        book.cash += qty * fill_price - fee
        pos.qty -= qty
        if pos.qty <= 1e-12:
            del book.positions[symbol]
    return Fill(now, symbol, side, qty, ref_price, fill_price, fee, reason)


def apply_funding(book: Book, events: Iterable[tuple[str, datetime, float, float]],
                  last_charged: Mapping[str, datetime]) -> list[FundingCharge]:
    """Charge funding events (symbol, time, rate, mark) for positions open at that time, once each."""
    charges = []
    for symbol, when, rate, mark in sorted(events, key=lambda e: e[1]):
        pos = book.positions.get(symbol)
        if not pos or when <= pos.opened_at:
            continue
        if symbol in last_charged and when <= last_charged[symbol]:
            continue
        amount = pos.qty * mark * rate
        book.cash -= amount
        charges.append(FundingCharge(when, symbol, rate, pos.qty, mark, amount))
    return charges


def check_stops(book: Book, bars: Mapping[str, list[tuple[datetime, float, float]]], now: datetime) -> list[Fill]:
    """Exit any long whose stop was crossed. `bars` holds (open_time, open, low) per symbol since the last check.

    Fills at the stop, or at the bar's open when it gapped below the stop.
    """
    fills = []
    for symbol, pos in list(book.positions.items()):
        for _, open_, low in bars.get(symbol, []):
            if low <= pos.stop_price:
                ref = min(open_, pos.stop_price)
                fills.append(execute(book, symbol, "SELL", pos.qty, ref, "stop", now, stop=True))
                break
    return fills


def rebalance(book: Book, targets: Mapping[str, float], prices: Mapping[str, float],
              min_notional: Mapping[str, float], now: datetime, reason: str = "rebalance") -> list[Fill]:
    """Move the book to target weights at current prices. Sells first so cash is freed before buys."""
    equity = book.equity(prices)
    orders = T.orders_for(targets, equity, prices, book.quantities(), min_notional)
    fills = []
    for o in sorted(orders, key=lambda o: o.side != "SELL"):
        fills.append(execute(book, o.symbol, o.side, o.qty, prices[o.symbol], reason, now))
    return fills


def update_peak_and_kill(book: Book, prices: Mapping[str, float], now: datetime) -> list[Fill]:
    """Track peak equity; on a kill-switch breach close everything and halt the book."""
    equity = book.equity(prices)
    book.peak_equity = max(book.peak_equity, equity)
    if book.halted or not T.kill_switch_hit(equity, book.peak_equity):
        return []
    fills = [execute(book, s, "SELL", p.qty, prices[s], "kill", now) for s, p in list(book.positions.items())]
    book.halted = True
    book.halted_reason = (f"Equity {equity:.2f} fell {T.KILL_DRAWDOWN:.0%} below peak {book.peak_equity:.2f} "
                          f"on {now:%Y-%m-%d %H:%M} UTC")
    return fills


def rebalance_due(signal_day, done: set[str], halted: bool) -> str | None:
    """'initial' for the very first rebalance, 'rebalance' on a rebalance close not yet executed, else None."""
    if halted or signal_day.isoformat() in done:
        return None
    if not done:
        return "initial"
    return "rebalance" if T.is_rebalance_day(signal_day) else None
