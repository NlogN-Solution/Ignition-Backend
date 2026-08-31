"""Public catalogue tables (CATALOGUE-CMS-PLAN.md §5.2, §5.4, §5.5).

These three sit alongside the ported `academic.py` tables rather than replacing
them. The decision recorded in the plan is *one* catalogue: `universities` and
`programs` are extended in place, and only genuinely new entities live here.

`Scholarship` is new because there was no scholarship model at all — the public
site synthesised twelve of its fifteen records at runtime from an index
expression. `UniversityRoute` is new because the entry-criteria matrix has no
representation anywhere in the schema. `CourseProfile` is new because the site
needs an editorial explainer per subject that is *not* one university's
offering of it.

Catalogue tables hard-delete. `SoftDeleteMixin` is deliberately limited to
`User` and `StudentProfile`.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import CourseLevel, CourseSubject, EntryRoute

if TYPE_CHECKING:
    from .academic import Country, Program, University


class UniversityRoute(Base, UUIDPKMixin, TimestampMixin):
    """One column of a university's entry-criteria matrix.

    The source spreadsheet is a matrix per institution: criteria labels down
    the side, entry routes across the top. This is one (university, route,
    applicant country) cell-column of it, and the admin renders it back in
    exactly that shape because it is the file staff already work in.

    Criteria are written for a specific applicant nationality — "Tribhuvan
    University 45% OR 2:2 OR Second Division" is advice for Nepali applicants.
    `applicant_country_id` keeps that honest when a second market is added,
    rather than silently presenting one market's requirements as universal.
    """

    __tablename__ = "university_routes"

    university_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("universities.id", ondelete="CASCADE"),
        nullable=False,
    )
    route_key: Mapped[EntryRoute] = mapped_column(
        enum_type(EntryRoute, "entry_route", create_type=False),
        nullable=False,
    )
    #: The sheet's own column header, verbatim — "BNurs(Adult Nursing)",
    #: "Enhanced Extended Masters". Staff recognise their own wording.
    label: Mapped[str | None] = mapped_column(String(120))
    applicant_country_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("countries.id", ondelete="SET NULL"),
    )

    academic_criteria: Mapped[str | None] = mapped_column(Text)
    english_criteria: Mapped[str | None] = mapped_column(Text)
    english_waiver: Mapped[str | None] = mapped_column(Text)
    fee_structure: Mapped[str | None] = mapped_column(Text)
    scholarship_text: Mapped[str | None] = mapped_column(Text)
    gap_policy: Mapped[str | None] = mapped_column(Text)
    cas_deposit: Mapped[str | None] = mapped_column(Text)
    enrolment_fee: Mapped[str | None] = mapped_column(Text)
    deadlines: Mapped[str | None] = mapped_column(Text)
    previous_refusal: Mapped[str | None] = mapped_column(Text)

    #: Unrecognised criteria label -> value. The source file uses eleven known
    #: labels plus a long tail of one-offs ("PATHWAY PROGRAMME", "LONDON
    #: CAMPUS"). They land here rather than being dropped, and the admin
    #: renders them as editable key/value rows: nothing in the file is lost.
    extras: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    university: Mapped[University] = relationship(back_populates="routes")
    applicant_country: Mapped[Country | None] = relationship()
    programs: Mapped[list[Program]] = relationship(back_populates="route")

    __table_args__ = (
        # NULLS NOT DISTINCT is load-bearing, not a detail. `applicant_country_id`
        # is nullable — most routes state one market's criteria and name no
        # country — and Postgres treats NULLs as distinct by default, so the
        # plain constraint would happily accept the same (university, route)
        # twice. That would make the importer's upsert-by-slug non-idempotent
        # and quietly double every route on the second run. Requires PG 15+.
        UniqueConstraint(
            "university_id",
            "route_key",
            "applicant_country_id",
            name="uq_university_routes_uni_route_country",
            postgresql_nulls_not_distinct=True,
        ),
        Index("idx_university_routes_university_id", "university_id"),
        Index("idx_university_routes_is_published", "is_published"),
    )

    def __repr__(self) -> str:
        return f"<UniversityRoute id={self.id} university_id={self.university_id} route={self.route_key}>"


class CourseProfile(Base, UUIDPKMixin, TimestampMixin):
    """The editorial explainer for a subject, independent of any university.

    The public site has two course layers and needs both: `/courses` searches
    real offerings (`programs`, ~4,300 of them), while `/courses/[subject]`
    stays a written explainer of what studying the subject is like. Around
    30-60 of these, staff-authored.
    """

    __tablename__ = "course_profiles"

    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    qualification: Mapped[str | None] = mapped_column(String(60))
    subject: Mapped[CourseSubject | None] = mapped_column(enum_type(CourseSubject, "course_subject", create_type=False))
    course_level: Mapped[CourseLevel | None] = mapped_column(enum_type(CourseLevel, "course_level", create_type=False))
    duration_years: Mapped[float | None] = mapped_column(Numeric(3, 1))
    placement: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    overview: Mapped[str | None] = mapped_column(Text)
    what_you_study: Mapped[str | None] = mapped_column(Text)
    #: [{year, items: str[]}]
    modules: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    skills: Mapped[list[str] | None] = mapped_column(JSONB)
    #: {academic, subjects, english}
    entry: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    career_outcomes: Mapped[list[str] | None] = mapped_column(JSONB)
    #: Career slugs on the public site, not foreign keys — careers are content,
    #: not catalogue rows.
    related_careers: Mapped[list[str] | None] = mapped_column(JSONB)
    image_url: Mapped[str | None] = mapped_column(Text)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_example: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    programs: Mapped[list[Program]] = relationship(back_populates="course_profile")

    __table_args__ = (
        Index("idx_course_profiles_slug", "slug"),
        Index("idx_course_profiles_subject", "subject"),
        Index("idx_course_profiles_is_published", "is_published"),
    )

    def __repr__(self) -> str:
        return f"<CourseProfile id={self.id} slug={self.slug}>"


class Scholarship(Base, UUIDPKMixin, TimestampMixin):
    """A funding award, university-attached or external.

    Promotes records the public site previously invented at render time: twelve
    of its fifteen scholarships had their id, level, nationality and deadline
    generated from `(index + awardIndex) % 4`.

    `amount` and `deadline` are strings, deliberately. The source values are
    prose — "£1,000 (for the first year only)", "5% EARLY PAYMENT DISCOUNT If
    full fees paid", "£2,000 for each year (1st, 2nd & 3rd) Total: £6,000".
    Forcing them into numerics and dates would lose the part that makes them
    useful to a student.
    """

    __tablename__ = "scholarships"

    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(200))
    #: "university" | "external"
    kind: Mapped[str | None] = mapped_column(String(20))
    university_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("universities.id", ondelete="CASCADE"),
    )

    #: CourseLevel display values. NULL on `subjects` means any subject.
    levels: Mapped[list[str] | None] = mapped_column(JSONB)
    subjects: Mapped[list[str] | None] = mapped_column(JSONB)
    nationality_group: Mapped[str | None] = mapped_column(String(60))
    amount: Mapped[str | None] = mapped_column(String(60))
    deadline: Mapped[str | None] = mapped_column(String(60))
    eligibility: Mapped[str | None] = mapped_column(Text)
    apply_via: Mapped[str | None] = mapped_column(Text)
    #: {label, href} — the authority this figure is deferred to.
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_example: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    university: Mapped[University | None] = relationship(back_populates="scholarships")

    __table_args__ = (
        Index("idx_scholarships_slug", "slug"),
        Index("idx_scholarships_university_id", "university_id"),
        Index("idx_scholarships_is_published", "is_published"),
    )

    def __repr__(self) -> str:
        return f"<Scholarship id={self.id} slug={self.slug}>"
