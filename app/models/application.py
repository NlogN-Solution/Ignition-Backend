from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import ApplicationStatus

if TYPE_CHECKING:
    from .academic import Intake, Program
    from .document import ApplicationDocument, Document
    from .payment import Payment
    from .task import Task
    from .user import User


class Application(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "applications"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("programs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    counsellor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[ApplicationStatus] = mapped_column(
        enum_type(ApplicationStatus, "application_status", create_type=False),
        nullable=False,
        server_default=ApplicationStatus.DRAFT.value,
    )

    # NOTE (carried over from ED360): these six are annotated `date` but stored
    # as String(10). The annotation lies, and range/sort queries are
    # lexicographic. Left as-is so the port stays mechanical — converting them to
    # real DATE columns is a tracked follow-up, cheap here because Ignition has
    # no existing rows to migrate.
    application_date: Mapped[date | None] = mapped_column(String(10))
    submission_date: Mapped[date | None] = mapped_column(String(10))
    offer_received_date: Mapped[date | None] = mapped_column(String(10))
    visa_applied_date: Mapped[date | None] = mapped_column(String(10))
    visa_decision_date: Mapped[date | None] = mapped_column(String(10))
    enrollment_date: Mapped[date | None] = mapped_column(String(10))

    tuition_fee: Mapped[float | None] = mapped_column(Numeric(12, 2))
    scholarship_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    university_application_id: Mapped[str | None] = mapped_column(String(100))
    intake_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("intakes.id"))
    remarks: Mapped[str | None] = mapped_column(Text)

    student: Mapped[User] = relationship(
        "User",
        foreign_keys=[student_id],
        back_populates="applications_as_student",
    )
    counsellor: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[counsellor_id],
        back_populates="applications_as_counsellor",
    )
    program: Mapped[Program] = relationship(back_populates="applications")
    intake: Mapped[Intake | None] = relationship(back_populates="applications")
    status_history: Mapped[list[ApplicationStatusHistory]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
    )
    application_documents: Mapped[list[ApplicationDocument]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        overlaps="documents",
    )
    documents: Mapped[list[Document]] = relationship(
        secondary="application_documents",
        back_populates="applications",
        cascade="save-update, merge",
        overlaps="application_documents",
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="application")
    payments: Mapped[list[Payment]] = relationship(back_populates="application")

    __table_args__ = (
        Index("idx_applications_student_id", "student_id"),
        Index("idx_applications_program_id", "program_id"),
        Index("idx_applications_counsellor_id", "counsellor_id"),
        Index("idx_applications_status", "status"),
        Index("idx_applications_intake_id", "intake_id"),
    )

    def __repr__(self) -> str:
        return f"<Application id={self.id} status={self.status}>"


class ApplicationStatusHistory(Base, UUIDPKMixin):
    """Every status transition, append-only.

    This is what the student portal's application timeline renders from — the
    reason a student can see "Documents Uploaded ✓ / Under University Review →"
    without the CRM having to maintain a second, student-facing copy of state.
    """

    __tablename__ = "application_status_history"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    old_status: Mapped[ApplicationStatus | None] = mapped_column(
        enum_type(ApplicationStatus, "application_status", create_type=False)
    )
    new_status: Mapped[ApplicationStatus] = mapped_column(
        enum_type(ApplicationStatus, "application_status", create_type=False),
        nullable=False,
    )
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    remarks: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    application: Mapped[Application] = relationship(back_populates="status_history")
    changer: Mapped[User | None] = relationship("User")

    __table_args__ = (
        Index("idx_application_status_history_application_id", "application_id"),
        Index("idx_application_status_history_changed_by", "changed_by"),
    )

    def __repr__(self) -> str:
        return f"<ApplicationStatusHistory id={self.id} new_status={self.new_status}>"
