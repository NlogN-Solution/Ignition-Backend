"""Schemas for the public catalogue entities (CATALOGUE-CMS-PLAN.md §5.2, §5.4, §5.5).

Five classes per entity, matching `schemas/academic.py`: Base carries the
writable fields, Create is Base, Update repeats them all as optional so a PATCH
can clear a nullable column, Read adds the identifiers, List wraps a page.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import CourseLevel, CourseSubject, EntryRoute


class UniversityRouteBase(BaseModel):
    university_id: UUID
    route_key: EntryRoute
    label: str | None = Field(default=None, max_length=120)
    applicant_country_id: UUID | None = None
    academic_criteria: str | None = None
    english_criteria: str | None = None
    english_waiver: str | None = None
    fee_structure: str | None = None
    scholarship_text: str | None = None
    gap_policy: str | None = None
    cas_deposit: str | None = None
    enrolment_fee: str | None = None
    deadlines: str | None = None
    previous_refusal: str | None = None
    #: Unrecognised criteria labels from the source file, kept rather than
    #: dropped and rendered in the admin as editable key/value rows.
    extras: dict[str, Any] | None = None
    display_order: int = 0
    is_published: bool = False


class UniversityRouteCreate(UniversityRouteBase):
    pass


class UniversityRouteUpdate(BaseModel):
    university_id: UUID | None = None
    route_key: EntryRoute | None = None
    label: str | None = Field(default=None, max_length=120)
    applicant_country_id: UUID | None = None
    academic_criteria: str | None = None
    english_criteria: str | None = None
    english_waiver: str | None = None
    fee_structure: str | None = None
    scholarship_text: str | None = None
    gap_policy: str | None = None
    cas_deposit: str | None = None
    enrolment_fee: str | None = None
    deadlines: str | None = None
    previous_refusal: str | None = None
    extras: dict[str, Any] | None = None
    display_order: int | None = None
    is_published: bool | None = None


class UniversityRouteRead(UniversityRouteBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UniversityRouteList(BaseModel):
    items: list[UniversityRouteRead]
    total: int
    page: int
    limit: int


class CourseProfileBase(BaseModel):
    slug: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=200)
    qualification: str | None = Field(default=None, max_length=60)
    subject: CourseSubject | None = None
    course_level: CourseLevel | None = None
    duration_years: float | None = None
    placement: bool = False
    overview: str | None = None
    what_you_study: str | None = None
    modules: list[dict[str, Any]] | None = None
    skills: list[str] | None = None
    entry: dict[str, Any] | None = None
    career_outcomes: list[str] | None = None
    related_careers: list[str] | None = None
    image_url: str | None = None
    is_published: bool = False
    is_example: bool = False
    display_order: int = 0


class CourseProfileCreate(CourseProfileBase):
    pass


class CourseProfileUpdate(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    qualification: str | None = Field(default=None, max_length=60)
    subject: CourseSubject | None = None
    course_level: CourseLevel | None = None
    duration_years: float | None = None
    placement: bool | None = None
    overview: str | None = None
    what_you_study: str | None = None
    modules: list[dict[str, Any]] | None = None
    skills: list[str] | None = None
    entry: dict[str, Any] | None = None
    career_outcomes: list[str] | None = None
    related_careers: list[str] | None = None
    image_url: str | None = None
    is_published: bool | None = None
    is_example: bool | None = None
    display_order: int | None = None


class CourseProfileRead(CourseProfileBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CourseProfileList(BaseModel):
    items: list[CourseProfileRead]
    total: int
    page: int
    limit: int


class ScholarshipBase(BaseModel):
    slug: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    provider: str | None = Field(default=None, max_length=200)
    kind: str | None = Field(default=None, max_length=20)
    university_id: UUID | None = None
    levels: list[str] | None = None
    #: NULL means any subject, which is not the same as an empty list.
    subjects: list[str] | None = None
    nationality_group: str | None = Field(default=None, max_length=60)
    #: Prose, not a number — "£2,000 for each year (1st, 2nd & 3rd)".
    amount: str | None = Field(default=None, max_length=60)
    deadline: str | None = Field(default=None, max_length=60)
    eligibility: str | None = None
    apply_via: str | None = None
    source: dict[str, Any] | None = None
    is_published: bool = False
    is_example: bool = False


class ScholarshipCreate(ScholarshipBase):
    pass


class ScholarshipUpdate(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    provider: str | None = Field(default=None, max_length=200)
    kind: str | None = Field(default=None, max_length=20)
    university_id: UUID | None = None
    levels: list[str] | None = None
    subjects: list[str] | None = None
    nationality_group: str | None = Field(default=None, max_length=60)
    amount: str | None = Field(default=None, max_length=60)
    deadline: str | None = Field(default=None, max_length=60)
    eligibility: str | None = None
    apply_via: str | None = None
    source: dict[str, Any] | None = None
    is_published: bool | None = None
    is_example: bool | None = None


class ScholarshipRead(ScholarshipBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScholarshipList(BaseModel):
    items: list[ScholarshipRead]
    total: int
    page: int
    limit: int
