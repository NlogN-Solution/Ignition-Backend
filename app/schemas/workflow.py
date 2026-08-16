from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import (
    ApplicationWorkflowStatus,
    ChecklistItemStatus,
    DocumentType,
    WorkflowActivityType,
    WorkflowStepStatus,
)

# --- Workflow stage document requirements ---------------------------------


class WorkflowStageDocumentRequirementBase(BaseModel):
    document_type: DocumentType | None = None
    custom_label: str | None = None
    is_required: bool = True


class WorkflowStageDocumentRequirementCreate(WorkflowStageDocumentRequirementBase):
    pass


class WorkflowStageDocumentRequirementUpdate(BaseModel):
    document_type: DocumentType | None = None
    custom_label: str | None = None
    is_required: bool | None = None


class WorkflowStageDocumentRequirementRead(WorkflowStageDocumentRequirementBase):
    id: UUID
    stage_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Workflow stages --------------------------------------------------------


class WorkflowStageBase(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=150)
    description: str | None = None
    category: str | None = None
    color: str | None = None
    icon: str | None = None
    is_active: bool = True


class WorkflowStageCreate(WorkflowStageBase):
    pass


class WorkflowStageUpdate(BaseModel):
    key: str | None = None
    name: str | None = None
    description: str | None = None
    category: str | None = None
    color: str | None = None
    icon: str | None = None
    is_active: bool | None = None


class WorkflowStageRead(WorkflowStageBase):
    id: UUID
    template_id: UUID
    order: int
    created_at: datetime
    updated_at: datetime
    document_requirements: list[WorkflowStageDocumentRequirementRead] = []

    model_config = ConfigDict(from_attributes=True)


class ReorderStagesRequest(BaseModel):
    stage_ids: list[UUID]


# --- Workflow templates ------------------------------------------------------


class WorkflowTemplateBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    slug: str | None = Field(default=None, max_length=160)
    description: str | None = None
    country_id: UUID | None = None
    is_default: bool = False
    is_active: bool = True


class WorkflowTemplateCreate(WorkflowTemplateBase):
    pass


class WorkflowTemplateUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    country_id: UUID | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class WorkflowTemplateRead(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None
    country_id: UUID | None
    is_default: bool
    is_active: bool
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    stage_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class WorkflowTemplateDetailRead(WorkflowTemplateRead):
    stages: list[WorkflowStageRead] = []


class WorkflowTemplateList(BaseModel):
    items: list[WorkflowTemplateRead]
    total: int
    page: int
    limit: int


# --- Application workflow ----------------------------------------------------


class InstantiateWorkflowRequest(BaseModel):
    template_id: UUID | None = None


class UpdateWorkflowStepRequest(BaseModel):
    status: WorkflowStepStatus | None = None
    assigned_to: UUID | None = None
    notes: str | None = None


class WorkflowStepActivityRead(BaseModel):
    id: UUID
    step_id: UUID
    activity_type: WorkflowActivityType
    performed_by: UUID | None
    old_status: WorkflowStepStatus | None
    new_status: WorkflowStepStatus | None
    comment: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AddStepCommentRequest(BaseModel):
    comment: str = Field(..., min_length=1)


class ApplicationWorkflowStepRead(BaseModel):
    id: UUID
    application_workflow_id: UUID
    stage_id: UUID | None
    stage_name_snapshot: str
    status: WorkflowStepStatus
    assigned_to: UUID | None
    updated_by: UUID | None
    notes: str | None
    started_at: datetime | None
    completed_at: datetime | None
    order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApplicationWorkflowRead(BaseModel):
    id: UUID
    application_id: UUID
    template_id: UUID
    status: ApplicationWorkflowStatus
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    steps: list[ApplicationWorkflowStepRead] = []

    model_config = ConfigDict(from_attributes=True)


class WorkflowStepListItem(BaseModel):
    """Flattened cross-application row — used by the dashboard bottleneck widget."""

    id: UUID
    application_id: UUID
    application_workflow_id: UUID
    template_id: UUID
    stage_id: UUID | None
    stage_key: str | None
    stage_name_snapshot: str
    status: WorkflowStepStatus
    assigned_to: UUID | None
    order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowStepList(BaseModel):
    items: list[WorkflowStepListItem]
    total: int
    page: int
    limit: int


# --- Application checklist ----------------------------------------------------


class ApplicationChecklistItemBase(BaseModel):
    stage_id: UUID | None = None
    document_type: DocumentType | None = None
    custom_label: str | None = None
    is_required: bool = True
    notes: str | None = None


class ApplicationChecklistItemCreate(ApplicationChecklistItemBase):
    pass


class ApplicationChecklistItemUpdate(BaseModel):
    status: ChecklistItemStatus | None = None
    document_id: UUID | None = None
    notes: str | None = None
    is_required: bool | None = None


class ApplicationChecklistItemRead(ApplicationChecklistItemBase):
    id: UUID
    application_id: UUID
    status: ChecklistItemStatus
    document_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
