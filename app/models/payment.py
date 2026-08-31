from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import PaymentMethod, PaymentPurpose, PaymentStatus

if TYPE_CHECKING:
    from .academic import Country
    from .application import Application
    from .user import User

# ED360's legacy per-user `Subscription` model lived in this module. It predates
# multi-tenancy, is referenced by no route there, and has no meaning in a
# single-tenant product — dropped with the rest of Bucket A.


class Payment(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "payments"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"))
    purpose: Mapped[PaymentPurpose] = mapped_column(
        enum_type(PaymentPurpose, "payment_purpose", create_type=False),
        nullable=False,
        server_default=PaymentPurpose.OTHER.value,
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="NPR")
    payment_method: Mapped[PaymentMethod] = mapped_column(
        enum_type(PaymentMethod, "payment_method", create_type=False),
        nullable=False,
    )
    status: Mapped[PaymentStatus] = mapped_column(
        enum_type(PaymentStatus, "payment_status", create_type=False),
        nullable=False,
        server_default=PaymentStatus.PENDING.value,
    )
    transaction_reference: Mapped[str | None] = mapped_column(String(255))
    payment_date: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    remarks: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    student: Mapped[User] = relationship(
        "User",
        foreign_keys=[student_id],
        back_populates="payments_owned",
    )
    creator: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[created_by],
        back_populates="payments_created",
    )
    application: Mapped[Application | None] = relationship(back_populates="payments")

    __table_args__ = (
        Index("idx_payments_student_id", "student_id"),
        # The portal-access lookup runs on every gated request, so it gets its
        # own index rather than filtering the student's whole payment history.
        Index("idx_payments_student_purpose_status", "student_id", "purpose", "status"),
        Index("idx_payments_application_id", "application_id"),
        Index("idx_payments_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<Payment id={self.id} amount={self.amount} status={self.status}>"


class PortalAccessFee(Base, UUIDPKMixin, TimestampMixin):
    """What it costs a student from a given country to use the portal.

    Ignition's fee is one-time and varies by where the student is applying
    from — NPR 5,000 in Nepal, different elsewhere — so it is per-country data
    that staff change over time, not a constant. `country_id` is nullable: that
    row is the fallback used for any country without its own price, so a
    student from an unlisted country is quoted something rather than being
    blocked by missing configuration.
    """

    __tablename__ = "portal_access_fees"

    country_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("countries.id", ondelete="CASCADE"),
        unique=True,
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text)

    country: Mapped[Country | None] = relationship()

    def __repr__(self) -> str:
        return f"<PortalAccessFee country_id={self.country_id} {self.currency} {self.amount}>"
