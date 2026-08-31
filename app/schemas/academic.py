from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import CourseLevel, CourseSubject, DegreeLevel, UkRegion


class CountryBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    iso2: str = Field(min_length=2, max_length=2)
    iso3: str | None = Field(default=None, max_length=3)
    phone_code: str | None = Field(default=None, max_length=10)
    currency_code: str | None = Field(default=None, max_length=3)
    flag_url: str | None = None
    is_active: bool = True
    # Phase 4 catalog enrichment.
    average_tuition_usd: float | None = None
    average_living_cost_usd: float | None = None
    visa_information: str | None = None
    display_order: int = 0


class CountryCreate(CountryBase):
    pass


class CountryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    iso2: str | None = Field(default=None, min_length=2, max_length=2)
    iso3: str | None = Field(default=None, max_length=3)
    phone_code: str | None = Field(default=None, max_length=10)
    currency_code: str | None = Field(default=None, max_length=3)
    flag_url: str | None = None
    is_active: bool | None = None
    average_tuition_usd: float | None = None
    average_living_cost_usd: float | None = None
    visa_information: str | None = None
    display_order: int | None = None


class CountryRead(CountryBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CountryList(BaseModel):
    items: list[CountryRead]
    total: int
    page: int
    limit: int


class UniversityBase(BaseModel):
    country_id: UUID
    name: str = Field(min_length=1, max_length=255)
    short_name: str | None = Field(default=None, max_length=100)
    website: str | None = None
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = None
    city: str | None = Field(default=None, max_length=100)
    logo_url: str | None = None
    ranking: int | None = None
    is_partner: bool = False
    is_active: bool = True
    acceptance_rate: float | None = None
    faculties: list[str] | None = None
    highlights: list[str] | None = None
    campus_type: str | None = None
    # Public catalogue (CATALOGUE-CMS-PLAN.md §5.1). Every one of these is
    # optional, and that is the contract the public site is written against:
    # a section whose field is absent hides itself. The read models are
    # serialised with `exclude_none=True` on the public routes so an absent
    # field produces no key at all rather than a null.
    slug: str | None = Field(default=None, max_length=160)
    region: UkRegion | None = None
    tagline: str | None = Field(default=None, max_length=300)
    overview: str | None = None
    student_experience: str | None = None
    careers_text: str | None = None
    tuition_min: float | None = None
    tuition_max: float | None = None
    living_cost_monthly: float | None = None
    accommodation: dict[str, Any] | None = None
    entry: dict[str, Any] | None = None
    international_support: list[str] | None = None
    facilities: list[str] | None = None
    subjects: list[str] | None = None
    monogram: str | None = Field(default=None, max_length=3)
    founded: str | None = Field(default=None, max_length=20)
    kind: str | None = Field(default=None, max_length=100)
    campus: str | None = Field(default=None, max_length=200)
    student_population: str | None = Field(default=None, max_length=50)
    international_students: str | None = Field(default=None, max_length=120)
    student_staff_ratio: str | None = Field(default=None, max_length=20)
    history: list[str] | None = None
    milestones: list[dict[str, Any]] | None = None
    rankings: list[dict[str, Any]] | None = None
    awards: list[dict[str, Any]] | None = None
    employability: dict[str, Any] | None = None
    interview_profile: dict[str, Any] | None = None
    imagery: dict[str, Any] | None = None
    flyer_url: str | None = None
    placement_year: bool = False
    is_published: bool = False
    is_example: bool = False


class UniversityCreate(UniversityBase):
    pass


class UniversityUpdate(BaseModel):
    country_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    short_name: str | None = Field(default=None, max_length=100)
    website: str | None = None
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = None
    city: str | None = Field(default=None, max_length=100)
    logo_url: str | None = None
    ranking: int | None = None
    is_partner: bool | None = None
    is_active: bool | None = None
    acceptance_rate: float | None = None
    faculties: list[str] | None = None
    highlights: list[str] | None = None
    campus_type: str | None = None
    # Public catalogue (CATALOGUE-CMS-PLAN.md §5.1). Every one of these is
    # optional, and that is the contract the public site is written against:
    # a section whose field is absent hides itself. The read models are
    # serialised with `exclude_none=True` on the public routes so an absent
    # field produces no key at all rather than a null.
    slug: str | None = Field(default=None, max_length=160)
    region: UkRegion | None = None
    tagline: str | None = Field(default=None, max_length=300)
    overview: str | None = None
    student_experience: str | None = None
    careers_text: str | None = None
    tuition_min: float | None = None
    tuition_max: float | None = None
    living_cost_monthly: float | None = None
    accommodation: dict[str, Any] | None = None
    entry: dict[str, Any] | None = None
    international_support: list[str] | None = None
    facilities: list[str] | None = None
    subjects: list[str] | None = None
    monogram: str | None = Field(default=None, max_length=3)
    founded: str | None = Field(default=None, max_length=20)
    kind: str | None = Field(default=None, max_length=100)
    campus: str | None = Field(default=None, max_length=200)
    student_population: str | None = Field(default=None, max_length=50)
    international_students: str | None = Field(default=None, max_length=120)
    student_staff_ratio: str | None = Field(default=None, max_length=20)
    history: list[str] | None = None
    milestones: list[dict[str, Any]] | None = None
    rankings: list[dict[str, Any]] | None = None
    awards: list[dict[str, Any]] | None = None
    employability: dict[str, Any] | None = None
    interview_profile: dict[str, Any] | None = None
    imagery: dict[str, Any] | None = None
    flyer_url: str | None = None
    placement_year: bool | None = None
    is_published: bool | None = None
    is_example: bool | None = None


class UniversityRead(UniversityBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UniversityList(BaseModel):
    items: list[UniversityRead]
    total: int
    page: int
    limit: int


class ProgramBase(BaseModel):
    university_id: UUID
    name: str = Field(min_length=1, max_length=255)
    degree_level: DegreeLevel | None = None
    field_of_study: str | None = Field(default=None, max_length=100)
    duration_months: int | None = None
    tuition_fee: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    intake: str | None = Field(default=None, max_length=50)
    minimum_gpa: float | None = None
    minimum_ielts: float | None = None
    is_active: bool = True
    intakes_summary: list[str] | None = None
    highlights: list[str] | None = None
    outcomes: list[str] | None = None
    requirements: dict[str, Any] | None = None
    key_dates: dict[str, Any] | None = None
    course_type: str | None = None
    image_url: str | None = None
    # Public catalogue (CATALOGUE-CMS-PLAN.md §5.3).
    slug: str | None = Field(default=None, max_length=200)
    course_profile_id: UUID | None = None
    route_id: UUID | None = None
    subject: CourseSubject | None = None
    course_level: CourseLevel | None = None
    qualification: str | None = Field(default=None, max_length=60)
    campus: str | None = Field(default=None, max_length=120)
    duration_years: float | None = None
    extra_requirements: str | None = None
    fee_tier: str | None = Field(default=None, max_length=20)
    placement: bool = False
    is_published: bool = False
    is_example: bool = False


class ProgramCreate(ProgramBase):
    pass


class ProgramUpdate(BaseModel):
    university_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    degree_level: DegreeLevel | None = None
    field_of_study: str | None = Field(default=None, max_length=100)
    duration_months: int | None = None
    tuition_fee: float | None = None
    currency: str | None = Field(default=None, max_length=3)
    intake: str | None = Field(default=None, max_length=50)
    minimum_gpa: float | None = None
    minimum_ielts: float | None = None
    is_active: bool | None = None
    intakes_summary: list[str] | None = None
    highlights: list[str] | None = None
    outcomes: list[str] | None = None
    requirements: dict[str, Any] | None = None
    key_dates: dict[str, Any] | None = None
    course_type: str | None = None
    image_url: str | None = None
    # Public catalogue (CATALOGUE-CMS-PLAN.md §5.3).
    slug: str | None = Field(default=None, max_length=200)
    course_profile_id: UUID | None = None
    route_id: UUID | None = None
    subject: CourseSubject | None = None
    course_level: CourseLevel | None = None
    qualification: str | None = Field(default=None, max_length=60)
    campus: str | None = Field(default=None, max_length=120)
    duration_years: float | None = None
    extra_requirements: str | None = None
    fee_tier: str | None = Field(default=None, max_length=20)
    placement: bool | None = None
    is_published: bool | None = None
    is_example: bool | None = None


class ProgramRead(ProgramBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProgramList(BaseModel):
    items: list[ProgramRead]
    total: int
    page: int
    limit: int


class IntakeBase(BaseModel):
    program_id: UUID
    name: str = Field(min_length=1, max_length=50)
    start_date: date | None = None
    application_deadline: date | None = None
    is_active: bool = True


class IntakeCreate(IntakeBase):
    pass


class IntakeUpdate(BaseModel):
    program_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=50)
    start_date: date | None = None
    application_deadline: date | None = None
    is_active: bool | None = None


class IntakeRead(IntakeBase):
    """No `updated_at`: `intakes` carries only `created_at` (as in ED360)."""

    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IntakeList(BaseModel):
    items: list[IntakeRead]
    total: int
    page: int
    limit: int


# --- Phase 4 content tables (student portal reads these) ----------------------


# The student portal only ever read these two tables, so until now they had a
# Read model and nothing else — the only write path for a blog post was
# `scripts/import_catalog.py`. Create/Update below close that: a CMS that
# cannot edit the articles it publishes is not a CMS.


class CountryGuideBase(BaseModel):
    country_id: UUID
    slug: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=200)
    summary: str | None = None
    about: dict[str, Any] | None = None
    hero_image_url: str | None = None
    is_published: bool = False
    display_order: int = 0


class CountryGuideCreate(CountryGuideBase):
    pass


class CountryGuideUpdate(BaseModel):
    country_id: UUID | None = None
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = None
    about: dict[str, Any] | None = None
    hero_image_url: str | None = None
    is_published: bool | None = None
    display_order: int | None = None


class CountryGuideRead(BaseModel):
    id: UUID
    country_id: UUID
    slug: str
    title: str
    summary: str | None = None
    about: dict[str, Any] | None = None
    hero_image_url: str | None = None
    is_published: bool = False
    display_order: int = 0

    model_config = ConfigDict(from_attributes=True)


class CountryGuideList(BaseModel):
    items: list[CountryGuideRead]
    total: int
    page: int
    limit: int


class BlogPostBase(BaseModel):
    slug: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=250)
    category: str | None = Field(default=None, max_length=100)
    author: str | None = Field(default=None, max_length=150)
    description: str | None = None
    body: str | None = None
    image_url: str | None = None
    external_url: str | None = None
    published_at: date | None = None
    is_published: bool = False


class BlogPostCreate(BlogPostBase):
    pass


class BlogPostUpdate(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=250)
    category: str | None = Field(default=None, max_length=100)
    author: str | None = Field(default=None, max_length=150)
    description: str | None = None
    body: str | None = None
    image_url: str | None = None
    external_url: str | None = None
    published_at: date | None = None
    is_published: bool | None = None


class BlogPostRead(BaseModel):
    id: UUID
    slug: str
    title: str
    category: str | None = None
    author: str | None = None
    description: str | None = None
    body: str | None = None
    image_url: str | None = None
    external_url: str | None = None
    published_at: date | None = None
    is_published: bool = False

    model_config = ConfigDict(from_attributes=True)


class BlogPostList(BaseModel):
    items: list[BlogPostRead]
    total: int
    page: int
    limit: int
