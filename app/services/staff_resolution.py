from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Application, Lead, User
from ..models.enums import UserRole


async def resolve_assigned_counsellor_id(session: AsyncSession, student_id: UUID) -> UUID | None:
    """The counsellor actually responsible for this student.

    Their most recent application's counsellor, else the owner of the lead they
    converted from. Returns None when neither resolves — deliberately no admin
    fallback; callers that need *somebody* to notify want
    `resolve_responsible_staff_ids`.
    """
    counsellor_id = await session.scalar(
        select(Application.counsellor_id)
        .where(Application.student_id == student_id, Application.counsellor_id.isnot(None))
        .order_by(Application.created_at.desc())
        .limit(1)
    )
    if counsellor_id:
        return counsellor_id

    return await session.scalar(
        select(Lead.assigned_to)
        .where(Lead.converted_user_id == student_id, Lead.assigned_to.isnot(None))
        .order_by(Lead.created_at.desc())
        .limit(1)
    )


async def resolve_responsible_staff_ids(session: AsyncSession, student_id: UUID) -> list[UUID]:
    """Who to notify about this student's activity.

    Their assigned counsellor if there is one, otherwise every admin and
    super_admin. ED360 additionally scopes that fallback to the student's
    organization; single-tenant, "every admin" is already the whole set.
    """
    counsellor_id = await resolve_assigned_counsellor_id(session, student_id)
    if counsellor_id:
        return [counsellor_id]

    result = await session.execute(
        select(User.id).where(
            User.role.in_([UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value]),
            User.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())
