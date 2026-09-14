from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import get_current_user
from ..api.exceptions import BadRequestException, ForbiddenException, NotFoundException
from ..models import StudentProfile, User
from ..models.enums import UserRole
from ..schemas.student_profile import (
    ResearchShortlist,
    StudentEducationHistoryRead,
    StudentEducationHistoryUpsert,
    StudentProfileRead,
    StudentProfileUpsert,
    StudentShortlist,
    StudentWorkExperienceRead,
    StudentWorkExperienceUpsert,
)
from ..services.student_profile_service import StudentProfileService, get_student_profile_service

router = APIRouter(prefix="/users", tags=["Student Profile"])

#: Staff who may read and edit a student's profile on their behalf.
STUDENT_PROFILE_ROLES = frozenset({UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.COUNSELLOR})


def _assert_can_access(current_user: User, user_id: UUID) -> None:
    """Owner, or staff with a student-facing role.

    ED360's version also compares organizations; single-tenant there is no
    second organization to be in, so what remains is the ownership check that
    was always doing the real work.
    """
    if current_user.id == user_id:
        return
    if current_user.role not in STUDENT_PROFILE_ROLES:
        raise ForbiddenException("You do not have access to this student profile")


async def _resolve_profile(
    current_user: User,
    user_id: UUID,
    service: StudentProfileService,
) -> StudentProfile:
    """Authorise the caller against `user_id` and return that user's profile.

    Sub-resource handlers go through this rather than looking an entry up by id
    alone, so an entry belonging to another student can never be reached by
    pairing your own `user_id` with their `entry_id`.
    """
    _assert_can_access(current_user, user_id)
    profile = await service.get_by_user_id(user_id)
    if profile is None:
        raise NotFoundException("Student profile not found")
    return profile


# --- Profile ------------------------------------------------------------------


