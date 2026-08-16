"""The visa and pre-departure journey.

Phase 6, fifth module. `VisaCase` is 1:1 with `Application` — the visa a
student applies for is for the offer they accepted, not a free-floating
record — and everything else here hangs off the case: the consulate's stage
ladder, the appointments a student must book, the documents the case needs,
its fees, and the pre-departure checklist.

There is deliberately no route that creates a `VisaCase`: staff open one when
a student's application reaches the visa stage, the same way a counsellor
starts an `Application` in the first place. Phase 6 only builds the student's
read/limited-write surface over it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, Boolean, Date, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import VisaAppointmentStatus, VisaCaseStatus, VisaDocumentRequirementStatus, VisaStageState

if TYPE_CHECKING:
    from .application import Application
    from .user import User


class VisaCase(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "visa_cases"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    #: Denormalized from `application.student_id`, matching `Document` and
    #: every other student-owned row — `StudentScopedRepository` filters on
    #: this column directly rather than joining through `Application`.
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    destination_country: Mapped[str] = mapped_column(String(100), nullable=False)
    visa_type: Mapped[str] = mapped_column(String(150), nullable=False)
    #: Assigned by the consulate once lodged; absent before then.
    application_number: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[VisaCaseStatus] = mapped_column(
        enum_type(VisaCaseStatus, "visa_case_status", create_type=False),
        nullable=False,
        server_default=VisaCaseStatus.PREPARING.value,
    )
    target_lodgement_date: Mapped[date | None] = mapped_column(Date)
    programme_start: Mapped[date | None] = mapped_column(Date)
    processing_estimate_weeks: Mapped[int | None] = mapped_column(Integer)

    application: Mapped[Application] = relationship()
    student: Mapped[User] = relationship()
    stages: Mapped[list[VisaStage]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="VisaStage.order"
    )
    appointments: Mapped[list[VisaAppointment]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="VisaAppointment.created_at"
    )
    document_requirements: Mapped[list[VisaDocumentRequirement]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="VisaDocumentRequirement.order"
    )
    fees: Mapped[list[VisaFee]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="VisaFee.created_at"
    )
    departure_checklist: Mapped[list[DepartureChecklistItem]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="DepartureChecklistItem.order"
    )

    __table_args__ = (Index("idx_visa_cases_student_id", "student_id"),)

    def __repr__(self) -> str:
        return f"<VisaCase application_id={self.application_id} status={self.status}>"


class VisaStage(Base, UUIDPKMixin, TimestampMixin):
    """One rung of the consulate's own process — offer accepted, biometrics,
    decision — not the platform's stages. Staff (or, eventually, a status feed
    from the consulate) move `state` forward; the student only reads it."""

    __tablename__ = "visa_stages"

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visa_cases.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    date: Mapped[date | None] = mapped_column(Date)
    state: Mapped[VisaStageState] = mapped_column(
        enum_type(VisaStageState, "visa_stage_state", create_type=False),
        nullable=False,
        server_default=VisaStageState.UPCOMING.value,
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    case: Mapped[VisaCase] = relationship(back_populates="stages")

    __table_args__ = (Index("idx_visa_stages_case_id", "case_id"),)

    def __repr__(self) -> str:
        return f"<VisaStage case_id={self.case_id} label={self.label!r}>"


class VisaAppointment(Base, UUIDPKMixin, TimestampMixin):
    """A medical exam, biometrics slot, or consulate visit — distinct from the
    counselling `Appointment` model, which is with staff, not a third party."""

    __tablename__ = "visa_appointments"

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visa_cases.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(200))
    scheduled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[VisaAppointmentStatus] = mapped_column(
        enum_type(VisaAppointmentStatus, "visa_appointment_status", create_type=False),
        nullable=False,
        server_default=VisaAppointmentStatus.NOT_BOOKED.value,
    )
    note: Mapped[str | None] = mapped_column(Text)

    case: Mapped[VisaCase] = relationship(back_populates="appointments")

    __table_args__ = (Index("idx_visa_appointments_case_id", "case_id"),)

    def __repr__(self) -> str:
        return f"<VisaAppointment case_id={self.case_id} title={self.title!r}>"


class VisaDocumentRequirement(Base, UUIDPKMixin, TimestampMixin):
    """What the consulate wants, separate from `ApplicationChecklistItem`
    (what the university wanted) — the two lists overlap in content but not in
    audience, and a document verified for admission is not thereby verified
    for the visa office."""

    __tablename__ = "visa_document_requirements"

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visa_cases.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[VisaDocumentRequirementStatus] = mapped_column(
        enum_type(VisaDocumentRequirementStatus, "visa_document_requirement_status", create_type=False),
        nullable=False,
        server_default=VisaDocumentRequirementStatus.MISSING.value,
    )
    note: Mapped[str | None] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    case: Mapped[VisaCase] = relationship(back_populates="document_requirements")

    __table_args__ = (Index("idx_visa_document_requirements_case_id", "case_id"),)

    def __repr__(self) -> str:
        return f"<VisaDocumentRequirement case_id={self.case_id} title={self.title!r}>"


class VisaFee(Base, UUIDPKMixin, TimestampMixin):
    """A consulate or panel-physician fee the student pays out of pocket.

    Self-reported, unlike `Payment`: there is no gateway integration for a
    fee paid directly to a foreign government, so a student marking it paid is
    the only record the platform will ever have. Real tuition and platform
    transactions go through `Payment` in the finance module, not here.
    """

    __tablename__ = "visa_fees"

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visa_cases.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    paid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    paid_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    case: Mapped[VisaCase] = relationship(back_populates="fees")

    __table_args__ = (Index("idx_visa_fees_case_id", "case_id"),)

    def __repr__(self) -> str:
        return f"<VisaFee case_id={self.case_id} label={self.label!r} paid={self.paid}>"


class DepartureChecklistItem(Base, UUIDPKMixin, TimestampMixin):
    """Flights, accommodation, insurance — the pre-departure list, scoped to a
    visa case rather than the general journey checklist because it only makes
    sense once a case exists to depart for."""

    __tablename__ = "departure_checklist_items"

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("visa_cases.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    case: Mapped[VisaCase] = relationship(back_populates="departure_checklist")

    __table_args__ = (Index("idx_departure_checklist_items_case_id", "case_id"),)

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    def __repr__(self) -> str:
        return f"<DepartureChecklistItem case_id={self.case_id} title={self.title!r}>"
