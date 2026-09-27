"""Bounded pagination, for every list route.

Only `/public/*` used to clamp its paging parameters. Everywhere else `page`
and `limit` were bare ints handed straight to `.offset()`/`.limit()`, so
`?limit=1000000` was honoured and `?page=0` produced a negative OFFSET that
Postgres rejects — a 500 per request (FAPI-SEC-007).

The ceiling is 200, not the public router's 100, because the consoles already
ask for 200 in places (the dashboard's lead-source and revenue widgets, the
student portal's catalogue pickers) and a stricter bound would break them
with a 422. `tests/test_endpoint_authorization.py`-style enforcement lives in
`tests/test_pagination_bounds.py`: a list route declaring a bare `int` for
`page` or `limit` fails the build.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

#: Largest page size any list route will serve.
MAX_PAGE_SIZE = 200
#: Deepest page any list route will serve. 10,000 × 200 is two million rows,
#: past which OFFSET paging is the wrong tool anyway — and it keeps the
#: computed offset comfortably inside a Postgres BIGINT.
MAX_PAGE = 10_000

PageParam = Annotated[int, Query(ge=1, le=MAX_PAGE)]
LimitParam = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


def clamp_page(page: int, limit: int) -> tuple[int, int]:
    """Defence in depth for services called from somewhere other than a route."""
    return max(1, min(page, MAX_PAGE)), max(1, min(limit, MAX_PAGE_SIZE))
