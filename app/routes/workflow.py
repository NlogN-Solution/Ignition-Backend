from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user, require_role
from ..api.deps import get_db_session
from ..api.exceptions import BadRequestException, ForbiddenException, NotFoundException
from ..models import Application, User
from ..models.enums import NotificationType, UserRole, WorkflowStepStatus
from ..schemas.workflow import (
    AddStepCommentRequest,
    ApplicationChecklistItemCreate,
    ApplicationChecklistItemRead,
    ApplicationChecklistItemUpdate,
    ApplicationWorkflowRead,
    ApplicationWorkflowStepRead,
    InstantiateWorkflowRequest,
    ReorderStagesRequest,
    UpdateWorkflowStepRequest,
    WorkflowStageCreate,
    WorkflowStageDocumentRequirementCreate,
    WorkflowStageDocumentRequirementRead,
    WorkflowStageDocumentRequirementUpdate,
    WorkflowStageRead,
    WorkflowStageUpdate,
    WorkflowStepActivityRead,
    WorkflowStepList,
    WorkflowStepListItem,
    WorkflowTemplateCreate,
    WorkflowTemplateDetailRead,
    WorkflowTemplateList,
    WorkflowTemplateRead,
    WorkflowTemplateUpdate,
)
from ..services.application_service import ApplicationService, get_application_service
from ..services.notification_service import NotificationService, get_notification_service
from ..services.workflow_service import ApplicationWorkflowService, ChecklistService, WorkflowTemplateService

router = APIRouter(tags=["Workflow"])


async def get_template_service(session: AsyncSession = Depends(get_db_session)) -> WorkflowTemplateService:
    return WorkflowTemplateService(session)


async def get_application_workflow_service(
    session: AsyncSession = Depends(get_db_session),
) -> ApplicationWorkflowService:
    return ApplicationWorkflowService(session)


async def get_checklist_service(session: AsyncSession = Depends(get_db_session)) -> ChecklistService:
    return ChecklistService(session)


#: Staff roles trusted with any application's workflow.
APPLICATION_STAFF_ROLES = frozenset({UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.COUNSELLOR, UserRole.ADMISSIONS})


async def _assert_application_access(
    application_id: UUID,
    user: User,
    application_service: ApplicationService,
) -> Application:
    """Staff on the application-handling roles may access any application; a
    student may access only their own. Returns the application so callers
    that already need it (e.g. to notify its student) don't re-fetch it.

    ED360 additionally compares organizations and lets a platform admin bypass
    the check entirely; neither survives the strip (R2/R3). What is left is the
    student-ownership branch, which was always the part doing real work.
    """
    application = await application_service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    if user.role in APPLICATION_STAFF_ROLES:
        return application
    if user.role is UserRole.STUDENT and application.student_id == user.id:
        return application
    raise ForbiddenException("You do not have access to this application")


def _serialize_template(template, stage_count: int) -> WorkflowTemplateRead:
    read = WorkflowTemplateRead.model_validate(template)
    read.stage_count = stage_count
    return read


# --- Workflow templates ------------------------------------------------------


@router.get("/workflow-templates", response_model=WorkflowTemplateList, summary="List workflow templates")
async def list_workflow_templates(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    country_id: UUID | None = None,
    is_active: bool | None = None,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(get_current_user),
) -> WorkflowTemplateList:
    rows, total = await service.list_templates(page, limit, search=search, country_id=country_id, is_active=is_active)
    items = [_serialize_template(t, count) for t, count in rows]
    return WorkflowTemplateList(items=items, total=total, page=page, limit=limit)


@router.get(
    "/workflow-templates/{template_id}", response_model=WorkflowTemplateDetailRead, summary="Get workflow template"
)
async def get_workflow_template(
    template_id: UUID,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(get_current_user),
) -> WorkflowTemplateDetailRead:
    template = await service.get_template(template_id, with_stages=True)
    if template is None:
        raise NotFoundException("Workflow template not found")
    detail = WorkflowTemplateDetailRead.model_validate(template)
    detail.stage_count = len(template.stages)
    return detail


@router.post("/workflow-templates", response_model=WorkflowTemplateRead, summary="Create workflow template")
async def create_workflow_template(
    payload: WorkflowTemplateCreate,
    service: WorkflowTemplateService = Depends(get_template_service),
    user: User = Depends(require_role("admin", "super_admin")),
) -> WorkflowTemplateRead:
    data = payload.model_dump()
    template = await service.create_template(data, created_by=user.id)
    return _serialize_template(template, 0)