@router.get("/{user_id}/student-profile", response_model=StudentProfileRead, summary="Get student profile")
async def get_student_profile(
    user_id: UUID,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> StudentProfileRead:
    profile = await _resolve_profile(current_user, user_id, service)
    return StudentProfileRead.model_validate(profile)


@router.patch(
    "/{user_id}/student-profile",
    response_model=StudentProfileRead,
    summary="Create or update student profile",
)
async def upsert_student_profile(
    user_id: UUID,
    payload: StudentProfileUpsert,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> StudentProfileRead:
    _assert_can_access(current_user, user_id)
    try:
        profile = await service.upsert(user_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    return StudentProfileRead.model_validate(profile)


@router.get(
    "/{user_id}/research",
    response_model=ResearchShortlist,
    summary="The student's public-site shortlist, resolved against the catalogue",
)
async def get_research_shortlist(
    user_id: UUID,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> ResearchShortlist:
    """What the student shortlisted on the public site, as catalogue rows.

    Resolving is all this does. Opening an application against any of these is
    still a counsellor's act, in the applications API, against a course they
    pick — a shortlist is where a conversation starts, not its conclusion.
    """
    _assert_can_access(current_user, user_id)
    profile = await service.get_by_user_id(user_id)
    if profile is None:
        # Not an error: a student who has not been through onboarding has no
        # profile and therefore no research, which is an empty shortlist rather
        # than a missing resource.
        return ResearchShortlist(catalogue="none")
    return ResearchShortlist.model_validate(await service.research_shortlist(profile))


@router.get(
    "/{user_id}/shortlist",
    response_model=StudentShortlist,
    summary="The courses and universities the student saved in the portal",
)
async def get_portal_shortlist(
    user_id: UUID,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> StudentShortlist:
    """What the student saved while signed in.

    The sibling of `/research`, and the counterpart it was missing: that one is
    anonymous browsing on the public site, carried across an origin boundary
    and resolved against the catalogue on a best-effort basis. This one is a
    signed-in student pointing at rows in the same catalogue staff work in, so
    there is nothing to resolve and nothing that can fail to.

    Reading is all it does. Opening an application against a shortlisted course
    is still a counsellor's act in the applications API.
    """
    _assert_can_access(current_user, user_id)
    return StudentShortlist.model_validate(await service.portal_shortlist(user_id))


# --- Education history ---------------------------------------------------------


@router.get(
    "/{user_id}/education",
    response_model=list[StudentEducationHistoryRead],
    summary="List education history",
)
async def list_education_history(
    user_id: UUID,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> list[StudentEducationHistoryRead]:
    profile = await _resolve_profile(current_user, user_id, service)
    entries = await service.list_education(profile)
    return [StudentEducationHistoryRead.model_validate(entry) for entry in entries]


@router.post(
    "/{user_id}/education",
    response_model=StudentEducationHistoryRead,
    summary="Add an education history entry",
)
async def add_education_history(
    user_id: UUID,
    payload: StudentEducationHistoryUpsert,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> StudentEducationHistoryRead:
    profile = await _resolve_profile(current_user, user_id, service)
    entry = await service.add_education(profile, payload.model_dump())
    return StudentEducationHistoryRead.model_validate(entry)


@router.patch(
    "/{user_id}/education/{entry_id}",
    response_model=StudentEducationHistoryRead,
    summary="Update an education history entry",
)
async def update_education_history(
    user_id: UUID,
    entry_id: UUID,
    payload: StudentEducationHistoryUpsert,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> StudentEducationHistoryRead:
    profile = await _resolve_profile(current_user, user_id, service)
    entry = await service.get_education_entry(entry_id, profile)
    if entry is None:
        raise NotFoundException("Education history entry not found")
    updated = await service.update_education(entry, payload.model_dump(exclude_unset=True))
    return StudentEducationHistoryRead.model_validate(updated)


@router.delete("/{user_id}/education/{entry_id}", summary="Delete an education history entry")
async def delete_education_history(
    user_id: UUID,
    entry_id: UUID,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, bool]:
    profile = await _resolve_profile(current_user, user_id, service)
    entry = await service.get_education_entry(entry_id, profile)
    if entry is None:
        raise NotFoundException("Education history entry not found")
    await service.delete_education(entry)
    return {"success": True}


# --- Work experience -----------------------------------------------------------


@router.get(
    "/{user_id}/experience",
    response_model=list[StudentWorkExperienceRead],
    summary="List work experience",
)
async def list_work_experience(
    user_id: UUID,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> list[StudentWorkExperienceRead]:
    profile = await _resolve_profile(current_user, user_id, service)
    entries = await service.list_experience(profile)
    return [StudentWorkExperienceRead.model_validate(entry) for entry in entries]


@router.post(
    "/{user_id}/experience",
    response_model=StudentWorkExperienceRead,
    summary="Add a work experience entry",
)
async def add_work_experience(
    user_id: UUID,
    payload: StudentWorkExperienceUpsert,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> StudentWorkExperienceRead:
    profile = await _resolve_profile(current_user, user_id, service)
    entry = await service.add_experience(profile, payload.model_dump())
    return StudentWorkExperienceRead.model_validate(entry)


@router.patch(
    "/{user_id}/experience/{entry_id}",
    response_model=StudentWorkExperienceRead,
    summary="Update a work experience entry",
)
async def update_work_experience(
    user_id: UUID,
    entry_id: UUID,
    payload: StudentWorkExperienceUpsert,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> StudentWorkExperienceRead:
    profile = await _resolve_profile(current_user, user_id, service)
    entry = await service.get_experience_entry(entry_id, profile)
    if entry is None:
        raise NotFoundException("Work experience entry not found")
    updated = await service.update_experience(entry, payload.model_dump(exclude_unset=True))
    return StudentWorkExperienceRead.model_validate(updated)


@router.delete("/{user_id}/experience/{entry_id}", summary="Delete a work experience entry")
async def delete_work_experience(
    user_id: UUID,
    entry_id: UUID,
    service: StudentProfileService = Depends(get_student_profile_service),
    current_user: User = Depends(get_current_user),
) -> dict[str, bool]:
    profile = await _resolve_profile(current_user, user_id, service)
    entry = await service.get_experience_entry(entry_id, profile)
    if entry is None:
        raise NotFoundException("Work experience entry not found")
    await service.delete_experience(entry)
    return {"success": True}
