"""A read-through Redis cache in front of the public catalogue.

## Why this exists

`/public/*` serves 44 universities and 4,797 offerings out of Postgres, and
every response is the same for every caller: published-only rows, no user, no
token, no personalisation. The routes already say so in their headers —
`s-maxage=300, stale-while-revalidate=86400` — but a `Cache-Control` header is
an instruction to something in front of the API, and in development and on a
single Render instance there is nothing in front of the API. So every page view
paid full price.

Measured from this machine against the Neon instance in `us-east-2`, that price
is not small:

| Endpoint | Cold |
|---|---|
| `GET /public/universities/{slug}` | ~2.4s |
| `GET /public/courses?limit=24` | ~5–7s |
| `GET /public/courses/facets` | ~4.2s |

The queries themselves are not pathological — the course list is four of them,
not an N+1 — it is that four sequential round trips to another continent cost
about a second each before Postgres does any work. Nothing that can be fixed by
rewriting the SQL is the dominant term.

**And slow does not degrade gracefully here, it 404s.** `Ignition-Landing`'s
fetch wrapper gives the API 8 seconds and then returns `null`, and
`app/universities/[university]/page.tsx` reads `null` as "no such university"
and calls `notFound()`. Every university page on the public site was serving
"That page isn't here" — not because anything was missing, but because
`getUniversity` plus `getOfferingsAt` did not finish in time. Essex, with 264
offerings paged 100 at a time, took over three minutes uncached.

## What it does

`CachedRoute` is an `APIRoute` subclass, so the whole thing attaches in one
line — `APIRouter(..., route_class=CachedRoute)` — and no handler is touched or
has to remember to opt in. A `GET` that returns 200 has its body stored under
the request's path and sorted query string; the next identical request is
served from Redis without a database connection.

Three rules keep it honest:

* **Only `GET`, only 200.** A 404 for an unpublished slug is not cached: the
  moment staff publish it, the next request should find it.
* **Never cache an authenticated request.** Everything here is unauthenticated
  by design, but the preview flow reaches the same routes with a token, and a
  draft landing in a cache the whole internet reads is exactly the failure this
  file must not introduce.
* **Redis being down is a cache miss.** Every call is wrapped; a failed read
  falls through to the handler and a failed write is dropped. The catalogue
  must not stop working because the cache did.

## Freshness

`TTL_SECONDS` matches the `s-maxage` the routes already advertise, so the
backend cache and any CDN in front of it expire on the same schedule and a
corrected fee is live within five minutes at worst. Publishing does not wait
for that: `core.landing.revalidate` purges this cache in the same call that
expires the landing's tags, so pressing Publish is still immediate.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute

from .cache import get_client
from .config import get_settings

logger = logging.getLogger(__name__)

#: Bump to invalidate every entry at once — a payload shape change, say.
#: Cheaper and far less error-prone than reasoning about which keys a schema
#: edit invalidated.
_VERSION = "v1"

_PREFIX = f"public:{_VERSION}:"

#: Deliberately the same number as `_CACHE_CONTROL`'s `s-maxage` in
#: `routes/public.py`. If you change one, change the other.
TTL_SECONDS = 300

#: Long query strings (the course explorer sends eight filters) make unwieldy
#: keys, so anything past this is hashed. Short keys stay readable in
#: `redis-cli --scan`, which is worth keeping for the common case.
_MAX_READABLE_QUERY = 120


def cache_key(path: str, query: str) -> str:
    if len(query) > _MAX_READABLE_QUERY:
        query = "h:" + hashlib.sha256(query.encode()).hexdigest()[:32]
    return f"{_PREFIX}{path}?{query}" if query else f"{_PREFIX}{path}"


def _normalised_query(request: Request) -> str:
    """Query parameters, sorted, so `?a=1&b=2` and `?b=2&a=1` are one entry."""
    items = sorted(request.query_params.multi_items())
    return "&".join(f"{key}={value}" for key, value in items)


async def purge() -> int:
    """Drop every cached public response. Returns how many keys went.

    Called by `core.landing.revalidate`, which publishing already goes through.
    `scan_iter` rather than `keys`: this runs on the request path of a Publish
    click, and `KEYS` blocks the whole Redis instance while it walks.
    """
    try:
        client = get_client()
        removed = 0
        async for key in client.scan_iter(match=f"{_PREFIX}*", count=500):
            removed += await client.delete(key)
        return removed
    except Exception:
        logger.warning("Redis unavailable for public cache purge", exc_info=True)
        return 0


def _enabled() -> bool:
    """Off under `pytest`.

    The cache is a process-global Redis keyed on the path, and the test suite
    creates and destroys a university called `york-st-john` several times over.
    Left on, one test's response answers the next test's request, and a suite
    that passes or fails depending on what ran before it is worse than no cache
    at all. `ENVIRONMENT=test` is already how `redis_url` and `DATABASE_URL`
    keep the suite hermetic — this is the same rule.
    """
    return get_settings().ENVIRONMENT != "test"


class CachedRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            # A token means this might be a preview of unpublished copy. Do not
            # read from, and do not write to, a cache everyone shares.
            if not _enabled() or request.method != "GET" or request.headers.get("authorization"):
                return await original(request)

            key = cache_key(request.url.path, _normalised_query(request))

            try:
                hit = await get_client().get(key)
            except Exception:
                logger.warning("Redis unavailable for public cache read", exc_info=True)
                hit = None

            if hit is not None:
                entry = json.loads(hit)
                return Response(
                    content=entry["body"],
                    status_code=200,
                    media_type=entry["media_type"],
                    # `X-Cache` is the only way to tell a hit from a miss from
                    # outside, and "why is this stale" is the first question
                    # anyone asks of a cache.
                    headers={**entry["headers"], "X-Cache": "HIT"},
                )

            response = await original(request)
            if response.status_code != 200:
                return response

            body = getattr(response, "body", None)
            if body is None:
                # A streaming response has no `.body` to store. Nothing under
                # `/public` returns one today; this is here so that adding one
                # degrades to "uncached" instead of to an exception.
                return response

            entry = {
                "body": body.decode(),
                "media_type": response.media_type or "application/json",
                # Cache-Control travels with the entry so a hit still tells a
                # CDN what to do. Nothing else is carried: content-length is
                # recomputed, and no `/public` route sets a cookie.
                "headers": {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower() == "cache-control"
                },
            }

            try:
                await get_client().set(key, json.dumps(entry), ex=TTL_SECONDS)
            except Exception:
                logger.warning("Redis unavailable for public cache write", exc_info=True)

            response.headers["X-Cache"] = "MISS"
            return response

        return handler


class PurgingRoute(APIRoute):
    """Drops the public cache after any successful write on this router.

    Attached to the routers that own the data `/public/*` serves — the
    catalogue. Without it a counsellor could correct a university's tuition
    figure, reload the public page and see the old one for up to five minutes,
    with nothing to tell them which copy they were looking at.

    A route class rather than a call in each handler because there are fifteen
    of them across two files and the sixteenth would be the one that forgot.
    The CMS keeps its explicit `core.landing.revalidate`, which purges this
    cache *and* expires the landing's own tags; a catalogue write has no
    landing tags to expire, so it only needs this half.

    Coarse on purpose: it clears everything rather than working out which
    entries a program edit invalidated. A university's row appears in its own
    detail, the list, the facets, every course card that names it and every
    search page those appear on, so the honest answer to "what did that edit
    change" is "possibly all of it" — and the cost of being wrong is a stale
    price on a public page.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            if request.method != "GET" and response.status_code < 400 and _enabled():
                await purge()
            return response

        return handler
