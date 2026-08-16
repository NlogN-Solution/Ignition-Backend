from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..models.enums import DegreeLevel, InstitutionType


class StudentProfileUpsert(BaseModel):
    nationality: str | None = None
    passport_number: str | None = None
    citizenship_number: str | None = None
    current_address: str | None = None
    education_level: InstitutionType | None = None
    university_name: str | None = None
    institution_name: str | None = None
    graduation_year: int | None = None
    gpa: float | None = None
    preferred_country: str | None = None
    preferred_program: str | None = None
    preferred_intake: str | None = None
    budget: float | None = None
    notes: str | None = None
    father_name: str | None = None
    mother_name: str | None = None
    birth_place: str | None = None
    permanent_address: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    #: Free-form onboarding-wizard blocks. Opaque to the server — see the
    #: model's field comments — so no nested shape is validated here.
    onboarding_completed: bool | None = None
    address: dict[str, Any] | None = None
    preferences: dict[str, Any] | None = None
    test_scores: dict[str, Any] | None = None
    education: dict[str, Any] | None = None


class StudentProfileRead(BaseModel):
    id: UUID
    user_id: UUID
    nationality: str | None
    passport_number: str | None
    citizenship_number: str | None
    current_address: str | None
    education_level: InstitutionType
    university_name: str | None
    institution_name: str | None
    graduation_year: int | None
    gpa: float | None
    preferred_country: str | None
    preferred_program: str | None
    preferred_intake: str | None
    budget: float | None
    notes: str | None
    father_name: str | None
    mother_name: str | None
    birth_place: str | None
    permanent_address: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    onboarding_completed: bool
    address: dict[str, Any] | None
    preferences: dict[str, Any] | None
    test_scores: dict[str, Any] | None
    education: dict[str, Any] | None
    #: Derived, not stored — `StudentProfile.profile_completion`.
    profile_completion: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StudentEducationHistoryUpsert(BaseModel):
    institution_name: str
    degree_level: DegreeLevel | None = None
    field_of_study: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    grade: str | None = None
    is_completed: bool = True


class StudentEducationHistoryRead(BaseModel):
    id: UUID
    student_profile_id: UUID
    institution_name: str
    degree_level: DegreeLevel | None
    field_of_study: str | None
    start_date: date | None
    end_date: date | None
    grade: str | None
    is_completed: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class StudentWorkExperienceUpsert(BaseModel):
    company_name: str
    job_title: str
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    description: str | None = None


class StudentWorkExperienceRead(BaseModel):
    id: UUID
    student_profile_id: UUID
    company_name: str
    job_title: str
    start_date: date | None
    end_date: date | None
    is_current: bool
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
