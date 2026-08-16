"""The student's own journey checklist.

Phase 6, third module. This is the checklist for things that are *not* scoped to
an application — get a passport, book the IELTS, draft the SOP. Anything owed
against a specific application already has `ApplicationChecklistItem` and is not
duplicated here.

Two tables for the same reason progress has two: `ChecklistTemplateItem` is the
default ladder everybody starts on and can be re-tuned without a deploy, while
`StudentChecklistItem` is one student's copy — which they may complete, re-date,
or add to with items of their own.

Unlike milestones and points, this one *is* client-writable. A checklist is the
student's to-do list; ticking "passport secured" is a claim about their own life,
not a fact the system observes. What it must not do is silently pay them points
— completion emits an event and a subscriber decides what that is worth.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .user import User


class ChecklistTemplateItem(Base, UUIDPKMixin, TimestampMixin):
    """One rung of the default checklist, the same for every student."""

    __tablename__ = "checklist_template_items"

    key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: Free text, matching the journey stage names the portal displays.
    stage: Mapped[str | None] = mapped_column(String(60))
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: The key of the item that must be done first, or NULL for "nothing".
    #: A key rather than a foreign key: the dependency is on the *rung*, and a
    #: template row being re-seeded should not orphan it.
    depends_on_key: Mapped[str | None] = mapped_column(String(50))
    #: Days after the student joins that this item is due, or NULL for undated.
    #: Stored as an offset rather than a date because the template is shared —
    #: an absolute deadline would be wrong for everyone but the first cohort.
    due_after_days: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    def __repr__(self) -> str:
        return f"<ChecklistTemplateItem key={self.key}>"


class StudentChecklistItem(Base, UUIDPKMixin, TimestampMixin):
    """One student's copy of a checklist item, or one they wrote themselves.

    The title and description are copied from the template rather than read
    through the foreign key. A student who has already ticked "Sit the IELTS
    exam" should keep seeing the wording they agreed to, even after the template
    is reworded — and a custom item has no template to read from at all.
    """

    __tablename__ = "student_checklist_items"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: NULL for an item the student added themselves.
    template_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("checklist_template_items.id", ondelete="SET NULL")
    )
    #: Copied from the template; NULL for custom items, so it cannot collide
    #: with a template key under the uniqueness constraint below.
    key: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(String(60))
    order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    depends_on_key: Mapped[str | None] = mapped_column(String(50))
    due_date: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    #: Custom items may be deleted; seeded ones may not, so the journey cannot
    #: be quietly emptied to reach 100%.
    is_custom: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    student: Mapped[User] = relationship(back_populates="checklist_items")
    template_item: Mapped[ChecklistTemplateItem | None] = relationship()

    __table_args__ = (
        # Materialising the template twice for the same student is a bug, not a
        # second copy of the task. NULLs are distinct in Postgres, so custom
        # items are unaffected by this.
        UniqueConstraint("student_id", "key", name="uq_student_checklist_items_student_id_key"),
        Index("idx_student_checklist_items_student_id", "student_id"),
    )

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    def __repr__(self) -> str:
        return f"<StudentChecklistItem student_id={self.student_id} title={self.title!r}>"
