from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import PaymentMethod, PaymentStatus

if TYPE_CHECKING:
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
        Index("idx_payments_application_id", "application_id"),
        Index("idx_payments_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<Payment id={self.id} amount={self.amount} status={self.status}>"
