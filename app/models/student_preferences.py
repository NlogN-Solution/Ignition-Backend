"""Per-student dashboard display preferences.

Phase 6, ninth module. Unlike `StudentProfile.preferences` (Phase 1, the
student's *study* preferences — intake, destinations, budget), this is how the
portal itself should look and behave for them: which currency to show money
in, whether to see the points widget, which channels to notify them on. The
frontend source for this module is "—" in the plan — there is no JSON fixture
to port, because nothing in ED360 or the Django app modelled it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin


class StudentDashboardSettings(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "student_dashboard_settings"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    #: ISO 4217 code. Nullable — falls back to the currency implied by the
    #: student's destination country rather than a hardcoded default.
    preferred_currency: Mapped[str | None] = mapped_column(String(3))
    email_notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    push_notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    show_points_widget: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    def __repr__(self) -> str:
        return f"<StudentDashboardSettings student_id={self.student_id}>"
