from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import require_role
from ..api.exceptions import ForbiddenException, NotFoundException
from ..models import Application, User
from ..models.enums import UserRole
from ..schemas.application import (
    ApplicationCreate,
    ApplicationList,
    ApplicationRead,
    ApplicationStatusHistoryRead,
    ApplicationStatusUpdate,
    ApplicationUpdate,
)
from ..services.application_service import ApplicationService, get_application_service

router = APIRouter(prefix="/applications", tags=["Applications"])

#: Roles that may see applications at all. Students are included because an
#: applicant tracks their own application here — every handler below therefore
#: has to narrow a student to their own rows.
_VIEW_ROLES = require_role(UserRole.ADMIN, UserRole.COUNSELLOR, UserRole.ADMISSIONS, UserRole.STUDENT)

#: Staff who may create and edit applications on a student's behalf.
_MANAGE_ROLES = require_role(UserRole.ADMIN, UserRole.COUNSELLOR, UserRole.ADMISSIONS)


def _assert_visible_to(user: User, application: Application) -> None:
    if user.role is UserRole.STUDENT and application.student_id != user.id:
        raise ForbiddenException("Forbidden")


@router.get("", response_model=ApplicationList, summary="List applications")
async def list_applications(
    page: int = 1,
    limit: int = 20,
    student_id: UUID | None = None,
    counsellor_id: UUID | None = None,
    program_id: UUID | None = None,
    status: str | None = None,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(_VIEW_ROLES),
) -> ApplicationList:
    # Overwritten, not defaulted: a student passing someone else's student_id
    # still gets only their own.
    if user.role is UserRole.STUDENT:
        student_id = user.id

    applications, total = await service.list_applications(
        page,
        limit,
        student_id=student_id,
        counsellor_id=counsellor_id,
        program_id=program_id,
        status=status,
    )
    return ApplicationList(
        items=[ApplicationRead.model_validate(a) for a in applications],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/{application_id}", response_model=ApplicationRead, summary="Get application")
async def get_application(
    application_id: UUID,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(_VIEW_ROLES),
) -> ApplicationRead:
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    _assert_visible_to(user, application)
    return ApplicationRead.model_validate(application)


@router.post("", response_model=ApplicationRead, summary="Create application")
async def create_application(
    payload: ApplicationCreate,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(_MANAGE_ROLES),
) -> ApplicationRead:
    """Staff only.

    ED360 guards this with a bare `get_current_user`, so any authenticated
    account can post an application naming an arbitrary `student_id`,
    `counsellor_id` and `status` — including a student filing one against
    another student, or opening their own already marked `enrolled`. Students
    get their own application-submission flow in Phase 5; it does not run
    through this staff endpoint.
    """
    application = await service.create_application(payload.model_dump())
    return ApplicationRead.model_validate(application)


@router.post("/{application_id}/status", response_model=ApplicationRead, summary="Update application status")
async def change_application_status(
    application_id: UUID,
    payload: ApplicationStatusUpdate,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.COUNSELLOR)),
) -> ApplicationRead:
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    updated = await service.change_application_status(
        application,
        payload.status,
        performed_by=user.id,
        remarks=payload.remarks,
    )
    return ApplicationRead.model_validate(updated)


@router.get(
    "/{application_id}/status-history",
    response_model=list[ApplicationStatusHistoryRead],
    summary="Get application status history",
)
async def get_application_status_history(
    application_id: UUID,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(_VIEW_ROLES),
) -> list[ApplicationStatusHistoryRead]:
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    _assert_visible_to(user, application)
    history = await service.list_status_history(application_id)
    return [ApplicationStatusHistoryRead.model_validate(entry) for entry in history]


@router.patch("/{application_id}", response_model=ApplicationRead, summary="Update application")
async def update_application(
    application_id: UUID,
    payload: ApplicationUpdate,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.COUNSELLOR)),
) -> ApplicationRead:
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    updated = await service.update_application(application, payload.model_dump(exclude_unset=True))
    return ApplicationRead.model_validate(updated)


@router.delete("/{application_id}", response_model=ApplicationRead, summary="Delete application")
async def delete_application(
    application_id: UUID,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> ApplicationRead:
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    return ApplicationRead.model_validate(await service.delete_application(application))
