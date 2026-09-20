from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import require_role
from ..api.deps import get_db_session
from ..api.exceptions import BadRequestException, NotFoundException
from ..api.scoping import may_see_record, own_work_scope
from ..models import Lead, User
from ..schemas.lead import (
    DueFollowUpItem,
    DueFollowUpList,
    LeadActivityRead,
    LeadAssign,
    LeadConvert,
    LeadConvertResult,
    LeadCreate,
    LeadFollowUpComplete,
    LeadFollowUpCreate,
    LeadFollowUpList,
    LeadFollowUpRead,
    LeadList,
    LeadMarkLost,
    LeadQualify,
    LeadRead,
    LeadStatusUpdate,
    LeadUpdate,
)
from ..services.lead_service import LeadService

router = APIRouter(prefix="/leads", tags=["Leads"])


async def get_lead_service(session: AsyncSession = Depends(get_db_session)) -> LeadService:
    return LeadService(session)


@router.get("", response_model=LeadList, summary="List leads")
async def list_leads(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    status: str | None = None,
    statuses: str | None = None,
    source: str | None = None,
    priority: str | None = None,
    assigned_to: UUID | None = None,
    exclude_status: str | None = None,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor", "marketing")),
) -> LeadList:
    leads, total = await lead_service.list_leads(
        page,
        limit,
        search=search,
        status=status,
        statuses=statuses.split(",") if statuses else None,
        source=source,
        priority=priority,
        assigned_to=assigned_to,
        exclude_status=exclude_status,
        visible_to=own_work_scope(user),
    )
    return LeadList(items=leads, total=total, page=page, limit=limit)


