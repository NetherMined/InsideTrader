"""Test Results page endpoints: finished research result sets and the live side-by-side test.

GET /api/v1/research               list of result sets (no rows)
GET /api/v1/research/comparison    trend strategy vs current bot, live
GET /api/v1/research/{key}         one result set with its rows
"""
from __future__ import annotations

import json
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
