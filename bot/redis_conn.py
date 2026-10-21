"""Shared Redis client factory for the bot processes.

redis-py's defaults are a 5s socket timeout with no retries, so one slow read during model retraining
aborted a whole trading-engine pass. Retry timeouts and dropped connections with backoff instead.
"""

import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError, TimeoutError


def make_redis(url: str) -> aioredis.Redis:
    return aioredis.from_url(
        url,
        encoding="utf-8",
        decode_responses=True,
        socket_timeout=10,
        socket_connect_timeout=10,
        retry=Retry(ExponentialBackoff(cap=2, base=0.2), retries=3),
        retry_on_error=[ConnectionError, TimeoutError],
        health_check_interval=30,
    )
