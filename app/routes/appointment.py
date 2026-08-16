from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import require_role
from ..api.exceptions import ForbiddenException, NotFoundException, UnprocessableEntityException
from ..models import Appointment, User
from ..models.enums import AppointmentStatus, NotificationType, UserRole
from ..schemas.appointment import (
    AppointmentCreate,
    AppointmentList,
    AppointmentRead,
    AppointmentUpdate,
)
from ..services.appointment_service import AppointmentService, get_appointment_service
from ..services.notification_service import NotificationService, get_notification_service

router = APIRouter(prefix="/appointments", tags=["Appointments"])

_VIEW_ROLES = require_role(UserRole.ADMIN, UserRole.COUNSELLOR, UserRole.SUPPORT, UserRole.STUDENT)
_MANAGE_ROLES = require_role(UserRole.ADMIN, UserRole.COUNSELLOR)


def _assert_visible_to(user: User, appointment: Appointment) -> None:
    if user.role is UserRole.STUDENT and appointment.student_id != user.id:
        raise ForbiddenException("Forbidden")


@router.get("", response_model=AppointmentList, summary="List appointments")
async def list_appointments(
    page: int = 1,
    limit: int = 20,
    student_id: UUID | None = None,
    counsellor_id: UUID | None = None,
    lead_id: UUID | None = None,
    appointment_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    service: AppointmentService = Depends(get_appointment_service),
    user: User = Depends(_VIEW_ROLES),
) -> AppointmentList:
    if user.role is UserRole.STUDENT:
        student_id = user.id

    appointments, total = await service.list_appointments(
        page,
        limit,
        student_id=student_id,
        counsellor_id=counsellor_id,
        lead_id=lead_id,
        appointment_type=appointment_type,
        status=status,
        search=search,
    )
    return AppointmentList(
        items=[AppointmentRead.model_validate(a) for a in appointments],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/{appointment_id}", response_model=AppointmentRead, summary="Get appointment")
async def get_appointment(
    appointment_id: UUID,
    service: AppointmentService = Depends(get_appointment_service),
    user: User = Depends(_VIEW_ROLES),
) -> AppointmentRead:
    appointment = await service.get_appointment(appointment_id)
    if appointment is None:
        raise NotFoundException("Appointment not found")
    _assert_visible_to(user, appointment)
    return AppointmentRead.model_validate(appointment)


@router.post("", response_model=AppointmentRead, summary="Create appointment")
async def create_appointment(
    payload: AppointmentCreate,
    service: AppointmentService = Depends(get_appointment_service),
    notification_service: NotificationService = Depends(get_notification_service),
    user: User = Depends(_VIEW_ROLES),
) -> AppointmentRead:
    data = payload.model_dump()
    data["created_by"] = user.id

    if user.role is UserRole.STUDENT:
        # A student may only *request* a slot for themselves, with a preferred
        # date. The counsellor sets the real time, location and link when they
        # confirm it, so every field that would let a student self-schedule is
        # overwritten here rather than trusted.
        if payload.preferred_date is None:
            raise UnprocessableEntityException("preferred_date is required")

        counsellor_id = await service.resolve_assigned_counsellor_id(user.id)
        data.update(
            student_id=user.id,
            counsellor_id=counsellor_id,
            attendee_ids=None,
            lead_id=None,
            status=AppointmentStatus.REQUESTED,
            start_time=None,
            end_time=None,
            location=None,
            meeting_link=None,
        )
        appointment = await service.create_appointment(data)

        return AppointmentRead.model_validate(appointment)

    if payload.student_id is None:
        raise UnprocessableEntityException("student_id is required")
    if payload.start_time is None or payload.end_time is None:
        raise UnprocessableEntityException("start_time and end_time are required")

    data["status"] = payload.status or AppointmentStatus.SCHEDULED
    appointment = await service.create_appointment(data)

    return AppointmentRead.model_validate(appointment)


@router.patch("/{appointment_id}", response_model=AppointmentRead, summary="Update appointment")
async def update_appointment(
    appointment_id: UUID,
    payload: AppointmentUpdate,
    service: AppointmentService = Depends(get_appointment_service),
    notification_service: NotificationService = Depends(get_notification_service),
    user: User = Depends(_MANAGE_ROLES),
) -> AppointmentRead:
    appointment = await service.get_appointment(appointment_id)
    if appointment is None:
        raise NotFoundException("Appointment not found")

    previous_status = appointment.status
    updated = await service.update_appointment(appointment, payload.model_dump(exclude_unset=True))

    if updated.student_id and updated.status is not previous_status:
        await notification_service.notify_many(
            [updated.student_id],
            notification_type=NotificationType.APPOINTMENT,
            title="Appointment updated",
            message=f"{updated.title} is now {updated.status.value}",
        )
    return AppointmentRead.model_validate(updated)


@router.delete("/{appointment_id}", response_model=AppointmentRead, summary="Delete appointment")
async def delete_appointment(
    appointment_id: UUID,
    service: AppointmentService = Depends(get_appointment_service),
    user: User = Depends(_MANAGE_ROLES),
) -> AppointmentRead:
    appointment = await service.get_appointment(appointment_id)
    if appointment is None:
        raise NotFoundException("Appointment not found")
    return AppointmentRead.model_validate(await service.delete_appointment(appointment))
