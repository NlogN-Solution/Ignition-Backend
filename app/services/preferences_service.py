"""Per-student dashboard display preferences.

Phase 6. A single row per student, created with defaults on first read — the
same lazy-materialisation pattern as `StudentBudget` — since "no row yet"
and "every setting at its default" mean the same thing to a caller.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..models import StudentDashboardSettings, User


class PreferencesService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, student: User) -> StudentDashboardSettings:
        settings = await self.session.scalar(
            select(StudentDashboardSettings).where(StudentDashboardSettings.student_id == student.id)
        )
        if settings is not None:
            return settings
        settings = StudentDashboardSettings(student_id=student.id)
        self.session.add(settings)
        await self.session.commit()
        await self.session.refresh(settings)
        return settings

    async def update(self, student: User, fields: dict) -> StudentDashboardSettings:
        """`fields` is the payload's `exclude_unset=True` dump — a key being
        present means the client sent it, including an explicit `null` to
        clear `preferred_currency` back to "inferred from destination"."""
        settings = await self.get_or_create(student)
        for key in (
            "preferred_currency",
            "email_notifications_enabled",
            "push_notifications_enabled",
            "show_points_widget",
        ):
            if key in fields:
                setattr(settings, key, fields[key])
        await self.session.commit()
        await self.session.refresh(settings)
        return settings


async def get_preferences_service(session: AsyncSession = Depends(get_db_session)) -> PreferencesService:
    return PreferencesService(session)
