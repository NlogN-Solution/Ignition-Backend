from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import require_role, require_staff
from ..api.exceptions import BadRequestException, NotFoundException
from ..models import Department, User
from ..models.enums import UserRole
from ..schemas.department import DepartmentCreate, DepartmentList, DepartmentRead, DepartmentUpdate
from ..services.department_service import DepartmentService, get_department_service

router = APIRouter(prefix="/departments", tags=["Departments"])


def _to_read(department: Department, employee_count: int) -> DepartmentRead:
    return DepartmentRead(
        id=department.id,
        name=department.name,
        description=department.description,
        manager_id=department.manager_id,
        employee_count=employee_count,
        created_at=department.created_at,
        updated_at=department.updated_at,
    )


@router.get("", response_model=DepartmentList, summary="List departments")
async def list_departments(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    service: DepartmentService = Depends(get_department_service),
    _: User = Depends(require_staff),
) -> DepartmentList:
    departments, total = await service.list(page, limit, search=search)
    counts = await service.employee_counts([department.id for department in departments])
    return DepartmentList(
        items=[_to_read(department, counts.get(department.id, 0)) for department in departments],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/{department_id}", response_model=DepartmentRead, summary="Get department")
async def get_department(
    department_id: UUID,
    service: DepartmentService = Depends(get_department_service),
    _: User = Depends(require_staff),
) -> DepartmentRead:
    department = await service.get(department_id)
    if department is None:
        raise NotFoundException("Department not found")
    counts = await service.employee_counts([department.id])
    return _to_read(department, counts.get(department.id, 0))


@router.post("", response_model=DepartmentRead, summary="Create department")
async def create_department(
    payload: DepartmentCreate,
    service: DepartmentService = Depends(get_department_service),
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER)),
) -> DepartmentRead:
    department = await service.create(payload.model_dump())
    return _to_read(department, 0)


@router.patch("/{department_id}", response_model=DepartmentRead, summary="Update department")
async def update_department(
    department_id: UUID,
    payload: DepartmentUpdate,
    service: DepartmentService = Depends(get_department_service),
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER)),
) -> DepartmentRead:
    department = await service.get(department_id)
    if department is None:
        raise NotFoundException("Department not found")
    updated = await service.update(department, payload.model_dump(exclude_unset=True))
    counts = await service.employee_counts([updated.id])
    return _to_read(updated, counts.get(updated.id, 0))


@router.delete("/{department_id}", response_model=DepartmentRead, summary="Delete department")
async def delete_department(
    department_id: UUID,
    service: DepartmentService = Depends(get_department_service),
    _: User = Depends(require_role(UserRole.ADMIN, UserRole.MANAGER)),
) -> DepartmentRead:
    department = await service.get(department_id)
    if department is None:
        raise NotFoundException("Department not found")

    # `employee_profiles.department_id` is ON DELETE SET NULL, so deleting a
    # populated department would silently orphan its staff rather than fail.
    # Refuse and make the caller reassign.
    counts = await service.employee_counts([department.id])
    employee_count = counts.get(department.id, 0)
    if employee_count > 0:
        raise BadRequestException(
            f"Cannot delete a department with {employee_count} employee(s) assigned. Reassign them first."
        )

    return _to_read(await service.delete(department), 0)