@router.patch(
    "/workflow-templates/{template_id}", response_model=WorkflowTemplateRead, summary="Update workflow template"
)
async def update_workflow_template(
    template_id: UUID,
    payload: WorkflowTemplateUpdate,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(require_role("admin", "super_admin")),
) -> WorkflowTemplateRead:
    template = await service.get_template(template_id)
    if template is None:
        raise NotFoundException("Workflow template not found")
    updated = await service.update_template(template, payload.model_dump(exclude_unset=True))
    return _serialize_template(updated, 0)


@router.delete(
    "/workflow-templates/{template_id}", response_model=WorkflowTemplateRead, summary="Delete workflow template"
)
async def delete_workflow_template(
    template_id: UUID,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(require_role("admin", "super_admin")),
) -> WorkflowTemplateRead:
    template = await service.get_template(template_id)
    if template is None:
        raise NotFoundException("Workflow template not found")
    deleted = await service.delete_template(template)
    return _serialize_template(deleted, 0)


@router.post(
    "/workflow-templates/{template_id}/duplicate",
    response_model=WorkflowTemplateDetailRead,
    summary="Duplicate workflow template",
)
async def duplicate_workflow_template(
    template_id: UUID,
    service: WorkflowTemplateService = Depends(get_template_service),
    user: User = Depends(require_role("admin", "super_admin")),
) -> WorkflowTemplateDetailRead:
    template = await service.get_template(template_id)
    if template is None:
        raise NotFoundException("Workflow template not found")
    clone = await service.duplicate_template(template, created_by=user.id)
    detail = await service.get_template(clone.id, with_stages=True)
    result = WorkflowTemplateDetailRead.model_validate(detail)
    result.stage_count = len(detail.stages)  # type: ignore[union-attr]
    return result


# --- Workflow stages ----------------------------------------------------------


@router.post("/workflow-templates/{template_id}/stages", response_model=WorkflowStageRead, summary="Add workflow stage")
async def create_workflow_stage(
    template_id: UUID,
    payload: WorkflowStageCreate,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(require_role("admin", "super_admin")),
) -> WorkflowStageRead:
    stage = await service.create_stage(template_id, payload.model_dump())
    return WorkflowStageRead.model_validate(stage)


@router.post(
    "/workflow-templates/{template_id}/stages/reorder",
    response_model=list[WorkflowStageRead],
    summary="Reorder workflow stages",
)
async def reorder_workflow_stages(
    template_id: UUID,
    payload: ReorderStagesRequest,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(require_role("admin", "super_admin")),
) -> list[WorkflowStageRead]:
    stages = await service.reorder_stages(template_id, payload.stage_ids)
    return stages  # type: ignore[return-value]


@router.patch(
    "/workflow-templates/{template_id}/stages/{stage_id}",
    response_model=WorkflowStageRead,
    summary="Update workflow stage",
)
async def update_workflow_stage(
    template_id: UUID,
    stage_id: UUID,
    payload: WorkflowStageUpdate,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(require_role("admin", "super_admin")),
) -> WorkflowStageRead:
    stage = await service.get_stage(stage_id)
    if stage is None or stage.template_id != template_id:
        raise NotFoundException("Workflow stage not found")
    return WorkflowStageRead.model_validate(await service.update_stage(stage, payload.model_dump(exclude_unset=True)))


@router.delete(
    "/workflow-templates/{template_id}/stages/{stage_id}",
    response_model=WorkflowStageRead,
    summary="Delete workflow stage",
)
async def delete_workflow_stage(
    template_id: UUID,
    stage_id: UUID,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(require_role("admin", "super_admin")),
) -> WorkflowStageRead:
    stage = await service.get_stage(stage_id)
    if stage is None or stage.template_id != template_id:
        raise NotFoundException("Workflow stage not found")
    return WorkflowStageRead.model_validate(await service.delete_stage(stage))


@router.post(
    "/workflow-templates/{template_id}/stages/{stage_id}/requirements",
    response_model=WorkflowStageDocumentRequirementRead,
    summary="Add document requirement",
)
async def create_stage_requirement(
    template_id: UUID,
    stage_id: UUID,
    payload: WorkflowStageDocumentRequirementCreate,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(require_role("admin", "super_admin")),
) -> WorkflowStageDocumentRequirementRead:
    stage = await service.get_stage(stage_id)
    if stage is None or stage.template_id != template_id:
        raise NotFoundException("Workflow stage not found")
    return WorkflowStageDocumentRequirementRead.model_validate(
        await service.create_requirement(stage_id, payload.model_dump())
    )


