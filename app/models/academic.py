from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import DegreeLevel

if TYPE_CHECKING:
    from .application import Application

# The catalog is global. In ED360 these four tables were the only tenant-free
# ones and that was the right call — a university is a fact about the world, not
# about a customer. Nothing changes here under the single-tenant port; Phase 4
# extends these tables with the student portal's richer catalog fields.


class Country(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "countries"

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    iso2: Mapped[str] = mapped_column(String(2), nullable=False, unique=True)
    iso3: Mapped[str | None] = mapped_column(String(3), unique=True)
    phone_code: Mapped[str | None] = mapped_column(String(10))
    currency_code: Mapped[str | None] = mapped_column(String(3))
    flag_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    # Phase 4 (0002_ignition_catalog): fields the student portal's destination
    # screens need, sourced from the Django backend and the frontend's
    # countryFinance/countryGuides data.
    average_tuition_usd: Mapped[float | None] = mapped_column(Numeric(12, 2))
    average_living_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 2))
    visa_information: Mapped[str | None] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    universities: Mapped[list[University]] = relationship(
        back_populates="country",
        cascade="all, delete-orphan",
    )
    guide: Mapped[CountryGuide | None] = relationship(
        back_populates="country",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("idx_countries_name", "name"),)

    def __repr__(self) -> str:
        return f"<Country id={self.id} name={self.name}>"


class University(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "universities"

    country_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("countries.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(100))
    website: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    logo_url: Mapped[str | None] = mapped_column(Text)
    # Django's `world_ranking` maps onto this existing column — no new field.
    ranking: Mapped[int | None] = mapped_column(Integer)
    is_partner: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # Phase 4. `faculties` and `highlights` are free-form lists of strings with
    # no query or integrity requirements, so JSONB rather than side tables.
    acceptance_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    faculties: Mapped[list[str] | None] = mapped_column(JSONB)
    highlights: Mapped[list[str] | None] = mapped_column(JSONB)
    campus_type: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    country: Mapped[Country] = relationship(back_populates="universities")
    programs: Mapped[list[Program]] = relationship(
        back_populates="university",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_universities_country_id", "country_id"),
        Index("idx_universities_name", "name"),
        Index("idx_universities_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<University id={self.id} name={self.name}>"


class Program(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "programs"

    university_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("universities.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    degree_level: Mapped[DegreeLevel | None] = mapped_column(enum_type(DegreeLevel, "degree_level", create_type=False))
    field_of_study: Mapped[str | None] = mapped_column(String(100))
    duration_months: Mapped[int | None] = mapped_column(SmallInteger)
    tuition_fee: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    intake: Mapped[str | None] = mapped_column(String(50))
    minimum_gpa: Mapped[float | None] = mapped_column(Numeric(4, 2))
    minimum_ielts: Mapped[float | None] = mapped_column(Numeric(3, 1))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    # Phase 4. `intakes_summary` is display copy ("Feb / Jul"); the queryable
    # intake records remain the `intakes` relationship below.
    intakes_summary: Mapped[list[str] | None] = mapped_column(JSONB)
    highlights: Mapped[list[str] | None] = mapped_column(JSONB)
    outcomes: Mapped[list[str] | None] = mapped_column(JSONB)
    # Objects, not lists: the source data keys requirements by section
    # (academic/documents/english) and key dates by milestone name.
    requirements: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    key_dates: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    course_type: Mapped[str | None] = mapped_column(String(50))
    image_url: Mapped[str | None] = mapped_column(Text)

    university: Mapped[University] = relationship(back_populates="programs")
    intakes: Mapped[list[Intake]] = relationship(
        back_populates="program",
        cascade="all, delete-orphan",
    )
    applications: Mapped[list[Application]] = relationship(back_populates="program")

    __table_args__ = (
        Index("idx_programs_university_id", "university_id"),
        Index("idx_programs_name", "name"),
        Index("idx_programs_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Program id={self.id} name={self.name}>"


class Intake(Base, UUIDPKMixin):
    __tablename__ = "intakes"

    program_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    application_deadline: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    program: Mapped[Program] = relationship(back_populates="intakes")
    applications: Mapped[list[Application]] = relationship(back_populates="intake")

    __table_args__ = (
        Index("idx_intakes_program_id", "program_id"),
        Index("idx_intakes_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Intake id={self.id} name={self.name}>"


class CountryGuide(Base, UUIDPKMixin, TimestampMixin):
    """Long-form destination content shown on the student portal.

    One guide per country, keyed by a nullable FK rather than embedded on
    `countries`: a guide is editorial content with its own lifecycle (drafted,
    published, revised) and the frontend's `countryGuides.json` carries a
    free-form `about` block that would otherwise bloat every catalog query.
    """

    __tablename__ = "country_guides"

    country_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("countries.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    #: The `about` object from countryGuides.json — sections keyed by heading.
    about: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    hero_image_url: Mapped[str | None] = mapped_column(Text)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    country: Mapped[Country] = relationship(back_populates="guide")

    __table_args__ = (Index("idx_country_guides_is_published", "is_published"),)

    def __repr__(self) -> str:
        return f"<CountryGuide id={self.id} slug={self.slug}>"


class BlogPost(Base, UUIDPKMixin, TimestampMixin):
    """Marketing/advice articles surfaced in the student portal."""

    __tablename__ = "blog_posts"

    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    author: Mapped[str | None] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    external_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[date | None] = mapped_column(Date)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    __table_args__ = (
        Index("idx_blog_posts_is_published", "is_published"),
        Index("idx_blog_posts_published_at", "published_at"),
    )

    def __repr__(self) -> str:
        return f"<BlogPost id={self.id} slug={self.slug}>"
