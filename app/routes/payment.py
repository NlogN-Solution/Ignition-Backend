from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ..api.auth import require_role
from ..api.exceptions import ForbiddenException, NotFoundException
from ..models import User
from ..models.enums import UserRole
from ..schemas.payment import PaymentCreate, PaymentList, PaymentRead, PaymentUpdate
from ..services.payment_service import PaymentService, get_payment_service

router = APIRouter(prefix="/payments", tags=["Payments"])

_VIEW_ROLES = require_role(UserRole.ADMIN, UserRole.FINANCE, UserRole.STUDENT)
_MANAGE_ROLES = require_role(UserRole.ADMIN, UserRole.FINANCE)


@router.get("", response_model=PaymentList, summary="List payments")
async def list_payments(
    page: int = 1,
    limit: int = 20,
    student_id: UUID | None = None,
    application_id: UUID | None = None,
    status: str | None = None,
    payment_method: str | None = None,
    service: PaymentService = Depends(get_payment_service),
    user: User = Depends(_VIEW_ROLES),
) -> PaymentList:
    if user.role is UserRole.STUDENT:
        student_id = user.id

    payments, total = await service.list_payments(
        page,
        limit,
        student_id=student_id,
        application_id=application_id,
        status=status,
        payment_method=payment_method,
    )
    return PaymentList(
        items=[PaymentRead.model_validate(p) for p in payments],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/{payment_id}", response_model=PaymentRead, summary="Get payment")
async def get_payment(
    payment_id: UUID,
    service: PaymentService = Depends(get_payment_service),
    user: User = Depends(_VIEW_ROLES),
) -> PaymentRead:
    payment = await service.get_payment(payment_id)
    if payment is None:
        raise NotFoundException("Payment not found")
    if user.role is UserRole.STUDENT and payment.student_id != user.id:
        raise ForbiddenException("Forbidden")
    return PaymentRead.model_validate(payment)


@router.post("", response_model=PaymentRead, summary="Create payment")
async def create_payment(
    payload: PaymentCreate,
    service: PaymentService = Depends(get_payment_service),
    user: User = Depends(_MANAGE_ROLES),
) -> PaymentRead:
    data = payload.model_dump()
    # Who recorded the payment is an audit fact, so it comes from the token
    # rather than the body ED360 accepts it in.
    data["created_by"] = user.id
    return PaymentRead.model_validate(await service.create_payment(data))


@router.patch("/{payment_id}", response_model=PaymentRead, summary="Update payment")
async def update_payment(
    payment_id: UUID,
    payload: PaymentUpdate,
    service: PaymentService = Depends(get_payment_service),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> PaymentRead:
    payment = await service.get_payment(payment_id)
    if payment is None:
        raise NotFoundException("Payment not found")
    updated = await service.update_payment(payment, payload.model_dump(exclude_unset=True))
    return PaymentRead.model_validate(updated)


@router.delete("/{payment_id}", response_model=PaymentRead, summary="Delete payment")
async def delete_payment(
    payment_id: UUID,
    service: PaymentService = Depends(get_payment_service),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> PaymentRead:
    payment = await service.get_payment(payment_id)
    if payment is None:
        raise NotFoundException("Payment not found")
    return PaymentRead.model_validate(await service.delete_payment(payment))
