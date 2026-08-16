from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

import jwt
from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.security import verify_token
from ..models import User
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


async def get_current_user(
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
    if not user_id:
        raise UnauthorizedException("Invalid token payload")

    result = await session.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthorizedException("User not found")

    # A token outlives the change that suspended its owner, so status is checked
    # per request rather than only at login.
    if user.status is not UserStatus.ACTIVE:
        raise ForbiddenException("This account is not active")

    return user


_marks_auth(get_current_user, "authenticated")


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