@router.patch(
    "/workflow-templates/{template_id}/stages/{stage_id}/requirements/{requirement_id}",
    response_model=WorkflowStageDocumentRequirementRead,
    summary="Update document requirement",
)
async def update_stage_requirement(
    template_id: UUID,
    stage_id: UUID,
    requirement_id: UUID,
    payload: WorkflowStageDocumentRequirementUpdate,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(require_role("admin", "super_admin")),
) -> WorkflowStageDocumentRequirementRead:
    requirement = await service.get_requirement(requirement_id)
    if requirement is None or requirement.stage_id != stage_id:
        raise NotFoundException("Document requirement not found")
    return WorkflowStageDocumentRequirementRead.model_validate(
        await service.update_requirement(requirement, payload.model_dump(exclude_unset=True))
    )


@router.delete(
    "/workflow-templates/{template_id}/stages/{stage_id}/requirements/{requirement_id}",
    response_model=WorkflowStageDocumentRequirementRead,
    summary="Delete document requirement",
)
async def delete_stage_requirement(
    template_id: UUID,
    stage_id: UUID,
    requirement_id: UUID,
    service: WorkflowTemplateService = Depends(get_template_service),
    _: object = Depends(require_role("admin", "super_admin")),
) -> WorkflowStageDocumentRequirementRead:
    requirement = await service.get_requirement(requirement_id)
    if requirement is None or requirement.stage_id != stage_id:
        raise NotFoundException("Document requirement not found")
    return WorkflowStageDocumentRequirementRead.model_validate(await service.delete_requirement(requirement))


# --- Cross-application workflow steps (dashboard aggregation) -----------------


@router.get("/workflow-steps", response_model=WorkflowStepList, summary="List workflow steps across applications")
async def list_workflow_steps(
    page: int = 1,
    limit: int = 20,
    stage_id: UUID | None = None,
    status: WorkflowStepStatus | None = None,
    template_id: UUID | None = None,
    assigned_to: UUID | None = None,
    application_id: UUID | None = None,
    service: ApplicationWorkflowService = Depends(get_application_workflow_service),
    _: object = Depends(require_role("admin", "super_admin", "counsellor")),
) -> WorkflowStepList:
    items, total = await service.list_steps(
        page,
        limit,
        stage_id=stage_id,
        status=status,
        template_id=template_id,
        assigned_to=assigned_to,
        application_id=application_id,
    )
    return WorkflowStepList(items=[WorkflowStepListItem(**item) for item in items], total=total, page=page, limit=limit)


# --- Application workflow instance ---------------------------------------------


