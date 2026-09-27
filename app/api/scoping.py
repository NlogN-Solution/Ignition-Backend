"""Whose records a staff caller may see.

Every list in the console used to be the whole book of business: a counsellor
opening Leads saw every lead in the agency, including the ones another
counsellor was working, and the same for applications and student message
threads. Role checks answered "may you use this screen", never "which rows".

The rule here answers the second question, in one place, so the three surfaces
cannot drift apart.

**Own work, plus anything unclaimed.** A counsellor sees the records assigned to
them and the records assigned to nobody — the unclaimed ones because that is how
work gets picked up: a lead nobody owns is a lead this counsellor may take, and
hiding it would mean new enquiries sat unseen until an admin dealt them out.
What they must not see is a record with someone else's name on it.

Everyone senior to that — admin, super_admin, manager — sees everything, because
oversight is their job. Roles that are not narrowed here (marketing on leads,
admissions on applications) keep the visibility they already had; this module
narrows one role deliberately rather than quietly re-permissioning the console.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Application, Lead, User
from ..models.enums import UserRole
from .exceptions import NotFoundException

#: Roles whose lists are narrowed to their own records plus unassigned ones.
OWN_WORK_ONLY_ROLES = frozenset({UserRole.COUNSELLOR})


def own_work_scope(user: User) -> UUID | None:
    """The owner id this caller's rows must match, or `None` for "see everything".

    `None` is deliberately the unrestricted answer rather than "match rows owned
    by nobody": a service reading this has to treat the two cases differently,
    and a falsy-check bug would otherwise silently widen a counsellor's view
    instead of narrowing it.
    """
    return user.id if user.role in OWN_WORK_ONLY_ROLES else None


def may_see_record(user: User, owner_id: UUID | None) -> bool:
    """Whether `user` may open one record owned by `owner_id`.

    Unowned records (`owner_id is None`) are visible to everyone who may use the
    screen at all — they are the queue everybody picks from.
    """
    scope = own_work_scope(user)
    return scope is None or owner_id is None or owner_id == scope


# --- People (FAPI-SEC-014) ---------------------------------------------------
#
# The rule above covered leads, applications and conversations but not the
# people behind them: any counsellor could open any student's profile —
# passport and citizenship numbers, family, addresses — and their document
# vault, including students a colleague was working. That undid the narrowed
# lists, because the by-id routes still answered. A student "belongs" to a
# counsellor through the same two links the message scope uses: an application
# they counsel, or a lead assigned to them that converted into this account.
# A student with neither link to anyone is unclaimed and visible to all, for
# the same reason unclaimed leads are.


def student_visibility_condition(user: User, student_id_column: Any) -> ColumnElement[bool] | None:
    """A condition on a column holding a student's user id, or `None` for "all".

    The subqueries select only non-null ids, so `NOT IN` cannot be defeated by
    a NULL in the list (which would make it match nothing).
    """
    scope = own_work_scope(user)
    if scope is None:
        return None
    own = select(Application.student_id).where(
        Application.counsellor_id == scope, Application.deleted_at.is_(None)
    ).union(
        select(Lead.converted_user_id).where(
            Lead.assigned_to == scope, Lead.converted_user_id.is_not(None), Lead.deleted_at.is_(None)
        )
    )
    claimed = select(Application.student_id).where(
        Application.counsellor_id.is_not(None), Application.deleted_at.is_(None)
    ).union(
        select(Lead.converted_user_id).where(
            Lead.assigned_to.is_not(None), Lead.converted_user_id.is_not(None), Lead.deleted_at.is_(None)
        )
    )
    return or_(student_id_column.in_(own), student_id_column.not_in(claimed))


async def may_see_student(session: AsyncSession, user: User, student_id: UUID) -> bool:
    """By-id form of `student_visibility_condition`."""
    condition = student_visibility_condition(user, User.id)
    if condition is None:
        return True
    return await session.scalar(select(User.id).where(User.id == student_id, condition)) is not None


async def assert_may_see_student(session: AsyncSession, user: User, student_id: UUID, detail: str) -> None:
    """404, like the other scoped by-id reads: a 403 would confirm the student
    exists and that a colleague is working them."""
    if not await may_see_student(session, user, student_id):
        raise NotFoundException(detail)
