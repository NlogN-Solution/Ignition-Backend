from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..core.events import PaymentCompleted, event_bus
from ..models import Payment
from ..models.enums import PaymentStatus
from .partial_update import reject_null_on_required


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_payment(self, payment_id: UUID) -> Payment | None:
        return await self.session.scalar(select(Payment).where(Payment.id == payment_id))

    async def list_payments(
        self,
        page: int,
        limit: int,
        student_id: UUID | None = None,
        application_id: UUID | None = None,
        status: str | None = None,
        payment_method: str | None = None,
    ) -> tuple[list[Payment], int]:
        conditions: list[ColumnElement[bool]] = []
        if student_id:
            conditions.append(Payment.student_id == student_id)
        if application_id:
            conditions.append(Payment.application_id == application_id)
        if status:
            conditions.append(Payment.status == status)
        if payment_method:
            conditions.append(Payment.payment_method == payment_method)

        query = select(Payment)
        count_query = select(func.count()).select_from(Payment)
        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        query = query.order_by(Payment.created_at.desc(), Payment.id).limit(limit).offset((page - 1) * limit)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def create_payment(self, data: dict[str, Any]) -> Payment:
        payment = Payment(**data)
        self.session.add(payment)
        await self.session.commit()
        await self.session.refresh(payment)
        await self._publish_if_completed(payment)
        return payment

    async def _publish_if_completed(self, payment: Payment) -> None:
        """Only a completed payment is news to the student.

        A pending or failed row is bookkeeping; telling someone "payment
        received" for either would be wrong.
        """
        if payment.status is not PaymentStatus.COMPLETED:
            return
        await event_bus.publish(
            PaymentCompleted(
                payment_id=payment.id,
                student_id=payment.student_id,
                amount=float(payment.amount),
            ),
            self.session,
        )

    async def update_payment(self, payment: Payment, data: dict[str, Any]) -> Payment:
        reject_null_on_required(Payment, data)
        was_completed = payment.status is PaymentStatus.COMPLETED
        for key, value in data.items():
            setattr(payment, key, value)
        await self.session.commit()
        await self.session.refresh(payment)
        # Only on the transition, so editing a completed payment's remarks does
        # not re-announce it.
        if not was_completed:
            await self._publish_if_completed(payment)
        return payment

    async def delete_payment(self, payment: Payment) -> Payment:
        await self.session.delete(payment)
        await self.session.commit()
        return payment


async def get_payment_service(session: AsyncSession = Depends(get_db_session)) -> PaymentService:
    return PaymentService(session)
