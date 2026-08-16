from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..core.events import AppointmentScheduled, event_bus
from ..models import Appointment
from .partial_update import reject_null_on_required
from .staff_resolution import resolve_assigned_counsellor_id, resolve_responsible_staff_ids


class AppointmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_appointment(self, appointment_id: UUID) -> Appointment | None:
        return await self.session.scalar(select(Appointment).where(Appointment.id == appointment_id))

    async def list_appointments(
        self,
        page: int,
        limit: int,
        student_id: UUID | None = None,
        counsellor_id: UUID | None = None,
        lead_id: UUID | None = None,
        appointment_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> tuple[list[Appointment], int]:
        conditions: list[ColumnElement[bool]] = []
        if student_id:
            conditions.append(Appointment.student_id == student_id)
        if counsellor_id:
            conditions.append(Appointment.counsellor_id == counsellor_id)
        if lead_id:
            conditions.append(Appointment.lead_id == lead_id)
        if appointment_type:
            conditions.append(Appointment.appointment_type == appointment_type)
        if status:
            conditions.append(Appointment.status == status)
        if search and search.strip():
            value = f"%{search.strip().lower()}%"
            conditions.append(
                or_(
                    func.lower(Appointment.title).like(value),
                    func.lower(Appointment.description).like(value),
                    func.lower(Appointment.location).like(value),
                )
            )

        query = select(Appointment)
        count_query = select(func.count()).select_from(Appointment)
        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        query = (
            query.order_by(Appointment.start_time.desc().nullslast(), Appointment.id)
            .limit(limit)
            .offset((page - 1) * limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def create_appointment(self, data: dict[str, Any]) -> Appointment:
        appointment = Appointment(**data)
        self.session.add(appointment)
        await self.session.commit()
        await self.session.refresh(appointment)
        await event_bus.publish(
            AppointmentScheduled(
                appointment_id=appointment.id,
                student_id=appointment.student_id,
                title=appointment.title,
                status=appointment.status.value if appointment.status else "",
                requested_by_student=appointment.created_by == appointment.student_id,
            ),
            self.session,
        )
        return appointment

    async def update_appointment(self, appointment: Appointment, data: dict[str, Any]) -> Appointment:
        reject_null_on_required(Appointment, data)
        for key, value in data.items():
            setattr(appointment, key, value)
        await self.session.commit()
        await self.session.refresh(appointment)
        return appointment

    async def delete_appointment(self, appointment: Appointment) -> Appointment:
        await self.session.delete(appointment)
        await self.session.commit()
        return appointment

    async def resolve_assigned_counsellor_id(self, student_id: UUID) -> UUID | None:
        return await resolve_assigned_counsellor_id(self.session, student_id)

    async def get_responsible_staff_ids(self, student_id: UUID) -> list[UUID]:
        return await resolve_responsible_staff_ids(self.session, student_id)


async def get_appointment_service(session: AsyncSession = Depends(get_db_session)) -> AppointmentService:
    return AppointmentService(session)
