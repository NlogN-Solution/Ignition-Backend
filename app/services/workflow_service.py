from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import (
    Application,
    ApplicationChecklistItem,
    ApplicationWorkflow,
    ApplicationWorkflowStep,
    Program,
    University,
    WorkflowStage,
    WorkflowStageDocumentRequirement,
    WorkflowStepActivity,
    WorkflowTemplate,
)
from ..models.enums import ApplicationWorkflowStatus, ChecklistItemStatus, WorkflowActivityType, WorkflowStepStatus


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or uuid4().hex[:8]


class WorkflowTemplateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_templates(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        country_id: UUID | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[tuple[WorkflowTemplate, int]], int]:
        query = select(WorkflowTemplate)
        count_query = select(func.count()).select_from(WorkflowTemplate)

        if search:
            search_value = f"%{search.strip().lower()}%"
            search_filter = func.lower(WorkflowTemplate.name).like(search_value)
            query = query.where(search_filter)
            count_query = count_query.where(search_filter)

        if country_id:
            query = query.where(WorkflowTemplate.country_id == country_id)
            count_query = count_query.where(WorkflowTemplate.country_id == country_id)

        if is_active is not None:
            query = query.where(WorkflowTemplate.is_active == is_active)
            count_query = count_query.where(WorkflowTemplate.is_active == is_active)

        total = await self.session.scalar(count_query) or 0
        query = query.order_by(WorkflowTemplate.name).offset((page - 1) * limit).limit(limit)
        result = await self.session.execute(query)
        templates = result.scalars().all()

        stage_counts: dict[UUID, int] = {}
        if templates:
            count_result = await self.session.execute(
                select(WorkflowStage.template_id, func.count())
                .where(WorkflowStage.template_id.in_([t.id for t in templates]))
                .group_by(WorkflowStage.template_id)
            )
            # `dict()` over Rows: ruff rejects the equivalent comprehension (C416)
            # while mypy cannot see that a 2-tuple Row unpacks — same trade-off as
            # DepartmentService.employee_counts.
            stage_counts = dict(count_result.all())  # type: ignore[arg-type]

        return [(t, stage_counts.get(t.id, 0)) for t in templates], total

    async def get_template(self, template_id: UUID, with_stages: bool = False) -> WorkflowTemplate | None:
        query = select(WorkflowTemplate).where(WorkflowTemplate.id == template_id)
        if with_stages:
            query = query.options(
                selectinload(WorkflowTemplate.stages).selectinload(WorkflowStage.document_requirements)
            )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_template(self, data: dict[str, Any], created_by: UUID | None = None) -> WorkflowTemplate:
        payload = data.copy()
        if not payload.get("slug"):
            payload["slug"] = slugify(payload["name"])
        template = WorkflowTemplate(**payload, created_by=created_by)
        self.session.add(template)
        await self.session.commit()
        await self.session.refresh(template)
        return template

    async def update_template(self, template: WorkflowTemplate, data: dict[str, Any]) -> WorkflowTemplate:
        for key, value in data.items():
            if value is not None:
                setattr(template, key, value)
        await self.session.commit()
        await self.session.refresh(template)
        return template

    async def delete_template(self, template: WorkflowTemplate) -> WorkflowTemplate:
        await self.session.delete(template)
        await self.session.commit()
        return template

    async def duplicate_template(self, template: WorkflowTemplate, created_by: UUID | None = None) -> WorkflowTemplate:
        source = await self.get_template(template.id, with_stages=True)
        assert source is not None

        clone = WorkflowTemplate(
            name=f"{source.name} (copy)",
            slug=slugify(f"{source.name}-copy-{uuid4().hex[:6]}"),
            description=source.description,
            country_id=source.country_id,
            is_default=False,
            is_active=source.is_active,
            created_by=created_by,
        )
        self.session.add(clone)
        await self.session.flush()

        for stage in source.stages:
            stage_clone = WorkflowStage(
                template_id=clone.id,
                key=stage.key,
                name=stage.name,
                description=stage.description,
                category=stage.category,
                color=stage.color,
                icon=stage.icon,
                order=stage.order,
                is_active=stage.is_active,
            )
            self.session.add(stage_clone)
            await self.session.flush()

            for req in stage.document_requirements:
                self.session.add(
                    WorkflowStageDocumentRequirement(
                        stage_id=stage_clone.id,
                        document_type=req.document_type,
                        custom_label=req.custom_label,
                        is_required=req.is_required,
                    )
                )

        await self.session.commit()
        await self.session.refresh(clone)
        return clone

    async def create_stage(self, template_id: UUID, data: dict[str, Any]) -> WorkflowStage:
        max_order = await self.session.scalar(
            select(func.coalesce(func.max(WorkflowStage.order), -1)).where(WorkflowStage.template_id == template_id)
        )
        # NOT `(max_order or -1) + 1`: when the highest existing order is 0 that
        # expression is falsy and yields -1, so the second stage collides at 0
        # with the first — and every stage after it. `coalesce` above already
        # returns -1 for an empty template.
        next_order = (max_order if max_order is not None else -1) + 1
        stage = WorkflowStage(template_id=template_id, order=next_order, **data)
        self.session.add(stage)
        await self.session.commit()
        # `refresh` leaves `document_requirements` unloaded, and serializing the
        # stage would then lazy-load it outside the async greenlet and raise
        # MissingGreenlet. Re-read through `get_stage`, which eager-loads it.
        loaded = await self.get_stage(stage.id)
        assert loaded is not None
        return loaded

    async def get_stage(self, stage_id: UUID) -> WorkflowStage | None:
        result = await self.session.execute(
            select(WorkflowStage)
            .where(WorkflowStage.id == stage_id)
            .options(selectinload(WorkflowStage.document_requirements))
        )
        return result.scalar_one_or_none()

    async def update_stage(self, stage: WorkflowStage, data: dict[str, Any]) -> WorkflowStage:
        for key, value in data.items():
            if value is not None:
                setattr(stage, key, value)
        await self.session.commit()
        loaded = await self.get_stage(stage.id)
        assert loaded is not None
        return loaded

    async def delete_stage(self, stage: WorkflowStage) -> WorkflowStage:
        await self.session.delete(stage)
        await self.session.commit()
        return stage

    async def reorder_stages(self, template_id: UUID, stage_ids: list[UUID]) -> list[WorkflowStage]:
        result = await self.session.execute(
            select(WorkflowStage)
            .where(WorkflowStage.template_id == template_id)
            .options(selectinload(WorkflowStage.document_requirements))
        )
        stages_by_id = {s.id: s for s in result.scalars().all()}
        changed: list[WorkflowStage] = []
        for index, stage_id in enumerate(stage_ids):
            stage = stages_by_id.get(stage_id)
            if stage is not None and stage.order != index:
                stage.order = index
                changed.append(stage)
        await self.session.commit()
        # `updated_at` has a server-side onupdate default — refresh the rows that were
        # actually written so the response reflects the new value instead of triggering
        # an implicit (and here, broken-under-async) lazy reload during serialization.
        for stage in changed:
            await self.session.refresh(stage, attribute_names=["updated_at"])
        return sorted(stages_by_id.values(), key=lambda s: s.order)

    async def create_requirement(self, stage_id: UUID, data: dict[str, Any]) -> WorkflowStageDocumentRequirement:
        requirement = WorkflowStageDocumentRequirement(stage_id=stage_id, **data)
        self.session.add(requirement)
        await self.session.commit()
        await self.session.refresh(requirement)
        return requirement

    async def get_requirement(self, requirement_id: UUID) -> WorkflowStageDocumentRequirement | None:
        result = await self.session.execute(
            select(WorkflowStageDocumentRequirement).where(WorkflowStageDocumentRequirement.id == requirement_id)
        )
        return result.scalar_one_or_none()

    async def update_requirement(
        self, requirement: WorkflowStageDocumentRequirement, data: dict[str, Any]
    ) -> WorkflowStageDocumentRequirement:
        for key, value in data.items():
            if value is not None:
                setattr(requirement, key, value)
        await self.session.commit()
        await self.session.refresh(requirement)
        return requirement

    async def delete_requirement(
        self, requirement: WorkflowStageDocumentRequirement
    ) -> WorkflowStageDocumentRequirement:
        await self.session.delete(requirement)
        await self.session.commit()
        return requirement


