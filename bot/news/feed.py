"""Crypto news connector.

Polls free public feeds (CryptoCompare and Binance announcements), scores
each headline, and writes a short-lived bias per symbol. The executor uses
that bias as a news-trading filter: a fresh contradiction blocks the trade,
agreement gives a small confidence boost.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

import aiohttp
import redis.asyncio as aioredis
from loguru import logger

from bot.config import settings

_CRYPTOCOMPARE = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"
_BINANCE = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
_FEED_KEY = "news:feed"
_BIAS_PREFIX = "news:bias:"
_MAX_ITEMS = 30

_BULL = (
    "approval", "approved", "etf", "partnership", "listing", "lists", "upgrade",
    "adoption", "surge", "breakout", "buyback", "inflow", "record", "ath",
    "launch", "integration", "bullish", "rallies", "rally", "gains",
)
_BEAR = (
    "hack", "hacked", "exploit", "ban", "banned", "lawsuit", "sued", "sec",
    "crash", "delist", "halt", "liquidation", "bankruptcy", "hackers",
    "outage", "investigation", "bearish", "plunge", "plunges", "dump",
)


def _score(text: str) -> int:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return sum(1 for w in _BULL if w in words) - sum(1 for w in _BEAR if w in words)


def _symbols_in(text: str, known: set[str]) -> list[str]:
    upper = text.upper()
    hits = []
    for base in known:
        if re.search(rf"\b{re.escape(base)}\b", upper):
            hits.append(f"{base}/USDT")
    return hits


async def _get(session: aiohttp.ClientSession, url: str, **kwargs) -> dict | list | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8), **kwargs) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as exc:
        logger.debug(f"News fetch failed {url}: {exc}")
        return None


async def fetch_news(symbols: list[str]) -> list[dict]:
    known = {s.split("/")[0].upper() for s in symbols}
    items: list[dict] = []
    async with aiohttp.ClientSession() as session:
        cc = await _get(session, _CRYPTOCOMPARE)
        if isinstance(cc, dict):
            for row in (cc.get("Data") or [])[:25]:
                title = str(row.get("title") or "")
                body = str(row.get("body") or "")
                text = f"{title} {body}"
                published = int(row.get("published_on") or 0)
                items.append({
                    "source": str(row.get("source") or "CryptoCompare"),
                    "title": title,
                    "url": str(row.get("url") or ""),
                    "published": published,
                    "symbols": _symbols_in(text, known) or _symbols_in(str(row.get("categories") or ""), known),
                    "score": _score(text),
                })
        bn = await _get(session, _BINANCE, params={"catalogId": 48, "pageNo": 1, "pageSize": 20})
        articles = (((bn or {}).get("data") or {}).get("articles")) if isinstance(bn, dict) else None
        for row in articles or []:
            title = str(row.get("title") or "")
            published = int((row.get("releaseDate") or 0) / 1000)
            items.append({
                "source": "Binance",
                "title": title,
                "url": f"https://www.binance.com/en/support/announcement/{row.get('code', '')}",
                "published": published,
                "symbols": _symbols_in(title, known),
                "score": _score(title),
            })
    items.sort(key=lambda i: i["published"], reverse=True)
    return items[:_MAX_ITEMS]


def _bias_for(items: list[dict], now: float, max_age_s: int) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in items:
        age = now - int(item.get("published") or 0)
        if age < 0 or age > max_age_s or not item["symbols"] or item["score"] == 0:
            continue
        for symbol in item["symbols"]:
            current = out.get(symbol)
            if current is None or abs(item["score"]) > abs(current["score"]):
                out[symbol] = {
                    "side": "BUY" if item["score"] > 0 else "SELL",
                    "score": item["score"],
                    "title": item["title"],
                    "source": item["source"],
                    "age_s": int(age),
                }
    return out


async def run_news_loop(symbols: list[str], redis: aioredis.Redis, stop_event: asyncio.Event) -> None:
    logger.info(f"News connector started for {len(symbols)} symbols")
    while not stop_event.is_set():
        if not settings.news_enabled:
            await asyncio.sleep(settings.news_poll_seconds)
            continue
        try:
            items = await fetch_news(symbols)
            now = datetime.now(timezone.utc).timestamp()
            await redis.setex(_FEED_KEY, settings.news_poll_seconds * 3, json.dumps(items))
            bias = _bias_for(items, now, settings.news_max_age_minutes * 60)
            pipe = redis.pipeline()
            for symbol, payload in bias.items():
                pipe.setex(_BIAS_PREFIX + symbol, settings.news_max_age_minutes * 60, json.dumps(payload))
            if bias:
                await pipe.execute()
            logger.info(f"News refresh: {len(items)} headlines, {len(bias)} symbol biases")
        except Exception as exc:
            logger.warning(f"News refresh failed: {exc}")
        await asyncio.sleep(settings.news_poll_seconds)


async def get_news_bias(redis: aioredis.Redis, symbol: str) -> dict | None:
    raw = await redis.get(_BIAS_PREFIX + symbol)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None
