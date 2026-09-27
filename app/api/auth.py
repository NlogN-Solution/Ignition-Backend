from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar

import jwt
from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import verify_token
from ..models import User, UserSession
from ..models.enums import STAFF_ROLES, UserRole, UserStatus
from .deps import get_db_session
from .exceptions import ForbiddenException, UnauthorizedException

security = HTTPBearer()

#: Attribute stamped on every dependency in this module that establishes who
#: the caller is. `tests/test_endpoint_authorization.py` walks the dependency
#: tree of every registered route and fails the build for any that carries
#: none — the standing version of Phase 2's "audit the endpoints guarded only
#: by `get_current_user`", which a one-off review would not keep true.
AUTH_MARKER = "__ignition_auth__"

F = TypeVar("F", bound=Callable[..., object])


def _marks_auth(func: F, level: str) -> F:
    setattr(func, AUTH_MARKER, level)
    return func


#: What a staff account holding a temporary password may still do: see who it
#: is, change the password, or leave.
_PASSWORD_CHANGE_ALLOWED_PATHS = frozenset(
    {"/api/v1/auth/me", "/api/v1/auth/change-password", "/api/v1/auth/logout"}
)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(security),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    token = credentials.credentials
    try:
        payload = verify_token(token)
    except jwt.PyJWTError:
        # ED360 lets PyJWTError escape, so an expired token surfaces as a 500.
        # It is a 401 — the client's cue to refresh.
        raise UnauthorizedException("Invalid or expired token") from None

    if payload.get("type") != "access":
        # Refresh tokens are longer-lived and must never be accepted as bearer
        # credentials.
        raise UnauthorizedException("Invalid access token")

    user_id = payload.get("sub")
    session_id = payload.get("sid")
    if not user_id or not session_id:
        raise UnauthorizedException("Invalid token payload")
    try:
        user_uuid = uuid.UUID(str(user_id))
        session_uuid = uuid.UUID(str(session_id))
    except ValueError:
        raise UnauthorizedException("Invalid token payload") from None

    # The session the token was issued with must still be live (FAPI-SEC-016).
    # Logout, password change, an admin reset and refresh-token reuse all
    # revoke sessions; checking here makes that take effect on the next request
    # instead of when the access token happens to expire. One indexed
    # primary-key lookup per request.
    live_session = await session.scalar(
        select(UserSession.id).where(
            UserSession.id == session_uuid,
            UserSession.user_id == user_uuid,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(UTC),
        )
    )
    if live_session is None:
        raise UnauthorizedException("Session has ended")

    result = await session.execute(select(User).where(User.id == user_uuid, User.deleted_at.is_(None)))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthorizedException("User not found")

    # A token outlives the change that suspended its owner, so status is checked
    # per request rather than only at login.
    if user.status is not UserStatus.ACTIVE:
        raise ForbiddenException("This account is not active")

    # `must_change_password` used to be enforced only by the admin console's
    # router, so a temporary password — or the published seed password on a
    # hosted database (scripts/SEED_NEON.md) — gave full API access to anyone
    # calling the API directly. For staff it is now a server-side gate. Student
    # temporary passwords are random, per-account and shown once, and the
    # student portal has no forced-change screen, so they are not gated here.
    if user.must_change_password and user.role in STAFF_ROLES and request.url.path not in _PASSWORD_CHANGE_ALLOWED_PATHS:
        raise ForbiddenException("Password change required")

    return user


_marks_auth(get_current_user, "authenticated")


async def get_current_session_id(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> uuid.UUID | None:
    """The `user_sessions` id behind the presented access token.

    Only meaningful alongside `get_current_user`, which has already verified
    the token and that the session is live; this just reads the claim so a
    handler can act on *this* session (keep it on password change, end it on
    logout).
    """
    try:
        return uuid.UUID(str(verify_token(credentials.credentials).get("sid")))
    except (jwt.PyJWTError, ValueError):
        return None


def require_role(*roles: UserRole | str) -> Callable[..., Awaitable[User]]:
    """Allow only the listed roles — plus super_admin, who is the owner.

    The super_admin bypass is inherited from ED360, where it was implicit and
    undocumented. Here it is deliberate: single-tenant super_admin *is* the
    account owner. Where that is too broad, use `require_owner`.
    """
    allowed = {role.value if isinstance(role, UserRole) else role for role in roles}

    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role.value != UserRole.SUPER_ADMIN.value and user.role.value not in allowed:
            raise ForbiddenException("Forbidden")
        return user

    return _marks_auth(dependency, f"roles:{','.join(sorted(allowed))}")


async def require_public() -> None:
    """Declares an endpoint deliberately reachable without a token.

    This grants nothing and checks nothing — it exists so that the public
    catalogue routes still carry a marker from this module, and
    `test_every_endpoint_declares_an_auth_dependency` keeps passing
    structurally rather than being bypassed with an exemption list.

    The second test, `test_public_endpoints_are_exactly_the_declared_set`, is
    the one that actually holds the line: every route using this must also be
    named in `PUBLIC_ENDPOINTS`, so a new unauthenticated endpoint cannot be
    added quietly.
    """
    return None


_marks_auth(require_public, "public")


async def require_owner(user: User = Depends(get_current_user)) -> User:
    """super_admin only, with no role-list bypass.

    Replaces ED360's `require_platform_admin` (strip rule R6) for the handful of
    operations that genuinely belong to the account owner alone.
    """
    if user.role is not UserRole.SUPER_ADMIN:
        raise ForbiddenException("Forbidden")
    return user


_marks_auth(require_owner, "owner")


async def require_staff(user: User = Depends(get_current_user)) -> User:
    """Any role except STUDENT.

    ED360 guards 44 endpoints with a bare `get_current_user`, which is safe
    there only because students are not users of that API. In Ignition they are
    — public self-signup creates real STUDENT rows against the same database —
    so every staff router carries this dependency and a student presenting a
    perfectly valid token still gets a 403.
    """
    if user.role not in STAFF_ROLES:
        raise ForbiddenException("Staff access required")
    return user


_marks_auth(require_staff, "staff")
