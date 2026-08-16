from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..core.security import generate_temporary_password, hash_password
from ..models import User, UserSession
from ..models.enums import UserRole


class UserService:
    """Staff-facing user management.

    Every `organization_id` filter from ED360's version is gone (strip rule R2);
    with one tenant there is nothing to scope to.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user(self, user_id: UUID, include_deleted: bool = False) -> User | None:
        query = select(User).where(User.id == user_id)
        if not include_deleted:
            query = query.where(User.deleted_at.is_(None))
        return await self.session.scalar(query)

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))

    async def list_users(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        role: str | None = None,
        status: str | None = None,
        deleted: bool = False,
    ) -> tuple[Sequence[User], int]:
        query = select(User)
        count_query = select(func.count()).select_from(User)

        if not deleted:
            query = query.where(User.deleted_at.is_(None))
            count_query = count_query.where(User.deleted_at.is_(None))

        if search and search.strip():
            search_value = f"%{search.strip().lower()}%"
            search_filter = or_(
                func.lower(User.email).like(search_value),
                func.lower(User.first_name).like(search_value),
                func.lower(User.last_name).like(search_value),
                func.lower(User.display_name).like(search_value),
                func.lower(User.phone).like(search_value),
            )
            query = query.where(search_filter)
            count_query = count_query.where(search_filter)

        if role:
            query = query.where(User.role == role)
            count_query = count_query.where(User.role == role)

        if status:
            query = query.where(User.status == status)
            count_query = count_query.where(User.status == status)

        # ED360 leaves this unordered, which makes pagination non-deterministic:
        # Postgres may return rows in any order, so a row can appear on two
        # pages or none.
        query = query.order_by(User.created_at.desc(), User.id).offset((page - 1) * limit).limit(limit)

        total = await self.session.scalar(count_query) or 0
        result = await self.session.execute(query)
        return result.scalars().all(), total

    async def list_staff_directory(
        self,
        search: str | None = None,
        role: str | None = None,
        user_id: UUID | None = None,
        limit: int = 20,
    ) -> Sequence[User]:
        """Name + role for any non-student.

        Separate from `list_users` so every staff role can look up a colleague
        to add as an appointment attendee without gaining the fuller
        staff-management access `GET /users` reserves for admin/super_admin.
        """
        query = select(User).where(User.role != UserRole.STUDENT.value, User.deleted_at.is_(None))
        if user_id is not None:
            query = query.where(User.id == user_id)
        if role:
            query = query.where(User.role == role)
        if search and search.strip():
            search_value = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(User.first_name).like(search_value),
                    func.lower(User.last_name).like(search_value),
                    func.lower(User.display_name).like(search_value),
                )
            )
        query = query.order_by(User.first_name).limit(limit)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def create_user(self, data: dict[str, Any]) -> tuple[User, str | None]:
        """Create a staff or student account.

        Returns the user and, when one was generated, the temporary password —
        the caller's only chance to read it. `must_change_password` is set in
        that case so the generated value cannot become a permanent credential,
        which ED360 does not do.
        """
        payload = data.copy()
        supplied = payload.pop("password", None)
        generated = None if supplied else generate_temporary_password()
        password = supplied or generated

        assert password is not None  # one of the two branches always sets it
        user = User(
            **payload,
            password_hash=hash_password(password),
            must_change_password=generated is not None,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user, generated

    async def reset_password(self, user: User, new_password: str | None = None) -> tuple[User, str]:
        """Set a new password and close every existing session.

        An admin-forced reset is usually the response to a lost or compromised
        account, so leaving the old refresh tokens live would defeat it. Also
        clears the lockout, which is the other reason an admin reaches for this.
        """
        password = new_password or generate_temporary_password()
        user.password_hash = hash_password(password)
        user.must_change_password = new_password is None
        user.failed_login_attempts = 0
        user.locked_until = None

        await self.session.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.commit()
        await self.session.refresh(user)
        return user, password

    async def update_user(self, user: User, data: dict[str, Any]) -> User:
        payload = data.copy()
        if password := payload.pop("password", None):
            user.password_hash = hash_password(password)

        for key, value in payload.items():
            if value is not None:
                setattr(user, key, value)

        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def delete_user(self, user: User) -> User:
        user.deleted_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def restore_user(self, user: User) -> User:
        user.deleted_at = None
        await self.session.commit()
        await self.session.refresh(user)
        return user


async def get_user_service(session: AsyncSession = Depends(get_db_session)) -> UserService:
    return UserService(session)
