from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, cast

import jwt
from fastapi import Depends
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..api.deps import get_db_session
from ..api.exceptions import ConflictException, ForbiddenException
from ..core.config import get_settings
from ..core.events import StudentCreated, event_bus
from ..core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
    verify_token,
)
from ..models import User, UserSession
from ..models.enums import UserRole, UserStatus
from ..schemas.auth import PublicRegisterRequest

settings = get_settings()


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Hash to compare against when no user matches the submitted email.

    A missing account then costs the same bcrypt round as a wrong password;
    without it, login latency tells an unauthenticated caller which addresses
    are registered. Computed on first use rather than at import so a bcrypt
    round is not charged to every `import app.main` (including Alembic's).
    """
    return hash_password("bcrypt-timing-equaliser")


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Authentication ────────────────────────────────────────────────────────

    async def authenticate(
        self,
        email: str,
        password: str,
        ip_address: str | None = None,
    ) -> User | None:
        """Verify credentials, enforcing lockout.

        Returns `None` for bad credentials. Raises `ForbiddenException` when the
        account is locked, because "wrong password" and "locked out" need to be
        distinguishable by the user who owns the account.
        """
        result = await self.session.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))
        user = result.scalar_one_or_none()

        if user is None:
            verify_password(password, _dummy_hash())
            return None

        now = datetime.now(UTC)
        if user.locked_until is not None and user.locked_until > now:
            raise ForbiddenException("Account temporarily locked due to failed login attempts")

        if not user.password_hash or not verify_password(password, user.password_hash):
            await self._register_failed_attempt(user, now)
            return None

        # Checked after the password, so this never reveals whether a suspended
        # address exists to someone who cannot authenticate as it.
        if user.status is not UserStatus.ACTIVE:
            raise ForbiddenException("This account is not active")

        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        user.last_login_ip = ip_address
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def _register_failed_attempt(self, user: User, now: datetime) -> None:
        # A lapsed lock leaves its counter behind; restart the count rather than
        # letting one stale attempt re-lock the account on the next mistake.
        if user.locked_until is not None and user.locked_until <= now:
            user.failed_login_attempts = 0
            user.locked_until = None

        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=settings.ACCOUNT_LOCKOUT_MINUTES)
        await self.session.commit()

    async def register_user(self, payload: PublicRegisterRequest) -> User:
        """Public self-signup. Always creates an ACTIVE STUDENT.

        `role` and `status` are not parameters and are not read from the
        payload — see `PublicRegisterRequest`.
        """
        existing = await self.session.execute(select(User.id).where(User.email == payload.email))
        if existing.scalar_one_or_none() is not None:
            raise ConflictException("An account with this email already exists")

        user = User(
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=payload.email,
            password_hash=hash_password(payload.password),
            role=UserRole.STUDENT,
            status=UserStatus.ACTIVE,
            phone=payload.phone,
            date_of_birth=payload.date_of_birth,
            gender=payload.gender.value if payload.gender else None,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        await event_bus.publish(StudentCreated(student_id=user.id, email=user.email), self.session)
        return user

    async def change_password(self, user: User, current_password: str, new_password: str) -> bool:
        if not user.password_hash or not verify_password(current_password, user.password_hash):
            return False
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        # Changing a password is how someone responds to a suspected
        # compromise, so it has to invalidate sessions the attacker may hold.
        await self.revoke_all_sessions(user.id)
        await self.session.commit()
        await self.session.refresh(user)
        return True

    async def get_user_by_id(self, user_id: str | uuid.UUID) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
        return result.scalar_one_or_none()

    # ── Sessions ──────────────────────────────────────────────────────────────

    async def issue_tokens(
        self,
        user: User,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, str]:
        """Mint an access/refresh pair and record the refresh token.

        ED360's `create_tokens` is synchronous and persists nothing, which is
        why its refresh tokens cannot be revoked. Every refresh token minted
        here has a `user_sessions` row, and `refresh_tokens` rejects any token
        without a live one.
        """
        access_token = create_access_token(
            str(user.id),
            role=user.role.value,
        )
        refresh_token, token_hash, expires_at = create_refresh_token(str(user.id))

        self.session.add(
            UserSession(
                user_id=user.id,
                refresh_token_hash=token_hash,
                ip_address=ip_address,
                user_agent=user_agent,
                expires_at=expires_at,
            )
        )
        await self.session.commit()

        return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}

    async def refresh_tokens(
        self,
        refresh_token: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, str] | None:
        """Exchange a refresh token for a new pair, rotating the session.

        A valid signature is necessary but not sufficient: the token must also
        match a session row that is neither revoked nor expired. That row is
        revoked before the replacement is issued, so each refresh token is
        single-use and a replayed one is dead.
        """
        try:
            payload = verify_token(refresh_token)
        except jwt.PyJWTError:
            return None
        if payload.get("type") != "refresh":
            return None
        user_id = payload.get("sub")
        if not user_id:
            return None

        now = datetime.now(UTC)
        token_hash = hash_refresh_token(refresh_token)
        session_row = await self.session.scalar(
            select(UserSession).where(
                UserSession.refresh_token_hash == token_hash,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
        )
        if session_row is None:
            return None

        user = await self.get_user_by_id(user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            return None

        session_row.revoked_at = now
        await self.session.flush()

        return await self.issue_tokens(user, ip_address=ip_address, user_agent=user_agent)

    async def _revoke_where(self, *conditions: ColumnElement[bool]) -> int:
        """Mark every matching live session revoked; returns how many.

        `session.execute` is typed as returning `Result`, which has no
        `rowcount` — only the `CursorResult` a DML statement actually produces
        does. The cast is the narrowing, not a silenced error.
        """
        result = cast(
            "CursorResult[Any]",
            await self.session.execute(
                update(UserSession)
                .where(UserSession.revoked_at.is_(None), *conditions)
                .values(revoked_at=datetime.now(UTC))
            ),
        )
        return result.rowcount

    async def revoke_session(self, refresh_token: str) -> bool:
        """Revoke one session. Returns whether a live session matched."""
        revoked = await self._revoke_where(UserSession.refresh_token_hash == hash_refresh_token(refresh_token))
        await self.session.commit()
        return bool(revoked)

    async def revoke_all_sessions(self, user_id: uuid.UUID) -> int:
        """Revoke every live session for a user. Returns how many were closed.

        Does not commit — callers fold this into their own transaction so the
        password change and the revocation land together.
        """
        return await self._revoke_where(UserSession.user_id == user_id)


async def get_auth_service(session: AsyncSession = Depends(get_db_session)) -> AuthService:
    return AuthService(session)