# Registered before `/{lead_id}` routes so this literal path always wins.
@router.get("/follow-ups/due", response_model=DueFollowUpList, summary="List due/overdue follow-ups across leads")
async def list_due_follow_ups(
    page: int = 1,
    limit: int = 50,
    counsellor_id: UUID | None = None,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> DueFollowUpList:
    items, total = await lead_service.list_due_follow_ups(page, limit, counsellor_id=counsellor_id)
    return DueFollowUpList(items=[DueFollowUpItem(**item) for item in items], total=total, page=page, limit=limit)


async def _lead_for(lead_id: UUID, lead_service: LeadService, user: User) -> Lead:
    """Load a lead, or refuse.

    Every by-id route goes through here so that narrowing the list view cannot
    be walked around by typing the id into the address bar — the list and the
    record have to agree about what this caller may see.

    A lead owned by another counsellor answers 404, not 403: "you may not see
    this" and "this does not exist" have to be indistinguishable, or the id
    becomes an oracle for how many leads the agency holds and who owns them.
    """
    lead = await lead_service.get_lead(lead_id)
    if lead is None or not may_see_record(user, lead.assigned_to):
        raise NotFoundException("Lead not found")
    return lead


@router.get("/{lead_id}", response_model=LeadRead, summary="Get lead")
async def get_lead(
    lead_id: UUID,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor", "marketing")),
) -> LeadRead:
    lead = await _lead_for(lead_id, lead_service, user)
    return LeadRead.model_validate(lead)


@router.get("/{lead_id}/activities", response_model=list[LeadActivityRead], summary="List lead activities")
async def list_lead_activities(
    lead_id: UUID,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> list[LeadActivityRead]:
    # Loaded only to enforce the caller's scope; the rows below come from the id.
    await _lead_for(lead_id, lead_service, user)
    return [LeadActivityRead.model_validate(a) for a in await lead_service.list_activities(lead_id)]


@router.post("", response_model=LeadRead, summary="Create lead")
async def create_lead(
    payload: LeadCreate,
    lead_service: LeadService = Depends(get_lead_service),
    # ED360 guards this with a bare `get_current_user` while every other write
    # on the router is staff-gated, so a student could file CRM leads.
    user: User = Depends(require_role("admin", "counsellor", "marketing")),
) -> LeadRead:
    data = payload.model_dump()
    lead = await lead_service.create_lead(data, performed_by=user.id)
    return LeadRead.model_validate(lead)


@router.post("/{lead_id}/assign", response_model=LeadRead, summary="Assign lead")
async def assign_lead(
    lead_id: UUID,
    payload: LeadAssign,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> LeadRead:
    lead = await _lead_for(lead_id, lead_service, user)
    return LeadRead.model_validate(await lead_service.assign_lead(lead, payload.assigned_to, performed_by=user.id))


@router.post("/{lead_id}/status", response_model=LeadRead, summary="Update lead status")
async def change_lead_status(
    lead_id: UUID,
    payload: LeadStatusUpdate,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> LeadRead:
    lead = await _lead_for(lead_id, lead_service, user)
    updated = await lead_service.change_lead_status(
        lead,
        payload.status,
        performed_by=user.id,
        remarks=payload.remarks,
    )
    return LeadRead.model_validate(updated)


@router.post("/{lead_id}/qualify", response_model=LeadRead, summary="Qualify lead as a prospect")
async def qualify_lead(
    lead_id: UUID,
    payload: LeadQualify,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> LeadRead:
    lead = await _lead_for(lead_id, lead_service, user)
    return LeadRead.model_validate(await lead_service.qualify_lead(lead, performed_by=user.id))


@router.post("/{lead_id}/lost", response_model=LeadRead, summary="Mark lead as lost")
async def mark_lead_lost(
    lead_id: UUID,
    payload: LeadMarkLost,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> LeadRead:
    lead = await _lead_for(lead_id, lead_service, user)
    return LeadRead.model_validate(
        await lead_service.mark_lost(lead, payload.reason, performed_by=user.id, remarks=payload.remarks)
    )


@router.post("/{lead_id}/convert", response_model=LeadConvertResult, summary="Convert lead to client")
async def convert_lead(
    lead_id: UUID,
    payload: LeadConvert,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> LeadConvertResult:
    lead = await _lead_for(lead_id, lead_service, user)
    updated_lead, created_new_user, portal_account_created, generated_password = await lead_service.convert_lead(
        lead,
        converted_user_id=payload.converted_user_id,
        performed_by=user.id,
        remarks=payload.remarks,
        conversion_source=payload.conversion_source,
        create_portal_account=payload.create_portal_account,
    )
    return LeadConvertResult(
        lead=updated_lead,
        created_new_user=created_new_user,
        student_user_id=updated_lead.converted_user_id,
        portal_account_created=portal_account_created,
        generated_password=generated_password,
    )


@router.patch("/{lead_id}", response_model=LeadRead, summary="Update lead")
async def update_lead(
    lead_id: UUID,
    payload: LeadUpdate,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> LeadRead:
    lead = await _lead_for(lead_id, lead_service, user)
    return LeadRead.model_validate(await lead_service.update_lead(lead, payload.model_dump(exclude_unset=True)))


@router.delete("/{lead_id}", response_model=LeadRead, summary="Delete lead")
async def delete_lead(
    lead_id: UUID,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin")),
) -> LeadRead:
    lead = await _lead_for(lead_id, lead_service, user)
    return LeadRead.model_validate(await lead_service.delete_lead(lead, performed_by=user.id))


# --- Follow-ups -----------------------------------------------------------


@router.get("/{lead_id}/follow-ups", response_model=LeadFollowUpList, summary="List follow-ups for a lead")
async def list_lead_follow_ups(
    lead_id: UUID,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> LeadFollowUpList:
    # Loaded only to enforce the caller's scope; the rows below come from the id.
    await _lead_for(lead_id, lead_service, user)
    items = await lead_service.list_follow_ups(lead_id)
    return LeadFollowUpList(items=items, total=len(items))


@router.post("/{lead_id}/follow-ups", response_model=LeadFollowUpRead, summary="Schedule a follow-up")
async def create_lead_follow_up(
    lead_id: UUID,
    payload: LeadFollowUpCreate,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> LeadFollowUpRead:
    lead = await _lead_for(lead_id, lead_service, user)
    try:
        return LeadFollowUpRead.model_validate(
            await lead_service.create_follow_up(lead, payload.model_dump(), performed_by=user.id)
        )
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc


@router.patch("/{lead_id}/follow-ups/{follow_up_id}", response_model=LeadFollowUpRead, summary="Complete a follow-up")
async def complete_lead_follow_up(
    lead_id: UUID,
    follow_up_id: UUID,
    payload: LeadFollowUpComplete,
    lead_service: LeadService = Depends(get_lead_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> LeadFollowUpRead:
    lead = await _lead_for(lead_id, lead_service, user)
    follow_up = await lead_service.get_follow_up(follow_up_id)
    if follow_up is None or follow_up.lead_id != lead_id:
        raise NotFoundException("Follow-up not found")
    return LeadFollowUpRead.model_validate(
        await lead_service.complete_follow_up(lead, follow_up, payload.model_dump(), performed_by=user.id)
    )
