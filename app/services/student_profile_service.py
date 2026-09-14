from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from ..api.deps import get_db_session
from ..models import (
    Program,
    StudentEducationHistory,
    StudentProfile,
    StudentSavedCourse,
    StudentSavedUniversity,
    StudentWorkExperience,
    University,
)
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

    # --- Research shortlist ---------------------------------------------------

    async def research_shortlist(self, profile: StudentProfile) -> dict[str, Any]:
        """Resolve the student's public-site research against the real catalogue.

        Until the catalogue import, this was impossible: the public site's ids
        were slugs of invented institutions (`example-metropolitan`) and the
        backend's were UUIDs of separately seeded ones, with no correspondence
        between them. The handoff therefore travelled as read-only *research
        context* and a counsellor retyped everything into a new application.

        There is one catalogue now, and the public site's slugs are that
        catalogue's slugs, so a shortlist resolves to rows a counsellor can act
        on. Two things this still refuses to do:

        * **It does not resolve a `v1` payload.** Those carry the fictional
          slugs and were minted before the import; a `v1` id that happens to
          collide with a real slug would attach a student to an institution
          they never looked at.
        * **It does not open anything.** Resolving a name to a row is not
          consent to apply there. The counsellor still opens the application,
          against a course they choose, after talking to the student.

        A slug that no longer exists comes back in `unresolved` rather than
        being dropped, because "they shortlisted something we no longer list"
        is information, and a silently shorter list is not.
        """
        research = (profile.preferences or {}).get("research")
        if not isinstance(research, dict):
            return {"catalogue": "none", "universities": [], "unresolved": []}

        # `catalogue` is stamped by the portal from the payload version. Absent
        # means it predates the stamp, which means it predates the import.
        catalogue = research.get("catalogue") or "example"

        slugs: list[str] = []
        for entry in research.get("universities") or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                slugs.append(entry["id"])
        for entry in research.get("compared") or []:
            if isinstance(entry, str):
                slugs.append(entry)

        # Order-preserving de-duplication: the order is the student's own, and
        # the first thing they compared is not the same as the last.
        ordered = list(dict.fromkeys(slugs))
        if not ordered:
            return {"catalogue": catalogue, "universities": [], "unresolved": []}

        if catalogue != "live":
            return {"catalogue": catalogue, "universities": [], "unresolved": ordered}

        rows = (
            await self.session.execute(
                # No soft-delete on the catalogue tables — a university is
                # withdrawn by unpublishing it, and an unpublished one is still
                # a real institution a counsellor may apply to.
                select(University).where(University.slug.in_(ordered))
            )
        ).scalars().all()
        by_slug = {row.slug: row for row in rows}

        count_rows = (
            await self.session.execute(
                select(Program.university_id, func.count(Program.id))
                .where(
                    Program.university_id.in_([row.id for row in rows]),
                    Program.is_published.is_(True),
                )
                .group_by(Program.university_id)
            )
        ).all()
        counts: dict[UUID, int] = dict(count_rows)  # type: ignore[arg-type]

        universities = [
            {
                "slug": slug,
                "id": by_slug[slug].id,
                "name": by_slug[slug].name,
                "city": by_slug[slug].city,
                "region": by_slug[slug].region,
                "is_published": by_slug[slug].is_published,
                "course_count": counts.get(by_slug[slug].id, 0),
            }
            for slug in ordered
            if slug in by_slug
        ]

        return {
            "catalogue": catalogue,
            "universities": universities,
            "unresolved": [slug for slug in ordered if slug not in by_slug],
        }

    # --- Portal shortlist -----------------------------------------------------

    async def portal_shortlist(self, user_id: UUID) -> dict[str, Any]:
        """What the student saved while signed in, for whoever works their file.

        `student_saved_courses` and `student_saved_universities` have been
        written by the portal since it was built and read by nothing. A student
        could shortlist twelve courses and their counsellor would open the lead
        to a blank page, then ask them on a call what they had been looking at.

        Unlike `research_shortlist` there is no resolution step and no
        catalogue stamp to check: these rows are foreign keys into `programs`
        and `universities`, written by an authenticated student against the
        same catalogue staff work in. Nothing can fail to resolve.

        Unpublished rows are kept and flagged rather than filtered. A student
        saved it while it was published; withdrawing it afterwards is exactly
        the thing a counsellor needs to be told, not to have hidden.
        """
        course_rows = (
            await self.session.execute(
                select(StudentSavedCourse)
                .where(StudentSavedCourse.student_id == user_id)
                .options(selectinload(StudentSavedCourse.program).joinedload(Program.university))
                .order_by(StudentSavedCourse.created_at.desc())
            )
        ).scalars().all()

        university_rows = (
            await self.session.execute(
                select(StudentSavedUniversity)
                .where(StudentSavedUniversity.student_id == user_id)
                .options(selectinload(StudentSavedUniversity.university))
                .order_by(StudentSavedUniversity.created_at.desc())
            )
        ).scalars().all()

        # One grouped count for every shortlisted institution — including the
        # ones reached through a saved course, so the "start application"
        # affordance can be disabled consistently on both lists.
        university_ids = {row.university_id for row in university_rows}
        university_ids |= {row.program.university_id for row in course_rows if row.program is not None}
        counts: dict[UUID, int] = {}
        if university_ids:
            counts = dict(
                (
                    await self.session.execute(
                        select(Program.university_id, func.count(Program.id))
                        .where(Program.university_id.in_(university_ids), Program.is_published.is_(True))
                        .group_by(Program.university_id)
                    )
                ).all()  # type: ignore[arg-type]
            )

        courses = [
            {
                "id": row.program.id,
                "slug": row.program.slug,
                "title": row.program.name,
                "qualification": row.program.qualification,
                "course_level": row.program.course_level.value if row.program.course_level else None,
                "subject": row.program.subject.value if row.program.subject else None,
                "duration_years": float(row.program.duration_years)
                if row.program.duration_years is not None
                else None,
                "is_published": row.program.is_published,
                "university_id": row.program.university_id,
                "university_name": row.program.university.name if row.program.university else None,
                "university_slug": row.program.university.slug if row.program.university else None,
                "university_city": row.program.university.city if row.program.university else None,
                "saved_at": row.created_at,
            }
            for row in course_rows
            if row.program is not None
        ]

        universities = [
            {
                "id": row.university.id,
                "slug": row.university.slug,
                "name": row.university.name,
                "city": row.university.city,
                "region": row.university.region.value if row.university.region else None,
                "is_published": row.university.is_published,
                "course_count": counts.get(row.university.id, 0),
                "saved_at": row.created_at,
            }
            for row in university_rows
            if row.university is not None
        ]

        return {"courses": courses, "universities": universities}

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
