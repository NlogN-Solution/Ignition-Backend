"""How complete a student's profile is, and what is still missing.

The portal's meter used to count nine profile columns, so a student with no
date of birth, no gender, no family details, no education history, no work
experience and no test score could read "100%". Complete now means every field
the student can fill in on Edit Profile, plus at least one entry in each of the
repeatable sections — education, work experience and test scores.

Computed with queries rather than as a model property: it needs the account
(name, phone, date of birth, gender) and three related tables, and lazy-loading
any of those from a property under asyncio raises instead of loading.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import StudentEducationHistory, StudentEnglishTest, StudentProfile, StudentWorkExperience, User
from ..schemas.student_profile import StudentProfileRead

#: (label shown to the student, attribute on StudentProfile). University and
#: institution name are one fact asked two ways on the form, so either counts.
_PROFILE_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Nationality", ("nationality",)),
    ("Passport number", ("passport_number",)),
    ("Citizenship number", ("citizenship_number",)),
    ("Birth place", ("birth_place",)),
    ("Father's name", ("father_name",)),
    ("Mother's name", ("mother_name",)),
    ("Emergency contact name", ("emergency_contact_name",)),
    ("Emergency contact phone", ("emergency_contact_phone",)),
    ("Current address", ("current_address",)),
    ("Permanent address", ("permanent_address",)),
    ("Highest education level", ("education_level",)),
    ("University / institution name", ("university_name", "institution_name")),
    ("Graduation year", ("graduation_year",)),
    ("GPA", ("gpa",)),
    ("Preferred country", ("preferred_country",)),
    ("Preferred program", ("preferred_program",)),
    ("Preferred intake", ("preferred_intake",)),
    ("Budget", ("budget",)),
)

_ACCOUNT_FIELDS: tuple[tuple[str, str], ...] = (
    ("First name", "first_name"),
    ("Last name", "last_name"),
    ("Phone", "phone"),
    ("Date of birth", "date_of_birth"),
    ("Gender", "gender"),
)


def _filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _has_test_score(test_scores: dict[str, Any] | None) -> bool:
    """True if the onboarding/profile test block holds at least one scored test.

    Shape: `{"language": {"IELTS": {"selected": true, "score": "7.0", ...}}, "other": {...}}`.
    """
    for group in (test_scores or {}).values():
        if not isinstance(group, dict):
            continue
        for entry in group.values():
            if isinstance(entry, dict) and entry.get("selected") and _filled(entry.get("score")):
                return True
    return False


async def profile_completion(session: AsyncSession, profile: StudentProfile) -> tuple[int, list[str]]:
    """Return (percentage, labels of what is missing). 100 only when nothing is."""
    missing: list[str] = []

    user = await session.get(User, profile.user_id)
    for label, attr in _ACCOUNT_FIELDS:
        if user is None or not _filled(getattr(user, attr)):
            missing.append(label)

    for label, attrs in _PROFILE_FIELDS:
        if not any(_filled(getattr(profile, attr)) for attr in attrs):
            missing.append(label)

    async def _count(model: Any) -> int:
        return int(
            await session.scalar(select(func.count()).select_from(model).where(model.student_profile_id == profile.id))
            or 0
        )

    if await _count(StudentEducationHistory) == 0:
        missing.append("At least one education entry")
    if await _count(StudentWorkExperience) == 0:
        missing.append("At least one work experience entry")
    if not _has_test_score(profile.test_scores) and await _count(StudentEnglishTest) == 0:
        missing.append("At least one test score")

    total = len(_ACCOUNT_FIELDS) + len(_PROFILE_FIELDS) + 3
    # Floored, so a profile with anything missing can never round up to 100.
    percentage = 100 if not missing else math.floor((total - len(missing)) / total * 100)
    return percentage, missing


async def read_profile(session: AsyncSession, profile: StudentProfile) -> StudentProfileRead:
    """The API shape of a profile, with its completion filled in."""
    read = StudentProfileRead.model_validate(profile)
    read.profile_completion, read.profile_missing = await profile_completion(session, profile)
    return read
