from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..models.enums import PaymentMethod, PaymentStatus


class PaymentBase(BaseModel):
    student_id: UUID
    application_id: UUID | None = None
    amount: float
    currency: str | None = "NPR"
    payment_method: PaymentMethod
    status: PaymentStatus | None = PaymentStatus.PENDING
    transaction_reference: str | None = None
    payment_date: datetime | None = None
    remarks: str | None = None
    created_by: UUID | None = None


class PaymentCreate(PaymentBase):
    pass


class PaymentUpdate(BaseModel):
    """`created_by` is absent: it records who took the payment and is stamped
    from the authenticated user, not accepted from the request."""

    student_id: UUID | None = None
    application_id: UUID | None = None
    amount: float | None = None
    currency: str | None = None
    payment_method: PaymentMethod | None = None
    status: PaymentStatus | None = None
    transaction_reference: str | None = None
    payment_date: datetime | None = None
    remarks: str | None = None


class PaymentRead(PaymentBase):
    id: UUID
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class PaymentList(BaseModel):
    items: list[PaymentRead]
    total: int
    page: int
    limit: int
