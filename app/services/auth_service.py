from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, cast

import jwt
from fastapi import Depends
from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..api.deps import get_db_session
from ..api.exceptions import ConflictException, ForbiddenException
from ..core.config import get_settings
from ..core.events import StudentCreated, event_bus
from ..core.login_throttle import REAUTH_SCOPE, login_throttle
from ..core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
    verify_token,
)
from ..models import User, UserSession
from ..models.enums import NotificationType, UserRole, UserStatus
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
        """Verify credentials, enforcing both tiers of the lockout.

        Returns `None` for *every* refusal an unauthenticated caller could use
        to learn something — unknown address, wrong password, this address
        throttled from this IP, account locked — and pays one bcrypt round in
        each case, so neither the status, the body nor the latency tells them
        which (FAPI-SEC-011). The lock used to answer 403 "Account temporarily
        locked", which confirmed the address was registered and undid the
        timing equaliser below. The owner is told about a lock in-app instead.

        Raises only for a *correct* password on an inactive account, which
        reveals nothing to someone who does not already hold the password.
        """
        email = email.strip().lower()

        # Tier 1: this address has been guessing at this account. Refused
        # before the password is even compared, and not counted against the
        # account, so one source cannot push the account towards tier 2.
        if await login_throttle.is_blocked(email, ip_address):
            verify_password(password, _dummy_hash())
            return None

        result = await self.session.execute(
            select(User).where(func.lower(User.email) == email, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()

        if user is None:
            verify_password(password, _dummy_hash())
            await login_throttle.record_failure(email, ip_address)
            return None

        now = datetime.now(UTC)
        # Tier 2: the account-wide lock, reached only by failures from several
        # sources. Answered like any other failure.
        if user.locked_until is not None and user.locked_until > now:
            verify_password(password, _dummy_hash())
            await login_throttle.record_failure(email, ip_address)
            return None

        if not user.password_hash or not verify_password(password, user.password_hash):
            await login_throttle.record_failure(email, ip_address)
            await self._register_failed_attempt(user, now)
            return None

        # Checked after the password, so this never reveals whether a suspended
        # address exists to someone who cannot authenticate as it.
        if user.status is not UserStatus.ACTIVE:
            raise ForbiddenException("This account is not active")

        await login_throttle.clear(email, ip_address)
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
        newly_locked = user.failed_login_attempts >= settings.ACCOUNT_LOCKOUT_THRESHOLD
        if newly_locked:
            user.locked_until = now + timedelta(minutes=settings.ACCOUNT_LOCKOUT_MINUTES)
        await self.session.commit()

        if newly_locked:
            # The login response no longer says "locked", so the owner hears it
            # here — the one channel an attacker cannot read.
            from .notification_service import NotificationService  # local: avoids an import cycle

            await NotificationService(self.session).notify_many(
                [user.id],
                notification_type=NotificationType.SYSTEM,
                title="Sign-in temporarily locked",
                message=(
                    "Your account received many failed sign-in attempts and sign-in has been paused for "
                    f"{settings.ACCOUNT_LOCKOUT_MINUTES} minutes. If this was not you, change your password "
                    "once you are back in, and tell an administrator."
                ),
            )

    async def register_user(self, payload: PublicRegisterRequest) -> User:
        """Public self-signup. Always creates an ACTIVE STUDENT.

        `role` and `status` are not parameters and are not read from the
        payload — see `PublicRegisterRequest`.
        """
        # `payload.email` is already lower-cased (schemas/email.py); comparing
        # on lower() as well covers rows written before that was true.
        existing = await self.session.execute(select(User.id).where(func.lower(User.email) == payload.email))
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

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
        *,
        keep_session_id: uuid.UUID | None = None,
    ) -> bool:
        if not await self.verify_reauth(user, current_password):
            return False
        user.password_hash = hash_password(new_password)
        user.must_change_password = False
        # Changing a password is how someone responds to a suspected
        # compromise, so it has to invalidate sessions the attacker may hold —
        # every one except the session making the change, which is what the
        # route has always documented ("revokes every other session"). With
        # `sid` on access tokens, the others die on their next request.
        conditions: list[ColumnElement[bool]] = [UserSession.user_id == user.id]
        if keep_session_id is not None:
            conditions.append(UserSession.id != keep_session_id)
        await self._revoke_where(*conditions)
        await self.session.commit()
        await self.session.refresh(user)
        return True

    async def verify_reauth(self, user: User, password: str) -> bool:
        """Check the password of an already signed-in user, with a brake.

        Five wrong answers per lockout window per account, after which even the
        right one is refused until the window passes — so a stolen access token
        cannot be turned into the password by guessing through the "confirm
        your password" prompts.
        """
        if await login_throttle.is_blocked(user.email, REAUTH_SCOPE):
            verify_password(password, _dummy_hash())
            return False
        if user.password_hash and verify_password(password, user.password_hash):
            await login_throttle.clear(user.email, REAUTH_SCOPE)
            return True
        await login_throttle.record_failure(user.email, REAUTH_SCOPE)
        return False

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
        without a live one. The access token carries that row's id (`sid`), so
        revoking the row ends both.
        """
        refresh_token, token_hash, expires_at = create_refresh_token(str(user.id))
        session_row = UserSession(
            id=uuid.uuid4(),
            user_id=user.id,
            refresh_token_hash=token_hash,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
        )
        self.session.add(session_row)
        await self.session.commit()

        access_token = create_access_token(str(user.id), role=user.role.value, session_id=str(session_row.id))
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
        match a session row that is neither revoked nor expired.

        **Rotation is one atomic statement** (FAPI-SEC-016). It used to be a
        SELECT and then a separate write, so two concurrent refreshes with the
        same token could both pass the SELECT and both be issued a new pair.
        `UPDATE ... WHERE revoked_at IS NULL RETURNING` lets exactly one win.

        **Reuse is treated as theft.** A token whose row was already revoked
        means either two tabs raced (the loser arrives within a few seconds) or
        someone replayed a stolen token after its owner used it. Outside the
        short grace window, every session the user holds is revoked, so a thief
        holding an old refresh token cannot keep a foothold.
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
        rotated_user_id = await self.session.scalar(
            update(UserSession)
            .where(
                UserSession.refresh_token_hash == token_hash,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .values(revoked_at=now)
            .returning(UserSession.user_id)
        )

        if rotated_user_id is None:
            await self._handle_possible_reuse(token_hash, now)
            return None

        if str(rotated_user_id) != str(user_id):  # pragma: no cover - hash collision / tampering
            await self.session.rollback()
            return None

        user = await self.get_user_by_id(user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            await self.session.commit()
            return None

        return await self.issue_tokens(user, ip_address=ip_address, user_agent=user_agent)

    async def _handle_possible_reuse(self, token_hash: str, now: datetime) -> None:
        revoked = await self.session.scalar(
            select(UserSession).where(
                UserSession.refresh_token_hash == token_hash,
                UserSession.revoked_at.is_not(None),
            )
        )
        if revoked is None or revoked.revoked_at is None:
            return
        grace = timedelta(seconds=settings.REFRESH_REUSE_GRACE_SECONDS)
        if now - revoked.revoked_at <= grace:
            return
        await self.revoke_all_sessions(revoked.user_id)
        await self.session.commit()

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

    async def revoke_session(self, refresh_token: str, *, user_id: uuid.UUID) -> bool:
        """Revoke one of *this user's* sessions. Returns whether one matched.

        Scoped to the caller: a refresh token belonging to somebody else is
        ignored rather than revoked on their behalf.
        """
        revoked = await self._revoke_where(
            UserSession.refresh_token_hash == hash_refresh_token(refresh_token),
            UserSession.user_id == user_id,
        )
        await self.session.commit()
        return bool(revoked)

    async def revoke_session_by_id(self, session_id: uuid.UUID, *, user_id: uuid.UUID) -> bool:
        revoked = await self._revoke_where(UserSession.id == session_id, UserSession.user_id == user_id)
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
