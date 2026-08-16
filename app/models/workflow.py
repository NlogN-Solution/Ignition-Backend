from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import (
    ApplicationWorkflowStatus,
    ChecklistItemStatus,
    DocumentType,
    WorkflowActivityType,
    WorkflowStepStatus,
)

if TYPE_CHECKING:
    from .academic import Country
    from .application import Application
    from .document import Document
    from .user import User


class WorkflowTemplate(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "workflow_templates"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    # Globally unique, which is simply correct here. In ED360 this same
    # constraint sat on a tenant-scoped table and was a genuine bug: two
    # organizations could not both have a "standard-uk" template.
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    country_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("countries.id", ondelete="SET NULL"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    country: Mapped[Country | None] = relationship("Country")
    stages: Mapped[list[WorkflowStage]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="WorkflowStage.order",
    )

    __table_args__ = (
        Index("idx_workflow_templates_country_id", "country_id"),
        Index("idx_workflow_templates_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<WorkflowTemplate id={self.id} name={self.name}>"


class WorkflowStage(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "workflow_stages"

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(50))
    color: Mapped[str | None] = mapped_column(String(20))
    icon: Mapped[str | None] = mapped_column(String(50))
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    template: Mapped[WorkflowTemplate] = relationship(back_populates="stages")
    document_requirements: Mapped[list[WorkflowStageDocumentRequirement]] = relationship(
        back_populates="stage",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("template_id", "key", name="uq_workflow_stages_template_id_key"),
        Index("idx_workflow_stages_template_id", "template_id"),
    )

    def __repr__(self) -> str:
        return f"<WorkflowStage id={self.id} key={self.key}>"


class WorkflowStageDocumentRequirement(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "workflow_stage_document_requirements"

    stage_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_stages.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_type: Mapped[DocumentType | None] = mapped_column(
        enum_type(DocumentType, "document_type", create_type=False)
    )
    custom_label: Mapped[str | None] = mapped_column(String(150))
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    stage: Mapped[WorkflowStage] = relationship(back_populates="document_requirements")

    __table_args__ = (Index("idx_workflow_stage_document_requirements_stage_id", "stage_id"),)

    def __repr__(self) -> str:
        return f"<WorkflowStageDocumentRequirement id={self.id} stage_id={self.stage_id}>"


class ApplicationWorkflow(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "application_workflows"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[ApplicationWorkflowStatus] = mapped_column(
        enum_type(ApplicationWorkflowStatus, "application_workflow_status", create_type=False),
        nullable=False,
        server_default=ApplicationWorkflowStatus.ACTIVE.value,
    )
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    application: Mapped[Application] = relationship("Application")
    template: Mapped[WorkflowTemplate] = relationship("WorkflowTemplate")
    steps: Mapped[list[ApplicationWorkflowStep]] = relationship(
        back_populates="application_workflow",
        cascade="all, delete-orphan",
        order_by="ApplicationWorkflowStep.order",
    )

    __table_args__ = (
        Index("idx_application_workflows_template_id", "template_id"),
        Index("idx_application_workflows_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<ApplicationWorkflow id={self.id} application_id={self.application_id}>"


class ApplicationWorkflowStep(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "application_workflow_steps"

    application_workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("application_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_stages.id", ondelete="SET NULL"))
    stage_name_snapshot: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[WorkflowStepStatus] = mapped_column(
        enum_type(WorkflowStepStatus, "workflow_step_status", create_type=False),
        nullable=False,
        server_default=WorkflowStepStatus.PENDING.value,
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    application_workflow: Mapped[ApplicationWorkflow] = relationship(back_populates="steps")
    stage: Mapped[WorkflowStage | None] = relationship("WorkflowStage")
    assignee: Mapped[User | None] = relationship("User", foreign_keys=[assigned_to])
    updater: Mapped[User | None] = relationship("User", foreign_keys=[updated_by])
    activities: Mapped[list[WorkflowStepActivity]] = relationship(
        back_populates="step",
        cascade="all, delete-orphan",
        order_by="WorkflowStepActivity.created_at.desc()",
    )

    __table_args__ = (
        Index("idx_application_workflow_steps_application_workflow_id", "application_workflow_id"),
        Index("idx_application_workflow_steps_stage_id", "stage_id"),
        Index("idx_application_workflow_steps_status", "status"),
        Index("idx_application_workflow_steps_assigned_to", "assigned_to"),
    )

    def __repr__(self) -> str:
        return f"<ApplicationWorkflowStep id={self.id} status={self.status}>"


class WorkflowStepActivity(Base, UUIDPKMixin):
    __tablename__ = "workflow_step_activities"

    step_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("application_workflow_steps.id", ondelete="CASCADE"),
        nullable=False,
    )
    activity_type: Mapped[WorkflowActivityType] = mapped_column(
        enum_type(WorkflowActivityType, "workflow_activity_type", create_type=False),
        nullable=False,
    )
    performed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    old_status: Mapped[WorkflowStepStatus | None] = mapped_column(
        enum_type(WorkflowStepStatus, "workflow_step_status", create_type=False)
    )
    new_status: Mapped[WorkflowStepStatus | None] = mapped_column(
        enum_type(WorkflowStepStatus, "workflow_step_status", create_type=False)
    )
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    step: Mapped[ApplicationWorkflowStep] = relationship(back_populates="activities")
    performer: Mapped[User | None] = relationship("User")

    __table_args__ = (
        Index("idx_workflow_step_activities_step_id", "step_id"),
        Index("idx_workflow_step_activities_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<WorkflowStepActivity id={self.id} type={self.activity_type}>"


class ApplicationChecklistItem(Base, UUIDPKMixin, TimestampMixin):
    """Per-application document checklist.

    Already exactly what the student portal's "what do I still owe you" screen
    needs — Phase 5 exposes it read-only under /student/me rather than building
    a second checklist.
    """

    __tablename__ = "application_checklist_items"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_stages.id", ondelete="SET NULL"))
    document_type: Mapped[DocumentType | None] = mapped_column(
        enum_type(DocumentType, "document_type", create_type=False)
    )
    custom_label: Mapped[str | None] = mapped_column(String(150))
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    status: Mapped[ChecklistItemStatus] = mapped_column(
        enum_type(ChecklistItemStatus, "checklist_item_status", create_type=False),
        nullable=False,
        server_default=ChecklistItemStatus.PENDING.value,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship("Application")
    stage: Mapped[WorkflowStage | None] = relationship("WorkflowStage")
    document: Mapped[Document | None] = relationship("Document")

    __table_args__ = (
        Index("idx_application_checklist_items_application_id", "application_id"),
        Index("idx_application_checklist_items_stage_id", "stage_id"),
        Index("idx_application_checklist_items_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<ApplicationChecklistItem id={self.id} status={self.status}>"
