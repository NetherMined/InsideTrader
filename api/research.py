"""Test Results page endpoints: finished research result sets and the live side-by-side test.

GET /api/v1/research               list of result sets (no rows)
GET /api/v1/research/comparison    trend strategy vs current bot, live
GET /api/v1/research/trend         trend bot detail: status, signals, holdings, rebalances, fills, funding
GET /api/v1/research/{key}         one result set with its rows
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.db import get_session

router = APIRouter(prefix="/api/v1/research", tags=["research"])

NAMES = {"trend": "Trend strategy", "current_bot": "Current bot"}
SUMMARY_KEY = "trend:summary"


class ResearchMeta(BaseModel):
    key: str
    title: str
    description: str | None = None
    phase: str | None = None
    updated_at: str | None = None
    row_count: int


class ResearchSet(ResearchMeta):
    rows: list[dict[str, Any]]


class StrategySummary(BaseModel):
    strategy: str
    equity_now: float
    return_pct: float
    max_drawdown_pct: float
    trades: int
    open_positions: int


class DailyPoint(BaseModel):
    day: str
    strategy: str
    equity: float
    return_pct: float
    drawdown_pct: float
    trades: int


class Holding(BaseModel):
    symbol: str
    qty: float
    entry_price: float
    price: float
    value_usd: float
    pnl_pct: float
    weight_pct: float
    stop_price: float
    opened: str


class Comparison(BaseModel):
    running: bool
    as_of: str | None
    started_at: str | None
    start_capital: float | None
    days: int
    halted: bool
    halted_reason: str | None
    summary: list[StrategySummary]
    daily: list[DailyPoint]
    holdings: list[Holding]


def build_comparison(start: float | None, started_at: str | None, snaps: list[tuple], live: dict | None,
                     trades_by_day: dict[str, dict[str, int]], today: str) -> dict:
    """Combine daily snapshots, the live summary and trade counts into the page's comparison view.

    snaps: (day, strategy_key, equity, open_positions) rows. The live value replaces today's snapshot.
    """
    if not start:
        return {"running": False, "as_of": None, "started_at": None, "start_capital": None, "days": 0,
                "halted": False, "halted_reason": None, "summary": [], "daily": [], "holdings": []}
    summary, daily = [], []
    for key, name in NAMES.items():
        rows = [(d, e, n) for d, s, e, n in snaps if s == key]
        if live:
            rows = [r for r in rows if r[0] != today] + [(today, live[key]["equity"], live[key]["open_positions"])]
        peak, worst = start, 0.0
        for day, equity, _ in rows:
            peak = max(peak, equity)
            dd = (equity / peak - 1) * 100
            worst = min(worst, dd)
            trades = sum(n for d, n in trades_by_day.get(key, {}).items() if d <= day)
            daily.append({"day": day, "strategy": name, "equity": equity, "return_pct": (equity / start - 1) * 100,
                          "drawdown_pct": dd, "trades": trades})
        if rows:
            equity, n_open = rows[-1][1], rows[-1][2]
            summary.append({"strategy": name, "equity_now": equity, "return_pct": (equity / start - 1) * 100,
                            "max_drawdown_pct": worst, "trades": sum(trades_by_day.get(key, {}).values()),
                            "open_positions": n_open})
    days = len({d for d, *_ in snaps} | ({today} if live else set()))
    return {"running": True, "as_of": live["as_of"] if live else None, "started_at": started_at,
            "start_capital": start, "days": days, "halted": bool(live and live.get("halted")),
            "halted_reason": live.get("halted_reason") if live else None,
            "summary": summary, "daily": daily, "holdings": live["holdings"] if live else []}


async def _redis() -> aioredis.Redis:
    return aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)


async def _table_exists(session: AsyncSession, name: str) -> bool:
    return bool((await session.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name})).scalar())


@router.get("", response_model=list[ResearchMeta])
async def list_research(session: Annotated[AsyncSession, Depends(get_session)]):
    if not await _table_exists(session, "research_results"):
        return []
    rows = (await session.execute(text(
        "SELECT key, title, description, phase, updated_at, json_array_length(rows::json) AS row_count "
        "FROM research_results ORDER BY key"))).mappings().all()
    return [dict(r) for r in rows]


@router.get("/comparison", response_model=Comparison)
async def comparison(session: Annotated[AsyncSession, Depends(get_session)]):
    if not await _table_exists(session, "trend_account"):
        return build_comparison(None, None, [], None, {}, "")
    acct = (await session.execute(text("SELECT start_capital, started_at FROM trend_account WHERE id = 1"))).first()
    if not acct:
        return build_comparison(None, None, [], None, {}, "")
    snaps = [tuple(r) for r in (await session.execute(text(
        "SELECT day, strategy, equity, open_positions FROM strategy_equity ORDER BY day"))).all()]
    trend = (await session.execute(text("SELECT time::date::text, COUNT(*) FROM trend_fills GROUP BY 1"))).all()
    bot = (await session.execute(text(
        "SELECT closed_at::date::text, COUNT(*) FROM trades WHERE status = 'CLOSED' AND opened_at >= :s GROUP BY 1"),
        {"s": acct.started_at})).all()
    today = (await session.execute(text("SELECT (now() AT TIME ZONE 'UTC')::date::text"))).scalar()
    redis = await _redis()
    try:
        raw = await redis.get(SUMMARY_KEY)
    finally:
        await redis.aclose()
    live = json.loads(raw) if raw else None
    return build_comparison(acct.start_capital, acct.started_at.isoformat(), snaps, live,
                            {"trend": dict(trend), "current_bot": dict(bot)}, today)


# Trend bot detail. Rebalance calendar and risk limits mirror bot/trend/strategy.py (the API image does not include bot/).
TREND_EPOCH = date(2021, 1, 1)
TREND_HOLD_DAYS = 5
TREND_KILL_DRAWDOWN = 0.35
TREND_MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
                "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT")


class TrendStatus(BaseModel):
    state: str
    halted_reason: str | None
    started_at: str | None
    last_cycle: str | None
    start_capital: float
    equity: float
    cash: float
    invested: float
    invested_pct: float
    return_pct: float
    peak_equity: float
    drawdown_pct: float
    kill_drawdown_pct: float
    kill_equity: float
    funding_paid: float


class NextRebalance(BaseModel):
    signal_day: str
    runs_at: str


class TrendTarget(BaseModel):
    symbol: str
    signal: str
    target_pct: float
    held_pct: float


class TrendRebalanceRow(BaseModel):
    signal_day: str
    executed_at: str
    equity_before: float
    orders: int


class TrendFillRow(BaseModel):
    time: str
    symbol: str
    side: str
    qty: float
    fill_price: float
    notional: float
    fee_usdt: float
    reason: str


class FundingBySymbol(BaseModel):
    symbol: str
    payments: int
    amount_usdt: float


class FundingRow(BaseModel):
    time: str
    symbol: str
    rate: float
    amount_usdt: float


class TrendDetail(BaseModel):
    running: bool
    status: TrendStatus | None
    next_rebalance: NextRebalance | None
    last_signal_day: str | None
    targets: list[TrendTarget]
    holdings: list[Holding]
    rebalances: list[TrendRebalanceRow]
    fills: list[TrendFillRow]
    funding_by_symbol: list[FundingBySymbol]
    funding_recent: list[FundingRow]


def next_trend_rebalance(now: datetime) -> dict:
    """Next rebalance close (UTC date) whose run, 00:05 UTC the following day, is still ahead of `now`."""
    today = now.astimezone(timezone.utc).date()
    for k in range(-1, TREND_HOLD_DAYS + 1):
        day = today + timedelta(days=k)
        runs_at = datetime.combine(day + timedelta(days=1), time(0, 5), tzinfo=timezone.utc)
        if (day - TREND_EPOCH).days % TREND_HOLD_DAYS == 0 and runs_at > now:
            return {"signal_day": day.isoformat(), "runs_at": runs_at.isoformat()}
    raise RuntimeError("no rebalance found within one cycle")


def build_trend(acct: dict | None, live: dict | None, last_rebalance: dict | None, rebalances: list[dict],
                fills: list[dict], funding: list[dict], last_cycle: str | None, now: datetime) -> dict:
    """Trend bot detail view from its account row, live summary, rebalance/fill/funding rows."""
    if not acct:
        return {"running": False, "status": None, "next_rebalance": None, "last_signal_day": None, "targets": [],
                "holdings": [], "rebalances": [], "fills": [], "funding_by_symbol": [], "funding_recent": []}
    holdings = live["holdings"] if live else []
    invested = sum(h["value_usd"] for h in holdings)
    equity = live["trend"]["equity"] if live else acct["cash"] + invested
    peak = max(acct["peak_equity"], equity)
    by_symbol: dict[str, dict] = {}
    for f in funding:
        s = by_symbol.setdefault(f["symbol"], {"symbol": f["symbol"], "payments": 0, "amount_usdt": 0.0})
        s["payments"] += 1
        s["amount_usdt"] += f["amount_usdt"]
    held = {h["symbol"]: h["weight_pct"] for h in holdings}
    weights = (last_rebalance or {}).get("targets") or {}
    targets = [{"symbol": s, "signal": "long" if weights.get(s, 0) > 0 else "flat",
                "target_pct": weights.get(s, 0.0) * 100, "held_pct": held.get(s, 0.0)} for s in TREND_MAJORS]
    status = {
        "state": "halted" if acct["halted"] else "running", "halted_reason": acct["halted_reason"],
        "started_at": acct["started_at"], "last_cycle": last_cycle, "start_capital": acct["start_capital"],
        "equity": equity, "cash": acct["cash"], "invested": invested,
        "invested_pct": invested / equity * 100 if equity else 0.0,
        "return_pct": (equity / acct["start_capital"] - 1) * 100 if acct["start_capital"] else 0.0,
        "peak_equity": peak, "drawdown_pct": (equity / peak - 1) * 100 if peak else 0.0,
        "kill_drawdown_pct": -TREND_KILL_DRAWDOWN * 100, "kill_equity": peak * (1 - TREND_KILL_DRAWDOWN),
        "funding_paid": sum(f["amount_usdt"] for f in funding),
    }
    return {"running": True, "status": status, "next_rebalance": next_trend_rebalance(now),
            "last_signal_day": (last_rebalance or {}).get("signal_day"), "targets": targets, "holdings": holdings,
            "rebalances": rebalances,
            "fills": [{**f, "notional": f["qty"] * f["fill_price"]} for f in fills],
            "funding_by_symbol": sorted(by_symbol.values(), key=lambda s: s["amount_usdt"], reverse=True),
            "funding_recent": sorted(funding, key=lambda f: f["time"], reverse=True)[:50]}


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else v


@router.get("/trend", response_model=TrendDetail)
async def trend_detail(session: Annotated[AsyncSession, Depends(get_session)]):
    now = datetime.now(timezone.utc)
    if not await _table_exists(session, "trend_account"):
        return build_trend(None, None, None, [], [], [], None, now)
    acct = (await session.execute(text(
        "SELECT start_capital, cash, peak_equity, halted, halted_reason, started_at FROM trend_account WHERE id = 1"
    ))).mappings().first()
    if not acct:
        return build_trend(None, None, None, [], [], [], None, now)
    acct = {**acct, "started_at": _iso(acct["started_at"])}
    rebal = [{**r, "executed_at": _iso(r["executed_at"])} for r in (await session.execute(text(
        "SELECT signal_day, executed_at, equity_before, orders, targets FROM trend_rebalances ORDER BY signal_day DESC"
    ))).mappings().all()]
    fills = [{**r, "time": _iso(r["time"])} for r in (await session.execute(text(
        "SELECT time, symbol, side, qty, fill_price, fee_usdt, reason FROM trend_fills ORDER BY time DESC, id DESC LIMIT 500"
    ))).mappings().all()]
    funding = [{**r, "time": _iso(r["time"])} for r in (await session.execute(text(
        "SELECT time, symbol, rate, amount_usdt FROM trend_funding ORDER BY time"))).mappings().all()]
    redis = await _redis()
    try:
        raw, last_cycle = await redis.get(SUMMARY_KEY), await redis.get("trend:last_cycle")
    finally:
        await redis.aclose()
    last = rebal[0] if rebal else None
    if last and isinstance(last["targets"], str):
        last = {**last, "targets": json.loads(last["targets"])}
    rows = [{k: r[k] for k in ("signal_day", "executed_at", "equity_before", "orders")} for r in rebal]
    return build_trend(dict(acct), json.loads(raw) if raw else None, last, rows, fills, funding, last_cycle, now)


@router.get("/{key}", response_model=ResearchSet)
async def get_research(key: str, session: Annotated[AsyncSession, Depends(get_session)]):
    if not await _table_exists(session, "research_results"):
        raise HTTPException(404, "No research results have been published yet")
    row = (await session.execute(text(
        "SELECT key, title, description, phase, updated_at, rows FROM research_results WHERE key = :k"),
        {"k": key})).mappings().first()
    if not row:
        raise HTTPException(404, f"No result set named '{key}'")
    rows = row["rows"] if isinstance(row["rows"], list) else json.loads(row["rows"])
    return {**dict(row), "rows": rows, "row_count": len(rows)}
