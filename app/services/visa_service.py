"""The visa and pre-departure journey.

Phase 6. There is no `create` here — a `VisaCase` is opened by staff when an
application reaches the visa stage, the same way an `Application` is opened by
a counsellor rather than a student. This service is the student's read and
limited-write surface over a case staff already created.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..api.exceptions import NotFoundException
from ..models import (
    DepartureChecklistItem,
    User,
    VisaAppointment,
    VisaCase,
    VisaFee,
)
from ..models.enums import VisaAppointmentStatus


class VisaService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_case(self, student: User) -> VisaCase:
        """The student's one visa case, fully loaded.

        Not all students have one — a case exists once staff open it, so a
        student earlier in their journey gets a 404 rather than an empty case,
        matching how `/me/profile` behaves before a profile is created.
        """
        case = await self.session.scalar(
            select(VisaCase)
            .where(VisaCase.student_id == student.id)
            .options(
                selectinload(VisaCase.stages),
                selectinload(VisaCase.appointments),
                selectinload(VisaCase.document_requirements),
                selectinload(VisaCase.fees),
                selectinload(VisaCase.departure_checklist),
            )
        )
        if case is None:
            raise NotFoundException("No visa case yet")
        return case

    async def _owned_appointment(self, student: User, appointment_id: UUID) -> VisaAppointment:
        appointment = await self.session.scalar(
            select(VisaAppointment)
            .join(VisaCase, VisaAppointment.case_id == VisaCase.id)
            .where(VisaAppointment.id == appointment_id, VisaCase.student_id == student.id)
        )
        if appointment is None:
            raise NotFoundException("Visa appointment not found")
        return appointment

    async def book_appointment(self, student: User, appointment_id: UUID, scheduled_at: datetime) -> VisaAppointment:
        appointment = await self._owned_appointment(student, appointment_id)
        appointment.scheduled_at = scheduled_at
        appointment.status = VisaAppointmentStatus.BOOKED
        await self.session.commit()
        await self.session.refresh(appointment)
        return appointment

    async def cancel_appointment(self, student: User, appointment_id: UUID) -> VisaAppointment:
        appointment = await self._owned_appointment(student, appointment_id)
        appointment.status = VisaAppointmentStatus.CANCELLED
        await self.session.commit()
        await self.session.refresh(appointment)
        return appointment

    async def _owned_fee(self, student: User, fee_id: UUID) -> VisaFee:
        fee = await self.session.scalar(
            select(VisaFee)
            .join(VisaCase, VisaFee.case_id == VisaCase.id)
            .where(VisaFee.id == fee_id, VisaCase.student_id == student.id)
        )
        if fee is None:
            raise NotFoundException("Visa fee not found")
        return fee

    async def mark_fee_paid(self, student: User, fee_id: UUID, paid: bool) -> VisaFee:
        """Self-reported — there is no gateway behind a fee paid straight to a
        foreign consulate, so the student's word is the only record."""
        fee = await self._owned_fee(student, fee_id)
        fee.paid = paid
        fee.paid_at = datetime.now(UTC) if paid else None
        await self.session.commit()
        await self.session.refresh(fee)
        return fee

    async def _owned_departure_item(self, student: User, item_id: UUID) -> DepartureChecklistItem:
        item = await self.session.scalar(
            select(DepartureChecklistItem)
            .join(VisaCase, DepartureChecklistItem.case_id == VisaCase.id)
            .where(DepartureChecklistItem.id == item_id, VisaCase.student_id == student.id)
        )
        if item is None:
            raise NotFoundException("Departure checklist item not found")
        return item

    async def set_departure_item_completed(
        self, student: User, item_id: UUID, completed: bool
    ) -> DepartureChecklistItem:
        item = await self._owned_departure_item(student, item_id)
        item.completed_at = datetime.now(UTC) if completed else None
        await self.session.commit()
        await self.session.refresh(item)
        return item


async def get_visa_service(session: AsyncSession = Depends(get_db_session)) -> VisaService:
    return VisaService(session)
