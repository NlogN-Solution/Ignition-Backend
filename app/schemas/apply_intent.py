"""What an apply intent looks like on the wire.

The request carries a slug and nothing else the server will believe. The
response carries the *resolved* course, because the point of the feature is
that the browser never had to hold it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApplyIntentCreate(BaseModel):
    #: A public course slug, as served by `/public/courses/{slug}`. Resolved
    #: against `programs.slug` server-side; anything unpublished 404s.
    course_slug: str = Field(min_length=1, max_length=200)
    intake_id: UUID | None = None
    #: The public page the student pressed Apply on. Advisory only.
    source_path: str | None = Field(default=None, max_length=300)


class ApplyIntentCourse(BaseModel):
    """The "you're applying for" card, assembled from the database."""

    program_id: UUID
    course_slug: str | None
    course_name: str
    degree_level: str | None
    university_id: UUID | None
    university_name: str | None
    university_city: str | None
    intake_id: UUID | None
    intake_label: str | None
    tuition_fee: float | None
    currency: str | None
    duration_months: int | None


class ApplyIntentRead(BaseModel):
    id: UUID
    course: ApplyIntentCourse
    source_path: str | None
    created_at: datetime
    claimed_at: datetime | None
    fulfilled_at: datetime | None
    #: True once this student has opened the application it was about. The
    #: portal stops offering it as "your selected course" at that point.
    is_fulfilled: bool

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def of(cls, intent: Any) -> ApplyIntentRead:
        """Assemble the response from an intent and its resolved course.

        A classmethod here rather than a helper in either router, because both
        the public routes and `/student/me/apply-intent` return it and the card
        has to read *identically* before and after sign-in — a student who sees
        "MSc Computer Science, Coventry, September 2026" on the registration
        screen must see the same sentence on the first screen inside. Two
        formatters would eventually disagree.

        Reads only already-loaded relationships: `ApplyIntentService` eager-loads
        programme, university and intake, so nothing here triggers IO outside
        the async greenlet.
        """
        program = intent.program
        university = program.university if program else None
        return cls(
            id=intent.id,
            course=ApplyIntentCourse(
                program_id=intent.program_id,
                course_slug=program.slug if program else None,
                course_name=program.name if program else "",
                degree_level=program.degree_level if program else None,
                university_id=intent.university_id,
                university_name=university.name if university else None,
                university_city=university.city if university else None,
                intake_id=intent.intake_id,
                intake_label=intent.intake.name if intent.intake else (program.intake if program else None),
                tuition_fee=float(program.tuition_fee) if program and program.tuition_fee is not None else None,
                currency=program.currency if program else None,
                duration_months=program.duration_months if program else None,
            ),
            source_path=intent.source_path,
            created_at=intent.created_at,
            claimed_at=intent.claimed_at,
            fulfilled_at=intent.fulfilled_at,
            is_fulfilled=intent.fulfilled_at is not None,
        )