@router.get(
    "/applications/{application_id}/workflow",
    response_model=ApplicationWorkflowRead,
    summary="Get application workflow",
)
async def get_application_workflow(
    application_id: UUID,
    service: ApplicationWorkflowService = Depends(get_application_workflow_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> ApplicationWorkflowRead:
    await _assert_application_access(application_id, user, application_service)
    workflow = await service.get_by_application(application_id)
    if workflow is None:
        raise NotFoundException("This application has no workflow yet")
    return workflow  # type: ignore[return-value]


@router.post(
    "/applications/{application_id}/workflow",
    response_model=ApplicationWorkflowRead,
    summary="Start application workflow",
)
async def start_application_workflow(
    application_id: UUID,
    payload: InstantiateWorkflowRequest,
    service: ApplicationWorkflowService = Depends(get_application_workflow_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> ApplicationWorkflowRead:
    await _assert_application_access(application_id, user, application_service)
    try:
        workflow = await service.instantiate(application_id, payload.template_id, performed_by=user.id)
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    return workflow  # type: ignore[return-value]


@router.patch(
    "/applications/{application_id}/workflow/steps/{step_id}",
    response_model=ApplicationWorkflowStepRead,
    summary="Update workflow step",
)
async def update_application_workflow_step(
    application_id: UUID,
    step_id: UUID,
    payload: UpdateWorkflowStepRequest,
    service: ApplicationWorkflowService = Depends(get_application_workflow_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> ApplicationWorkflowStepRead:
    await _assert_application_access(application_id, user, application_service)
    step = await service.get_step(step_id)
    if step is None:
        raise NotFoundException("Workflow step not found")
    return await service.update_step(step, payload.model_dump(exclude_unset=True), performed_by=user.id)  # type: ignore[return-value]


@router.get(
    "/applications/{application_id}/workflow/steps/{step_id}/activities",
    response_model=list[WorkflowStepActivityRead],
    summary="List workflow step activities",
)
async def list_workflow_step_activities(
    application_id: UUID,
    step_id: UUID,
    service: ApplicationWorkflowService = Depends(get_application_workflow_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> list[WorkflowStepActivityRead]:
    await _assert_application_access(application_id, user, application_service)
    return await service.list_activities(step_id)  # type: ignore[return-value]


@router.post(
    "/applications/{application_id}/workflow/steps/{step_id}/activities",
    response_model=WorkflowStepActivityRead,
    summary="Add a comment to a workflow step",
)
async def add_workflow_step_comment(
    application_id: UUID,
    step_id: UUID,
    payload: AddStepCommentRequest,
    service: ApplicationWorkflowService = Depends(get_application_workflow_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(require_role("admin", "super_admin", "counsellor")),
) -> WorkflowStepActivityRead:
    await _assert_application_access(application_id, user, application_service)
    step = await service.get_step(step_id)
    if step is None:
        raise NotFoundException("Workflow step not found")
    return await service.add_comment(step, payload.comment, performed_by=user.id)  # type: ignore[return-value]


# --- Application checklist ------------------------------------------------------


@router.get(
    "/applications/{application_id}/checklist",
    response_model=list[ApplicationChecklistItemRead],
    summary="List application checklist items",
)
async def list_application_checklist(
    application_id: UUID,
    service: ChecklistService = Depends(get_checklist_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> list[ApplicationChecklistItemRead]:
    await _assert_application_access(application_id, user, application_service)
    return await service.list_items(application_id)  # type: ignore[return-value]


@router.post(
    "/applications/{application_id}/checklist",
    response_model=ApplicationChecklistItemRead,
    summary="Request a document from the student",
)
async def create_application_checklist_item(
    application_id: UUID,
    payload: ApplicationChecklistItemCreate,
    service: ChecklistService = Depends(get_checklist_service),
    application_service: ApplicationService = Depends(get_application_service),
    notification_service: NotificationService = Depends(get_notification_service),
    # Any staff role that already has application access may request a
    # document — not just admin/counsellor, so admissions can ask for a
    # missing transcript without going through a counsellor first.
    user: User = Depends(require_role("admin", "super_admin", "counsellor", "admissions")),
) -> ApplicationChecklistItemRead:
    application = await _assert_application_access(application_id, user, application_service)
    item = await service.create_item(application_id, payload.model_dump())
    label = item.custom_label or (item.document_type.value if item.document_type else "a document")
    await notification_service.notify_many(
        [application.student_id],
        notification_type=NotificationType.DOCUMENT,
        title="Document requested",
        message=f"Your counsellor requested {label} for your application. Upload it from the application details page.",
    )
    return item  # type: ignore[return-value]


#: A student may only link an uploaded document to their own checklist item —
#: verifying, rejecting or waiving it is a staff call, made either directly
#: here or (more commonly) implicitly via /documents/{id}/verify, which the
#: `sync_checklist_item_on_document_*` subscribers mirror onto this row.
_STUDENT_EDITABLE_CHECKLIST_FIELDS = frozenset({"document_id", "notes"})


@router.patch(
    "/applications/{application_id}/checklist/{item_id}",
    response_model=ApplicationChecklistItemRead,
    summary="Update a checklist item",
)
async def update_application_checklist_item(
    application_id: UUID,
    item_id: UUID,
    payload: ApplicationChecklistItemUpdate,
    service: ChecklistService = Depends(get_checklist_service),
    application_service: ApplicationService = Depends(get_application_service),
    user: User = Depends(get_current_user),
) -> ApplicationChecklistItemRead:
    await _assert_application_access(application_id, user, application_service)
    item = await service.get_item(item_id)
    if item is None or item.application_id != application_id:
        raise NotFoundException("Checklist item not found")

    data = payload.model_dump(exclude_unset=True)
    if user.role not in APPLICATION_STAFF_ROLES:
        disallowed = set(data) - _STUDENT_EDITABLE_CHECKLIST_FIELDS
        if disallowed:
            raise ForbiddenException(f"Only staff can update: {', '.join(sorted(disallowed))}")
    return await service.update_item(item, data)  # type: ignore[return-value]
