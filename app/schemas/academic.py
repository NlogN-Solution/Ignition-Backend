from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import DegreeLevel


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


class CountryGuideRead(BaseModel):
    id: UUID
    country_id: UUID
    slug: str
    title: str
    summary: str | None = None
    about: dict[str, Any] | None = None
    hero_image_url: str | None = None
    display_order: int = 0

    model_config = ConfigDict(from_attributes=True)


class CountryGuideList(BaseModel):
    items: list[CountryGuideRead]
    total: int
    page: int
    limit: int


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

    model_config = ConfigDict(from_attributes=True)


class BlogPostList(BaseModel):
    items: list[BlogPostRead]
    total: int
    page: int
    limit: int
