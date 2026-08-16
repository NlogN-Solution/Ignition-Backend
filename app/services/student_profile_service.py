from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..models import StudentEducationHistory, StudentProfile, StudentWorkExperience
from .partial_update import reject_null_on_required


class StudentProfileService:
    """A student's profile plus its education and work-experience sub-resources.

    Every sub-resource lookup here takes the owning profile, not just the entry
    id. ED360 looks entries up by id alone and leaves ownership to the router,
    which checks the caller may access `user_id` and then never confirms the
    entry actually belongs to that user — so a student passing their own
    `user_id` with someone else's `entry_id` can edit or delete a stranger's
    history. Requiring the profile at the query makes that unrepresentable
    rather than something each of the four call sites must remember.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id: UUID) -> StudentProfile | None:
        return await self.session.scalar(
            select(StudentProfile).where(
                StudentProfile.user_id == user_id,
                StudentProfile.deleted_at.is_(None),
            )
        )

    async def upsert(self, user_id: UUID, data: dict[str, Any]) -> StudentProfile:
        profile = await self.get_by_user_id(user_id)
        if profile is None:
            if not data.get("education_level"):
                raise ValueError("education_level is required to create a student profile")
            profile = StudentProfile(user_id=user_id, **data)
            self.session.add(profile)
        else:
            reject_null_on_required(StudentProfile, data)
            for key, value in data.items():
                setattr(profile, key, value)

        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    # --- Education history ----------------------------------------------------

    async def list_education(self, profile: StudentProfile) -> list[StudentEducationHistory]:
        result = await self.session.execute(
            select(StudentEducationHistory)
            .where(StudentEducationHistory.student_profile_id == profile.id)
            .order_by(StudentEducationHistory.end_date.desc().nullslast())
        )
        return list(result.scalars().all())

    async def get_education_entry(
        self,
        entry_id: UUID,
        profile: StudentProfile,
    ) -> StudentEducationHistory | None:
        """Scoped to the profile — an entry belonging to someone else is a 404,
        not a foothold."""
        return await self.session.scalar(
            select(StudentEducationHistory).where(
                StudentEducationHistory.id == entry_id,
                StudentEducationHistory.student_profile_id == profile.id,
            )
        )

    async def add_education(self, profile: StudentProfile, data: dict[str, Any]) -> StudentEducationHistory:
        entry = StudentEducationHistory(student_profile_id=profile.id, **data)
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def update_education(
        self,
        entry: StudentEducationHistory,
        data: dict[str, Any],
    ) -> StudentEducationHistory:
        for key, value in data.items():
            setattr(entry, key, value)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def delete_education(self, entry: StudentEducationHistory) -> None:
        await self.session.delete(entry)
        await self.session.commit()

    # --- Work experience ------------------------------------------------------

    async def list_experience(self, profile: StudentProfile) -> list[StudentWorkExperience]:
        result = await self.session.execute(
            select(StudentWorkExperience)
            .where(StudentWorkExperience.student_profile_id == profile.id)
            .order_by(StudentWorkExperience.end_date.desc().nullslast())
        )
        return list(result.scalars().all())

    async def get_experience_entry(
        self,
        entry_id: UUID,
        profile: StudentProfile,
    ) -> StudentWorkExperience | None:
        return await self.session.scalar(
            select(StudentWorkExperience).where(
                StudentWorkExperience.id == entry_id,
                StudentWorkExperience.student_profile_id == profile.id,
            )
        )

    async def add_experience(self, profile: StudentProfile, data: dict[str, Any]) -> StudentWorkExperience:
        entry = StudentWorkExperience(student_profile_id=profile.id, **data)
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def update_experience(
        self,
        entry: StudentWorkExperience,
        data: dict[str, Any],
    ) -> StudentWorkExperience:
        for key, value in data.items():
            setattr(entry, key, value)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def delete_experience(self, entry: StudentWorkExperience) -> None:
        await self.session.delete(entry)
        await self.session.commit()


async def get_student_profile_service(session: AsyncSession = Depends(get_db_session)) -> StudentProfileService:
    return StudentProfileService(session)