class ApplicationWorkflowService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_application(self, application_id: UUID) -> ApplicationWorkflow | None:
        result = await self.session.execute(
            select(ApplicationWorkflow)
            .where(ApplicationWorkflow.application_id == application_id)
            .options(selectinload(ApplicationWorkflow.steps))
        )
        return result.scalar_one_or_none()

    async def _resolve_template(self, application: Application, template_id: UUID | None) -> WorkflowTemplate:
        if template_id:
            template = await self.session.get(WorkflowTemplate, template_id)
            if template is None:
                raise ValueError("Workflow template not found")
            return template

        country_id: UUID | None = None
        program = await self.session.get(Program, application.program_id)
        if program is not None:
            university = await self.session.get(University, program.university_id)
            if university is not None:
                country_id = university.country_id

        if country_id:
            result = await self.session.execute(
                select(WorkflowTemplate).where(
                    WorkflowTemplate.country_id == country_id, WorkflowTemplate.is_active.is_(True)
                )
            )
            template = result.scalars().first()
            if template is not None:
                return template

        result = await self.session.execute(
            select(WorkflowTemplate).where(WorkflowTemplate.is_default.is_(True), WorkflowTemplate.is_active.is_(True))
        )
        template = result.scalars().first()
        if template is not None:
            return template

        result = await self.session.execute(select(WorkflowTemplate).where(WorkflowTemplate.is_active.is_(True)))
        template = result.scalars().first()
        if template is None:
            raise ValueError("No active workflow template is available to instantiate")
        return template

    async def instantiate(
        self, application_id: UUID, template_id: UUID | None, performed_by: UUID | None
    ) -> ApplicationWorkflow:
        existing = await self.get_by_application(application_id)
        if existing is not None:
            raise ValueError("This application already has a workflow")

        application = await self.session.get(Application, application_id)
        if application is None:
            raise ValueError("Application not found")

        template = await self._resolve_template(application, template_id)

        stages_result = await self.session.execute(
            select(WorkflowStage)
            .where(WorkflowStage.template_id == template.id, WorkflowStage.is_active.is_(True))
            .order_by(WorkflowStage.order)
            .options(selectinload(WorkflowStage.document_requirements))
        )
        stages = stages_result.scalars().all()
        if not stages:
            raise ValueError("This workflow template has no active stages")

        workflow = ApplicationWorkflow(
            application_id=application_id,
            template_id=template.id,
        )
        self.session.add(workflow)
        await self.session.flush()

        now = datetime.now(UTC)
        for index, stage in enumerate(stages):
            step = ApplicationWorkflowStep(
                application_workflow_id=workflow.id,
                stage_id=stage.id,
                stage_name_snapshot=stage.name,
                status=WorkflowStepStatus.CURRENT if index == 0 else WorkflowStepStatus.PENDING,
                started_at=now if index == 0 else None,
                order=index,
            )
            self.session.add(step)
            await self.session.flush()

            self.session.add(
                WorkflowStepActivity(
                    step_id=step.id,
                    activity_type=WorkflowActivityType.CREATED,
                    performed_by=performed_by,
                    new_status=step.status,
                )
            )

            for req in stage.document_requirements:
                self.session.add(
                    ApplicationChecklistItem(
                        application_id=application_id,
                        stage_id=stage.id,
                        document_type=req.document_type,
                        custom_label=req.custom_label,
                        is_required=req.is_required,
                    )
                )

        await self.session.commit()
        return await self.get_by_application(application_id)  # type: ignore[return-value]

    async def get_step(self, step_id: UUID) -> ApplicationWorkflowStep | None:
        result = await self.session.execute(
            select(ApplicationWorkflowStep).where(ApplicationWorkflowStep.id == step_id)
        )
        return result.scalar_one_or_none()

    async def update_step(
        self, step: ApplicationWorkflowStep, data: dict[str, Any], performed_by: UUID | None
    ) -> ApplicationWorkflowStep:
        now = datetime.now(UTC)
        old_status = step.status
        new_status = data.get("status")
        new_assignee = data.get("assigned_to")
        new_notes = data.get("notes")

        if new_status is not None and new_status != old_status:
            step.status = new_status
            if new_status == WorkflowStepStatus.CURRENT and step.started_at is None:
                step.started_at = now
            if new_status in (
                WorkflowStepStatus.COMPLETED,
                WorkflowStepStatus.FAILED,
                WorkflowStepStatus.SKIPPED,
                WorkflowStepStatus.CANCELLED,
            ):
                step.completed_at = now
            step.updated_by = performed_by
            self.session.add(
                WorkflowStepActivity(
                    step_id=step.id,
                    activity_type=WorkflowActivityType.STATUS_CHANGED,
                    performed_by=performed_by,
                    old_status=old_status,
                    new_status=new_status,
                )
            )

        if new_assignee is not None and new_assignee != step.assigned_to:
            step.assigned_to = new_assignee
            step.updated_by = performed_by
            self.session.add(
                WorkflowStepActivity(
                    step_id=step.id,
                    activity_type=WorkflowActivityType.ASSIGNED,
                    performed_by=performed_by,
                )
            )

        if new_notes is not None and new_notes != step.notes:
            step.notes = new_notes
            step.updated_by = performed_by
            self.session.add(
                WorkflowStepActivity(
                    step_id=step.id,
                    activity_type=WorkflowActivityType.NOTE_ADDED,
                    performed_by=performed_by,
                    comment=new_notes,
                )
            )

        await self.session.commit()

        if new_status == WorkflowStepStatus.COMPLETED:
            await self._advance_workflow(step.application_workflow_id, step.order)

        await self.session.refresh(step)
        return step

    async def _advance_workflow(self, application_workflow_id: UUID, completed_order: int) -> None:
        result = await self.session.execute(
            select(ApplicationWorkflowStep)
            .where(ApplicationWorkflowStep.application_workflow_id == application_workflow_id)
            .order_by(ApplicationWorkflowStep.order)
        )
        steps = result.scalars().all()

        next_step = next(
            (s for s in steps if s.order > completed_order and s.status == WorkflowStepStatus.PENDING), None
        )
        if next_step is not None:
            next_step.status = WorkflowStepStatus.CURRENT
            next_step.started_at = datetime.now(UTC)
            self.session.add(
                WorkflowStepActivity(
                    step_id=next_step.id,
                    activity_type=WorkflowActivityType.STATUS_CHANGED,
                    old_status=WorkflowStepStatus.PENDING,
                    new_status=WorkflowStepStatus.CURRENT,
                )
            )

        terminal_statuses = {
            WorkflowStepStatus.COMPLETED,
            WorkflowStepStatus.FAILED,
            WorkflowStepStatus.SKIPPED,
            WorkflowStepStatus.CANCELLED,
        }
        if all(s.status in terminal_statuses for s in steps):
            workflow = await self.session.get(ApplicationWorkflow, application_workflow_id)
            if workflow is not None:
                workflow.status = ApplicationWorkflowStatus.COMPLETED
                workflow.completed_at = datetime.now(UTC)

        await self.session.commit()

    async def add_comment(
        self, step: ApplicationWorkflowStep, comment: str, performed_by: UUID | None
    ) -> WorkflowStepActivity:
        activity = WorkflowStepActivity(
            step_id=step.id,
            activity_type=WorkflowActivityType.COMMENT,
            performed_by=performed_by,
            comment=comment,
        )
        self.session.add(activity)
        await self.session.commit()
        await self.session.refresh(activity)
        return activity

    async def list_activities(self, step_id: UUID) -> list[WorkflowStepActivity]:
        result = await self.session.execute(
            select(WorkflowStepActivity)
            .where(WorkflowStepActivity.step_id == step_id)
            .order_by(WorkflowStepActivity.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_steps(
        self,
        page: int,
        limit: int,
        stage_id: UUID | None = None,
        status: WorkflowStepStatus | None = None,
        template_id: UUID | None = None,
        assigned_to: UUID | None = None,
        application_id: UUID | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        query = (
            select(
                ApplicationWorkflowStep,
                ApplicationWorkflow.application_id,
                ApplicationWorkflow.template_id,
                WorkflowStage.key.label("stage_key"),
            )
            .join(ApplicationWorkflow, ApplicationWorkflow.id == ApplicationWorkflowStep.application_workflow_id)
            .outerjoin(WorkflowStage, WorkflowStage.id == ApplicationWorkflowStep.stage_id)
        )
        count_query = (
            select(func.count())
            .select_from(ApplicationWorkflowStep)
            .join(ApplicationWorkflow, ApplicationWorkflow.id == ApplicationWorkflowStep.application_workflow_id)
        )

        if stage_id:
            query = query.where(ApplicationWorkflowStep.stage_id == stage_id)
            count_query = count_query.where(ApplicationWorkflowStep.stage_id == stage_id)
        if status:
            query = query.where(ApplicationWorkflowStep.status == status)
            count_query = count_query.where(ApplicationWorkflowStep.status == status)
        if assigned_to:
            query = query.where(ApplicationWorkflowStep.assigned_to == assigned_to)
            count_query = count_query.where(ApplicationWorkflowStep.assigned_to == assigned_to)
        if template_id:
            query = query.where(ApplicationWorkflow.template_id == template_id)
            count_query = count_query.where(ApplicationWorkflow.template_id == template_id)
        if application_id:
            query = query.where(ApplicationWorkflow.application_id == application_id)
            count_query = count_query.where(ApplicationWorkflow.application_id == application_id)

        total = await self.session.scalar(count_query) or 0
        query = query.order_by(ApplicationWorkflowStep.order).offset((page - 1) * limit).limit(limit)
        result = await self.session.execute(query)

        items = []
        for step, application_id_val, template_id_val, stage_key in result.all():
            items.append(
                {
                    "id": step.id,
                    "application_id": application_id_val,
                    "application_workflow_id": step.application_workflow_id,
                    "template_id": template_id_val,
                    "stage_id": step.stage_id,
                    "stage_key": stage_key,
                    "stage_name_snapshot": step.stage_name_snapshot,
                    "status": step.status,
                    "assigned_to": step.assigned_to,
                    "order": step.order,
                    "created_at": step.created_at,
                    "updated_at": step.updated_at,
                }
            )
        return items, total


class ChecklistService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_items(self, application_id: UUID) -> list[ApplicationChecklistItem]:
        result = await self.session.execute(
            select(ApplicationChecklistItem)
            .where(ApplicationChecklistItem.application_id == application_id)
            .order_by(ApplicationChecklistItem.created_at)
        )
        return list(result.scalars().all())

    async def get_item(self, item_id: UUID) -> ApplicationChecklistItem | None:
        result = await self.session.execute(
            select(ApplicationChecklistItem).where(ApplicationChecklistItem.id == item_id)
        )
        return result.scalar_one_or_none()

    async def create_item(self, application_id: UUID, data: dict[str, Any]) -> ApplicationChecklistItem:
        item = ApplicationChecklistItem(application_id=application_id, **data)
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def update_item(self, item: ApplicationChecklistItem, data: dict[str, Any]) -> ApplicationChecklistItem:
        for key, value in data.items():
            if value is not None:
                setattr(item, key, value)
        if data.get("document_id") and item.status == ChecklistItemStatus.PENDING:
            item.status = ChecklistItemStatus.SUBMITTED
        await self.session.commit()
        await self.session.refresh(item)
        return item
