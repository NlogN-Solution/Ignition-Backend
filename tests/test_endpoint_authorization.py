"""Architectural guard: every endpoint states who may call it.

Phase 2 of IGNITION_PLATFORM_PLAN.md calls for auditing the 44 ED360 endpoints
guarded only by a bare `get_current_user`. An audit performed once is true once;
this performs it on every run, over whatever routes exist at the time.

Two properties are enforced:

1. Every route carries one of the dependencies from `app.api.auth` — no
   endpoint is reachable without the server having decided who the caller is.
2. The set of routes reachable *without authentication* is exactly the list
   below. Adding a public endpoint is fine; adding one silently is not.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.api.auth import AUTH_MARKER
from app.main import app

#: Endpoints that must work before a caller has a token. Anything reaching this
#: list is a deliberate decision: it is the entire unauthenticated attack
#: surface of the API.
PUBLIC_ENDPOINTS: set[tuple[str, str]] = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/health/ready"),
    # Credential endpoints: rate-limited in app/core/rate_limit.py.
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/refresh"),
}

#: FastAPI's own docs routes, which are disabled in production by `main.py`.
_DOCS_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _iter_api_routes(routes) -> list[object]:
    """Flatten the route tree into objects exposing `.path`, `.methods`, `.dependant`.

    This FastAPI keeps `include_router` results in an `_IncludedRouter` holding
    the sub-router rather than splicing routes into `app.routes`, and only its
    `effective_route_contexts()` knows the prefixed path. A shallow scan finds
    four docs routes and passes vacuously, which is what
    `test_the_route_tree_is_actually_being_walked` exists to catch.
    """
    found: list[object] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            found.extend(contexts())
        nested = getattr(route, "routes", None)
        if nested:
            found.extend(_iter_api_routes(nested))
    return found


def _auth_levels(dependant) -> set[str]:
    """Every auth marker anywhere in a route's dependency tree."""
    levels: set[str] = set()
    call = getattr(dependant, "call", None)
    marker = getattr(call, AUTH_MARKER, None)
    if marker is not None:
        levels.add(marker)
    for sub in dependant.dependencies:
        levels |= _auth_levels(sub)
    return levels


def _endpoints() -> list[tuple[str, str, set[str]]]:
    endpoints = []
    for route in _iter_api_routes(app.routes):
        if route.path in _DOCS_PATHS:
            continue
        for method in sorted(route.methods or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            endpoints.append((method, route.path, _auth_levels(route.dependant)))
    return endpoints


def test_the_route_tree_is_actually_being_walked() -> None:
    """Guards the guard: a traversal bug would make everything below vacuous."""
    endpoints = _endpoints()
    assert len(endpoints) > 40, f"only found {len(endpoints)} routes — the traversal is missing nested routers"
    assert ("GET", "/api/v1/users") in [(m, p) for m, p, _ in endpoints]


def test_every_endpoint_declares_an_auth_dependency() -> None:
    unguarded = [
        f"{method} {path}"
        for method, path, levels in _endpoints()
        if not levels and (method, path) not in PUBLIC_ENDPOINTS
    ]
    assert not unguarded, (
        "These endpoints have no dependency from app.api.auth, so anyone can reach them:\n  "
        + "\n  ".join(sorted(unguarded))
    )


def test_the_public_surface_is_exactly_what_we_intend() -> None:
    actual_public = {(method, path) for method, path, levels in _endpoints() if not levels}

    newly_public = actual_public - PUBLIC_ENDPOINTS
    assert not newly_public, (
        "New unauthenticated endpoints appeared. If deliberate, add them to "
        "PUBLIC_ENDPOINTS with a reason:\n  " + "\n  ".join(f"{m} {p}" for m, p in sorted(newly_public))
    )

    no_longer_public = PUBLIC_ENDPOINTS - actual_public
    assert not no_longer_public, (
        "PUBLIC_ENDPOINTS lists routes that are gone or now guarded — prune it so it "
        "keeps describing reality:\n  " + "\n  ".join(f"{m} {p}" for m, p in sorted(no_longer_public))
    )


def test_staff_only_routers_never_admit_students() -> None:
    """Routers with no student-facing purpose must not be reachable by STUDENT.

    Checked structurally rather than by role name: a route qualifies if it
    requires staff, or lists roles that exclude `student`.
    """
    staff_only_prefixes = ("/api/v1/tasks", "/api/v1/departments", "/api/v1/employees")

    offenders = []
    for method, path, levels in _endpoints():
        if not path.startswith(staff_only_prefixes):
            continue
        # `require_staff` and `require_role` both depend on `get_current_user`,
        # so "authenticated" appears alongside them; it is the *absence* of a
        # stronger marker that means "any logged-in user, students included".
        restrictive = levels - {"authenticated"}
        admits_student = not restrictive or any("student" in level for level in restrictive)
        if admits_student:
            offenders.append(f"{method} {path} -> {sorted(levels)}")

    assert not offenders, "Staff-only routes reachable by a student:\n  " + "\n  ".join(sorted(offenders))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/users"),
        ("GET", "/api/v1/tasks"),
        ("GET", "/api/v1/departments"),
        ("GET", "/api/v1/employees"),
        ("GET", "/api/v1/documents/folders"),
    ],
)
@pytest.mark.asyncio
async def test_staff_endpoints_reject_an_unauthenticated_caller(client, method: str, path: str) -> None:
    """The static checks above prove a dependency is declared; this proves it
    actually runs."""
    response = await client.request(method, path)
    assert response.status_code == 401
