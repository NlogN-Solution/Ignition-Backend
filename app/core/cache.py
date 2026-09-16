"""A small Redis-backed cache.

Two things use it. `/me/dashboard` aggregates progress, points, applications,
appointments, documents, notifications and activity into one payload — cheap
individually, expensive as a bundle on every page load — and the public
catalogue caches whole responses through `core/public_cache.py`. Redis is
already provisioned for rate limiting (`core/rate_limit.py`); both reuse that
instance rather than adding a second cache backend.

Failure is soft everywhere: a Redis outage should degrade a caller to
"always computed fresh", not 500 it. Callers of the helpers therefore never
see a Redis exception — `get_json` returns `None` (a cache miss) and `set_json`
/ `invalidate` are no-ops on failure. `get_client` is the exception and hands
back the raw client, so its callers do their own wrapping.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from .config import get_settings

logger = logging.getLogger(__name__)

#: How stale a dashboard is allowed to be between the events that invalidate
#: it early. Short, because it is only a backstop — the event subscribers are
#: what keep it fresh in the common case.
DASHBOARD_CACHE_TTL_SECONDS = 60

_client: Redis | None = None


def get_client() -> Redis:
    """The shared client. Public because `core/public_cache.py` needs `scan_iter`
    and `delete`, which the three helpers below do not cover.

    Callers are responsible for their own error handling — see the module
    docstring: nothing in this file lets a Redis outage reach a request.
    """
    global _client
    if _client is None:
        settings = get_settings()
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def dashboard_cache_key(student_id: UUID) -> str:
    return f"dashboard:{student_id}"


async def get_json(key: str) -> Any | None:
    try:
        raw = await get_client().get(key)
    except Exception:
        logger.warning("Redis unavailable for cache read; serving uncached", exc_info=True)
        return None
    return json.loads(raw) if raw is not None else None


async def set_json(key: str, value: Any, ttl_seconds: int = DASHBOARD_CACHE_TTL_SECONDS) -> None:
    try:
        await get_client().set(key, json.dumps(value), ex=ttl_seconds)
    except Exception:
        logger.warning("Redis unavailable for cache write; continuing uncached", exc_info=True)


async def invalidate_dashboard_cache(student_id: UUID) -> None:
    try:
        await get_client().delete(dashboard_cache_key(student_id))
    except Exception:
        logger.warning("Redis unavailable for cache invalidation", exc_info=True)
