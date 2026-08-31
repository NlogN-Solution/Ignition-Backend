from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..models import Program, StudentEducationHistory, StudentProfile, StudentWorkExperience, University
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
