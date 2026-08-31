"""Talking to the public site: cache invalidation and preview links.

The landing renders `content_pages` and the catalogue statically and revalidates
on a timer (CATALOGUE-CMS-PLAN.md §10.3). A timer alone is not good enough for
an editor who has just pressed Publish and wants to see the page, so publishing
also calls a webhook that expires the relevant tags immediately.

Two deliberate properties:

* **Failure is not an error.** A revalidate that times out, 500s, or has nowhere
  to go leaves the row published and the timer to catch up. The alternative —
  failing the publish because the frontend's cache is unreachable — would make
  the CMS unusable whenever the public site is being redeployed.
* **Nothing is called at all when `LANDING_REVALIDATE_SECRET` is unset**, which
  is the default and the state of every developer machine. No secret means no
  trust relationship, and an unauthenticated purge endpoint is not one worth
  inventing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from .config import get_settings

logger = logging.getLogger(__name__)

#: Tag names are a contract with the landing's own `fetch(..., { next: { tags } })`
#: calls. Keep them coarse: an editor pressing Publish expects the whole site to
#: agree, not one route.
TAG_CONTENT = "content"
TAG_CATALOGUE = "catalogue"


async def revalidate(*tags: str) -> bool:
    """Ask the landing to expire cache tags. Returns whether it was told."""
    settings = get_settings()
    if not settings.LANDING_REVALIDATE_SECRET or not settings.LANDING_BASE_URL:
        return False

    url = f"{settings.LANDING_BASE_URL.rstrip('/')}/api/revalidate"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                url,
                json={"tags": list(tags)},
                headers={"x-revalidate-secret": settings.LANDING_REVALIDATE_SECRET},
            )
        response.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # Deliberately swallowed — see the module docstring.
        logger.warning("Landing revalidation failed for %s: %s", ", ".join(tags), exc)
        return False
    return True


def preview_token(page_key: str) -> str:
    """A short-lived token naming one page, signed with the shared secret.

    Scoped to a single key so a link forwarded to someone else unlocks that one
    draft rather than every unpublished page on the site.
    """
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.PREVIEW_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {"key": page_key, "exp": expire, "type": "preview"}
    return jwt.encode(payload, settings.LANDING_REVALIDATE_SECRET, algorithm=settings.JWT_ALGORITHM)


def preview_url(page_key: str, slug: str | None) -> str | None:
    """The link an editor follows to see a draft, or None if unconfigured."""
    settings = get_settings()
    if not settings.LANDING_REVALIDATE_SECRET or not settings.LANDING_BASE_URL:
        return None
    query = urlencode({"token": preview_token(page_key), "key": page_key, "slug": slug or ""})
    return f"{settings.LANDING_BASE_URL.rstrip('/')}/api/preview?{query}"
