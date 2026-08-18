from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import require_role
from ..api.deps import get_db_session
from ..api.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnprocessableEntityException,
)
from ..core.config import get_settings
from ..core.uploads import DOCUMENT_EXTENSIONS, LEAVE_ATTACHMENT_FOLDER, store_upload
from ..models import User
from ..models.enums import LeaveStatus
from ..schemas.leave import (
    LeaveApproveRequest,
    LeaveBalanceList,
    LeaveRejectRequest,
    LeaveRequestList,
    LeaveRequestRead,
    LeaveTypeCreate,
    LeaveTypeList,
    LeaveTypeRead,
    LeaveTypeUpdate,
)
from ..services.leave_service import LeaveService

router = APIRouter(tags=["Leave"])

settings = get_settings()

STAFF_ROLES = (
    "admin",
    "super_admin",
    "manager",
    "counsellor",
    "staff",
    "frontdesk",
    "finance",
    "marketing",
    "support",
    "admissions",
)
MANAGE_ROLES = ("admin", "super_admin", "manager")
TYPE_MANAGE_ROLES = ("admin", "super_admin")


async def get_leave_service(session: AsyncSession = Depends(get_db_session)) -> LeaveService:
    return LeaveService(session)


# --- Leave types (registered before /leave-requests/{id} — different prefix,
# no collision risk, but kept together for readability) ---------------------


@router.get("/leave-types", response_model=LeaveTypeList, summary="List leave types")
async def list_leave_types(
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> LeaveTypeList:
    types = await service.list_types()
    return LeaveTypeList(items=types)


@router.post("/leave-types", response_model=LeaveTypeRead, summary="Create leave type")
async def create_leave_type(
    payload: LeaveTypeCreate,
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*TYPE_MANAGE_ROLES)),
) -> LeaveTypeRead:
    data = payload.model_dump()
    return LeaveTypeRead.model_validate(await service.create_type(data))


@router.patch("/leave-types/{type_id}", response_model=LeaveTypeRead, summary="Update leave type")
async def update_leave_type(
    type_id: UUID,
    payload: LeaveTypeUpdate,
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*TYPE_MANAGE_ROLES)),
) -> LeaveTypeRead:
    leave_type = await service.get_type(type_id)
    if leave_type is None:
        raise NotFoundException("Leave type not found")
    return LeaveTypeRead.model_validate(await service.update_type(leave_type, payload.model_dump(exclude_unset=True)))


@router.delete("/leave-types/{type_id}", response_model=LeaveTypeRead, summary="Delete leave type")
async def delete_leave_type(
    type_id: UUID,
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*TYPE_MANAGE_ROLES)),
) -> LeaveTypeRead:
    leave_type = await service.get_type(type_id)
    if leave_type is None:
        raise NotFoundException("Leave type not found")
    return LeaveTypeRead.model_validate(await service.delete_type(leave_type))


# --- Requests ----------------------------------------------------------------


@router.post("/leave-requests", response_model=LeaveRequestRead, summary="Request leave")
async def create_leave_request(
    leave_type_id: UUID = Form(...),
    start_date: date = Form(...),
    end_date: date = Form(...),
    reason: str | None = Form(None),
    file: UploadFile | None = File(None),
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> LeaveRequestRead:
    if end_date < start_date:
        raise UnprocessableEntityException("End date can't be before the start date")

    leave_type = await service.get_type(leave_type_id)
    if leave_type is None:
        raise NotFoundException("Leave type not found")

    attachment_url = None
    attachment_name = None
    if file is not None:
        # ED360 writes `Path(file.filename).suffix` unchecked and with no size
        # cap, into the publicly served upload directory — same hole the avatar
        # and document uploads had.
        stored = await store_upload(file, DOCUMENT_EXTENSIONS, folder=LEAVE_ATTACHMENT_FOLDER)
        attachment_url = stored.url
        attachment_name = file.filename

    request = await service.create_request(
        {
            "user_id": user.id,
            "leave_type_id": leave_type_id,
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason,
            "attachment_url": attachment_url,
            "attachment_name": attachment_name,
        }
    )
    return LeaveRequestRead.model_validate(request)


@router.get("/leave-requests", response_model=LeaveRequestList, summary="List leave requests")
async def list_leave_requests(
    page: int = 1,
    limit: int = 20,
    user_id: UUID | None = None,
    status: str | None = None,
    leave_type_id: UUID | None = None,
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> LeaveRequestList:
    if user.role not in MANAGE_ROLES:
        user_id = user.id
    requests, total = await service.list_requests(
        page,
        limit,
        user_id=user_id,
        status=status,
        leave_type_id=leave_type_id,
    )
    return LeaveRequestList(items=requests, total=total, page=page, limit=limit)


@router.get(
    "/leave-requests/employees/{employee_id}/balance",
    response_model=LeaveBalanceList,
    summary="An employee's leave balance for a year",
)
async def get_leave_balance(
    employee_id: UUID,
    year: int | None = None,
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> LeaveBalanceList:
    if user.id != employee_id and user.role not in MANAGE_ROLES:
        raise ForbiddenException("You do not have access to this employee's leave balance")
    resolved_year = year or date.today().year
    entries = await service.get_balance(employee_id, resolved_year)
    return LeaveBalanceList(year=resolved_year, items=entries)


@router.get("/leave-requests/{request_id}", response_model=LeaveRequestRead, summary="Get leave request")
async def get_leave_request(
    request_id: UUID,
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> LeaveRequestRead:
    request = await service.get_request(request_id)
    if request is None:
        raise NotFoundException("Leave request not found")
    if user.id != request.user_id and user.role not in MANAGE_ROLES:
        raise ForbiddenException("You do not have access to this leave request")
    return LeaveRequestRead.model_validate(request)


@router.post("/leave-requests/{request_id}/approve", response_model=LeaveRequestRead, summary="Approve a leave request")
async def approve_leave_request(
    request_id: UUID,
    payload: LeaveApproveRequest,
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> LeaveRequestRead:
    request = await service.get_request(request_id)
    if request is None:
        raise NotFoundException("Leave request not found")
    if request.status != LeaveStatus.PENDING:
        raise ConflictException(f"This request is already {request.status.value}")
    return LeaveRequestRead.model_validate(await service.approve(request, user.id, payload.notes))


@router.post("/leave-requests/{request_id}/reject", response_model=LeaveRequestRead, summary="Reject a leave request")
async def reject_leave_request(
    request_id: UUID,
    payload: LeaveRejectRequest,
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> LeaveRequestRead:
    request = await service.get_request(request_id)
    if request is None:
        raise NotFoundException("Leave request not found")
    if request.status != LeaveStatus.PENDING:
        raise ConflictException(f"This request is already {request.status.value}")
    return LeaveRequestRead.model_validate(await service.reject(request, user.id, payload.reason))


@router.post("/leave-requests/{request_id}/cancel", response_model=LeaveRequestRead, summary="Cancel a leave request")
async def cancel_leave_request(
    request_id: UUID,
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> LeaveRequestRead:
    request = await service.get_request(request_id)
    if request is None:
        raise NotFoundException("Leave request not found")
    if user.id != request.user_id and user.role not in MANAGE_ROLES:
        raise ForbiddenException("You do not have access to this leave request")
    if request.status not in (LeaveStatus.PENDING, LeaveStatus.APPROVED):
        raise ConflictException(f"This request is already {request.status.value}")
    return LeaveRequestRead.model_validate(await service.cancel(request))
