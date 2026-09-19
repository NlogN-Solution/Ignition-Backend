"""Priority tasks: what a counsellor wants a student to do next.

Staff set them here; they land on the student's journey checklist flagged
`is_priority`, and the student dashboard's "Priority tasks" leads with them.
The student can tick one off (through their own `/student/me/checklist`), but
cannot reword or delete it — it is not theirs to edit, the same rule the seeded
journey follows.

Staff may also complete, re-date, reword or remove one here: a counsellor who
set the wrong task has to be able to take it back.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import require_role
from ..api.deps import get_db_session
from ..api.exceptions import NotFoundException
from ..models import StudentChecklistItem, User
from ..models.enums import NotificationType, UserRole
from ..schemas.checklist import PriorityTaskCreate, PriorityTaskRead, PriorityTaskUpdate
from ..services.checklist_service import ChecklistService, get_checklist_service
from ..services.notification_service import NotificationService, get_notification_service

router = APIRouter(prefix="/students/{student_id}/priority-tasks", tags=["Priority tasks"])

_STAFF = require_role(UserRole.ADMIN, UserRole.COUNSELLOR, UserRole.ADMISSIONS)


def _read(item: StudentChecklistItem) -> PriorityTaskRead:
    entry = PriorityTaskRead.model_validate(item)
    entry.is_complete = item.is_complete
    assigner = item.assigner
    if assigner is not None:
        entry.assigned_by_name = f"{assigner.first_name} {assigner.last_name or ''}".strip()
    return entry


async def _student(session: AsyncSession, student_id: UUID) -> User:
    student = await session.scalar(select(User).where(User.id == student_id, User.role == UserRole.STUDENT))
    if student is None:
        raise NotFoundException("Student not found")
    return student


async def _item(session: AsyncSession, student_id: UUID, item_id: UUID) -> StudentChecklistItem:
    item = await session.scalar(
        select(StudentChecklistItem).where(
            StudentChecklistItem.id == item_id,
            StudentChecklistItem.student_id == student_id,
            StudentChecklistItem.is_priority.is_(True),
        )
    )
    if item is None:
        raise NotFoundException("Priority task not found")
    return item


@router.get("", response_model=list[PriorityTaskRead], summary="A student's priority tasks")
async def list_priority_tasks(
    student_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    service: ChecklistService = Depends(get_checklist_service),
    _: User = Depends(_STAFF),
) -> list[PriorityTaskRead]:
    await _student(session, student_id)
    return [_read(item) for item in await service.list_priority(student_id)]


@router.post("", response_model=PriorityTaskRead, summary="Set a priority task for a student")
async def create_priority_task(
    student_id: UUID,
    payload: PriorityTaskCreate,
    session: AsyncSession = Depends(get_db_session),
    service: ChecklistService = Depends(get_checklist_service),
    notifications: NotificationService = Depends(get_notification_service),
    user: User = Depends(_STAFF),
) -> PriorityTaskRead:
    student = await _student(session, student_id)
    item = await service.create_priority(
        student,
        title=payload.title,
        description=payload.description,
        due_date=payload.due_date,
        assigned_by=user.id,
    )
    # The student hears about it, not only sees it next time they open the
    # dashboard. A notification failing must not undo the task.
    try:
        await notifications.create_notification(
            {
                "user_id": student.id,
                "type": NotificationType.TASK,
                "title": "New task from your counsellor",
                "message": payload.title,
                "action_url": "/",
            }
        )
    except Exception:  # noqa: BLE001 — best effort, the task itself is saved
        await session.rollback()
    return _read(item)


@router.patch("/{item_id}", response_model=PriorityTaskRead, summary="Update a student's priority task")
async def update_priority_task(
    student_id: UUID,
    item_id: UUID,
    payload: PriorityTaskUpdate,
    session: AsyncSession = Depends(get_db_session),
    service: ChecklistService = Depends(get_checklist_service),
    _: User = Depends(_STAFF),
) -> PriorityTaskRead:
    student = await _student(session, student_id)
    item = await _item(session, student_id, item_id)
    fields = payload.model_dump(exclude_unset=True)
    for key in ("title", "description", "due_date"):
        if key in fields and not (key == "title" and fields[key] is None):
            setattr(item, key, fields[key])
    await session.commit()
    if fields.get("completed") is not None:
        item = await service.set_completed(student, item, fields["completed"])
    await session.refresh(item, attribute_names=["assigner"])
    return _read(item)


@router.delete("/{item_id}", summary="Remove a student's priority task")
async def delete_priority_task(
    student_id: UUID,
    item_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(_STAFF),
) -> dict[str, bool]:
    item = await _item(session, student_id, item_id)
    await session.delete(item)
    await session.commit()
    return {"success": True}
