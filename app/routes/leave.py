from __future__ import annotations

import mimetypes
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import require_role
from ..api.deps import get_db_session
from ..api.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    UnprocessableEntityException,
)
from ..api.pagination import LimitParam, PageParam
from ..core.config import get_settings
from ..core.uploads import (
    DOCUMENT_EXTENSIONS,
    LEAVE_ATTACHMENT_FOLDER,
    build_download_response,
    build_download_url,
    store_upload,
)
from ..models import User
from ..models.enums import LeaveStatus, UserRole
from ..schemas.document import DocumentLinkRead
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


async def _reviewable_request(service: LeaveService, request_id: UUID, user: User):
    """A pending request this reviewer may decide.

    Not their own (FAPI-SEC-015): a manager approving their own paid leave is
    a segregation-of-duties failure, and attendance and payroll then trust it.
    The account owner (super_admin) is exempt, because there is nobody above
    them to ask. The status check here is for a friendly message; the
    authoritative one is the conditional UPDATE in `LeaveService._transition`.
    """
    request = await service.get_request(request_id)
    if request is None:
        raise NotFoundException("Leave request not found")
    if request.user_id == user.id and user.role is not UserRole.SUPER_ADMIN:
        raise ForbiddenException("You cannot review your own leave request")
    if request.status != LeaveStatus.PENDING:
        raise ConflictException(f"This request is already {request.status.value}")
    return request


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

    attachment_stored_file_name = None
    attachment_name = None
    if file is not None and file.filename:
        # ED360 writes `Path(file.filename).suffix` unchecked and with no size
        # cap, into the publicly served upload directory — same hole the avatar
        # and document uploads had.
        #
        # Private (FAPI-SEC-006): a leave attachment is usually a medical note.
        # It was uploaded as a public CDN asset whose URL worked for anyone,
        # forever. Now it is an `authenticated` asset served only through
        # `/leave-requests/{id}/attachment`, after an owner-or-manager check.
        stored = await store_upload(file, DOCUMENT_EXTENSIONS, folder=LEAVE_ATTACHMENT_FOLDER, private=True)
        attachment_stored_file_name = stored.stored_file_name
        attachment_name = file.filename

    request = await service.create_request(
        {
            "user_id": user.id,
            "leave_type_id": leave_type_id,
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason,
            "attachment_url": None,
            "attachment_stored_file_name": attachment_stored_file_name,
            "attachment_name": attachment_name,
        }
    )
    return LeaveRequestRead.model_validate(request)


@router.get("/leave-requests", response_model=LeaveRequestList, summary="List leave requests")
async def list_leave_requests(
    page: PageParam = 1,
    limit: LimitParam = 20,
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
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
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
    request = await _reviewable_request(service, request_id, user)
    approved = await service.approve(request, user.id, payload.notes)
    if approved is None:
        raise ConflictException(f"This request is already {request.status.value}")
    return LeaveRequestRead.model_validate(approved)


@router.post("/leave-requests/{request_id}/reject", response_model=LeaveRequestRead, summary="Reject a leave request")
async def reject_leave_request(
    request_id: UUID,
    payload: LeaveRejectRequest,
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*MANAGE_ROLES)),
) -> LeaveRequestRead:
    request = await _reviewable_request(service, request_id, user)
    rejected = await service.reject(request, user.id, payload.reason)
    if rejected is None:
        raise ConflictException(f"This request is already {request.status.value}")
    return LeaveRequestRead.model_validate(rejected)


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
    cancelled = await service.cancel(request)
    if cancelled is None:
        raise ConflictException(f"This request is already {request.status.value}")
    return LeaveRequestRead.model_validate(cancelled)


async def _authorised_attachment(service: LeaveService, request_id: UUID, user: User):
    request = await service.get_request(request_id)
    if request is None:
        raise NotFoundException("Leave request not found")
    if user.id != request.user_id and user.role not in MANAGE_ROLES:
        raise ForbiddenException("You do not have access to this leave request")
    if not request.attachment_stored_file_name and not request.attachment_url:
        raise NotFoundException("This leave request has no attachment")
    return request


def _mime_type_for(name: str | None) -> str | None:
    return mimetypes.guess_type(name or "")[0]


@router.get(
    "/leave-requests/{request_id}/attachment/link",
    response_model=DocumentLinkRead,
    summary="A link to a leave request's attachment",
)
async def get_leave_attachment_link(
    request_id: UUID,
    disposition: str = "inline",
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> DocumentLinkRead:
    """The requester, or a manager, only — the same rule as reading the request.

    Mirrors the document link endpoint: the caller gets a signed URL to open,
    minted only after the check. A request filed before FAPI-SEC-006 was
    fixed still has a public URL on its row; it is handed out here, behind the
    same check, until `scripts/migrate_leave_attachments_private.py` moves it.
    """
    request = await _authorised_attachment(service, request_id, user)
    name = request.attachment_name or "attachment"
    if request.attachment_stored_file_name:
        url = build_download_url(
            request.attachment_stored_file_name,
            folder=LEAVE_ATTACHMENT_FOLDER,
            download_name=name,
            inline=disposition != "attachment",
        ) or f"/api/v1/leave-requests/{request_id}/attachment"
    else:
        url = request.attachment_url or ""
    return DocumentLinkRead(url=url, file_name=name, mime_type=_mime_type_for(name))


@router.get("/leave-requests/{request_id}/attachment", summary="Download a leave request's attachment")
async def download_leave_attachment(
    request_id: UUID,
    disposition: str = "attachment",
    service: LeaveService = Depends(get_leave_service),
    user: User = Depends(require_role(*STAFF_ROLES)),
) -> Response:
    request = await _authorised_attachment(service, request_id, user)
    name = request.attachment_name or "attachment"
    if not request.attachment_stored_file_name:
        # Legacy public asset: the URL on the row is the only way to it.
        return RedirectResponse(request.attachment_url or "", status_code=307)
    return build_download_response(
        request.attachment_stored_file_name,
        folder=LEAVE_ATTACHMENT_FOLDER,
        mime_type=_mime_type_for(name),
        download_name=name,
        inline=disposition == "inline",
    )
