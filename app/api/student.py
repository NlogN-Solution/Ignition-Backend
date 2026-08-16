from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

from fastapi import Depends
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db.base import Base
from ..models import StudentProfile, User
from ..models.enums import UserRole
from .auth import AUTH_MARKER, get_current_user
from .deps import get_db_session
from .exceptions import ForbiddenException, NotFoundException

ModelT = TypeVar("ModelT", bound=Base)


async def get_current_student(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """The authenticated student, with their profile eager-loaded.

    Staff are refused outright rather than silently scoped to nothing: a
    counsellor hitting `/student/me` is a bug in the caller, and returning an
    empty portal would hide it.
    """
    if user.role is not UserRole.STUDENT:
        raise ForbiddenException("This endpoint is for student accounts")

    # Loaded here so handlers can read `student.student_profile` without a lazy
    # load firing outside the async greenlet.
    loaded = await session.scalar(select(User).options(selectinload(User.student_profile)).where(User.id == user.id))
    return loaded or user


setattr(get_current_student, AUTH_MARKER, "roles:student")


class StudentScopedRepository:
    """Query helper that cannot return another student's row.

    The plan's requirement is that scoping be *structural*, not remembered at
    each handler. Every read goes through `scoped()`, which applies
    `<model>.<column> == student.id` before the caller sees a statement — so the
    filter is not something a new endpoint can forget, the way ED360's
    per-handler `if user.role == "student"` checks could be.

    There is no tenancy backstop underneath any more, so this is the only thing
    standing between one applicant and another's passport scans.
    """

    def __init__(self, session: AsyncSession, student: User) -> None:
        self.session = session
        self.student = student

    def scoped(
        self,
        model: type[ModelT],
        column: str = "student_id",
        options: Sequence[Any] | None = None,
    ) -> Select[tuple[ModelT]]:
        query = select(model).where(getattr(model, column) == self.student.id)
        # Loader options belong here rather than at the call site: a response
        # model with a nested relationship will otherwise lazy-load during
        # serialization, outside the async greenlet, and raise MissingGreenlet.
        return query.options(*options) if options else query

    async def list(
        self,
        model: type[ModelT],
        *,
        column: str = "student_id",
        order_by: Any = None,
        conditions: list[ColumnElement[bool]] | None = None,
        options: Sequence[Any] | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[ModelT], int]:
        query = self.scoped(model, column, options)
        count_query = select(func.count()).select_from(model).where(getattr(model, column) == self.student.id)
        for condition in conditions or []:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        if order_by is not None:
            query = query.order_by(order_by)
        query = query.limit(limit).offset((page - 1) * limit)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def get_or_404(
        self,
        model: type[ModelT],
        entity_id: Any,
        *,
        column: str = "student_id",
        detail: str = "Not found",
        options: Sequence[Any] | None = None,
    ) -> ModelT:
        """Fetch one row belonging to this student.

        Someone else's id is a 404, never a 403: a 403 would confirm the row
        exists, which is itself a disclosure on a guessable identifier.
        """
        # `id` comes from UUIDPKMixin, which the ModelT bound (Base) does not
        # advertise; every model this repository is used with has one.
        entity = await self.session.scalar(
            self.scoped(model, column, options).where(model.id == entity_id)  # type: ignore[attr-defined]
        )
        if entity is None:
            raise NotFoundException(detail)
        return entity

    async def profile_or_404(self) -> StudentProfile:
        """Read the profile with a query, not off the eager-loaded relationship.

        `get_current_student` loads `student_profile` once per request; reading
        that attribute returns whatever was cached when the User was first
        materialised, so a profile created earlier in the same session is
        invisible. Querying keeps this correct regardless of identity-map state.
        """
        profile = await self.session.scalar(
            select(StudentProfile).where(
                StudentProfile.user_id == self.student.id,
                StudentProfile.deleted_at.is_(None),
            )
        )
        if profile is None:
            raise NotFoundException("Student profile not found")
        return profile


async def get_student_repository(
    student: User = Depends(get_current_student),
    session: AsyncSession = Depends(get_db_session),
) -> StudentScopedRepository:
    return StudentScopedRepository(session, student)
