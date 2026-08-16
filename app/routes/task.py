from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import require_role, require_staff
from ..api.exceptions import NotFoundException
from ..models import User
from ..models.enums import UserRole
from ..schemas.task import TaskCreate, TaskList, TaskRead, TaskUpdate
from ..services.task_service import TaskService, get_task_service

router = APIRouter(prefix="/tasks", tags=["Tasks"])

# Tasks are internal staff work items — students never see this surface at all,
# so the blanket staff gate is the right shape here.
_STAFF = require_staff


@router.get("", response_model=TaskList, summary="List tasks")
async def list_tasks(
    page: int = 1,
    limit: int = 20,
    assigned_to: UUID | None = None,
    assigned_by: UUID | None = None,
    student_id: UUID | None = None,
    lead_id: UUID | None = None,
    application_id: UUID | None = None,
    status: str | None = None,
    priority: str | None = None,
    task_type: str | None = None,
    search: str | None = None,
    service: TaskService = Depends(get_task_service),
    user: User = Depends(_STAFF),
) -> TaskList:
    tasks, total = await service.list_tasks(
        page,
        limit,
        assigned_to=assigned_to,
        assigned_by=assigned_by,
        student_id=student_id,
        lead_id=lead_id,
        application_id=application_id,
        status=status,
        priority=priority,
        task_type=task_type,
        search=search,
    )
    return TaskList(
        items=[TaskRead.model_validate(t) for t in tasks],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/{task_id}", response_model=TaskRead, summary="Get task")
async def get_task(
    task_id: UUID,
    service: TaskService = Depends(get_task_service),
    user: User = Depends(_STAFF),
) -> TaskRead:
    task = await service.get_task(task_id)
    if task is None:
        raise NotFoundException("Task not found")
    return TaskRead.model_validate(task)


@router.post("", response_model=TaskRead, summary="Create task")
async def create_task(
    payload: TaskCreate,
    service: TaskService = Depends(get_task_service),
    user: User = Depends(_STAFF),
) -> TaskRead:
    """Staff only.

    ED360 guards this with a bare `get_current_user`, so a student can assign
    work to any staff member — and set `assigned_by` to someone else while doing
    it. Here the delegator is taken from the token.
    """
    data = payload.model_dump()
    data["assigned_by"] = user.id
    return TaskRead.model_validate(await service.create_task(data))


@router.patch("/{task_id}", response_model=TaskRead, summary="Update task")
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    service: TaskService = Depends(get_task_service),
    user: User = Depends(_STAFF),
) -> TaskRead:
    task = await service.get_task(task_id)
    if task is None:
        raise NotFoundException("Task not found")
    updated = await service.update_task(task, payload.model_dump(exclude_unset=True))
    return TaskRead.model_validate(updated)


@router.delete("/{task_id}", response_model=TaskRead, summary="Delete task")
async def delete_task(
    task_id: UUID,
    service: TaskService = Depends(get_task_service),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> TaskRead:
    task = await service.get_task(task_id)
    if task is None:
        raise NotFoundException("Task not found")
    return TaskRead.model_validate(await service.delete_task(task))
