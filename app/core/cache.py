"""A small Redis-backed cache, scoped to `/me/dashboard`.

The dashboard aggregates progress, points, applications, appointments,
documents, notifications, and activity into one payload — cheap individually,
expensive as a bundle on every page load. Redis is already provisioned for
rate limiting (`core/rate_limit.py`); this reuses the same instance rather
than adding a second cache backend for one endpoint.

Failure is soft everywhere: a Redis outage should degrade the dashboard to
"always computed fresh", not 500 it. Callers therefore never see a Redis
exception — `get_json` returns `None` (a cache miss) and `set_json` /
`invalidate` are no-ops on failure.
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


def _get_client() -> Redis:
    global _client
    if _client is None:
        settings = get_settings()
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def dashboard_cache_key(student_id: UUID) -> str:
    return f"dashboard:{student_id}"


async def get_json(key: str) -> Any | None:
    try:
        raw = await _get_client().get(key)
    except Exception:
        logger.warning("Redis unavailable for cache read; serving uncached", exc_info=True)
        return None
    return json.loads(raw) if raw is not None else None


async def set_json(key: str, value: Any, ttl_seconds: int = DASHBOARD_CACHE_TTL_SECONDS) -> None:
    try:
        await _get_client().set(key, json.dumps(value), ex=ttl_seconds)
    except Exception:
        logger.warning("Redis unavailable for cache write; continuing uncached", exc_info=True)


async def invalidate_dashboard_cache(student_id: UUID) -> None:
    try:
        await _get_client().delete(dashboard_cache_key(student_id))
    except Exception:
        logger.warning("Redis unavailable for cache invalidation", exc_info=True)
