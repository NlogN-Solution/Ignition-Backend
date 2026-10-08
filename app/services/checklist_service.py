"""The student's journey checklist.

Phase 6. The checklist holds only what someone actually asked of this student:
tasks their counsellor set (`is_priority`) and any the student wrote themselves.
It used to be pre-filled from `ChecklistTemplateItem` — the same nine-step
passport-to-departure ladder for everyone, locked in order — but what a student
owes depends on their route and workflow, so a fixed ladder and its "2/9 done"
count promised a journey nobody had planned. Migration c7a2e9f14b58 removed the
copies already made; the template table stays, unused, for history.

Completion is client-driven — it is the student's own to-do list — but what a
completed item is *worth* is not: the service emits `ChecklistItemCompleted` and
a subscriber decides. That keeps the Phase 6 rule that points only ever move
through the event bus.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..api.exceptions import BadRequestException
from ..core.events import ChecklistItemCompleted, event_bus
from ..models import StudentChecklistItem, User

#: A student's own items sort after everything their counsellor set.
CUSTOM_ITEM_ORDER = 1000


class ChecklistService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_items(self, student: User) -> list[StudentChecklistItem]:
        result = await self.session.scalars(
            select(StudentChecklistItem)
            .where(StudentChecklistItem.student_id == student.id)
            .order_by(StudentChecklistItem.order, StudentChecklistItem.created_at)
        )
        return list(result)

    def locked_keys(self, items: list[StudentChecklistItem]) -> set[str]:
        """Keys of items whose prerequisite is still outstanding.

        A dependency naming an item this student does not have — a template rung
        that was deactivated, say — does not lock anything. Blocking a student
        behind a task that no longer exists would leave them with no way forward.
        """
        completed = {item.key for item in items if item.key and item.is_complete}
        present = {item.key for item in items if item.key}
        return {
            item.key
            for item in items
            if item.key
            and item.depends_on_key
            and item.depends_on_key in present
            and item.depends_on_key not in completed
        }

    async def set_completed(self, student: User, item: StudentChecklistItem, completed: bool) -> StudentChecklistItem:
        """Tick or un-tick an item.

        Ticking a locked item is refused: the ordering is the point of a journey
        checklist, and letting a student claim their visa before their passport
        makes both claims worthless. Un-ticking is always allowed — correcting a
        mistake must not need staff.
        """
        if completed == item.is_complete:
            return item

        if completed:
            items = await self.list_items(student)
            if item.key and item.key in self.locked_keys(items):
                blocker = next((i for i in items if i.key == item.depends_on_key), None)
                raise BadRequestException(
                    f"Finish “{blocker.title}” first." if blocker else "An earlier step must be completed first."
                )

        item.completed_at = datetime.now(UTC) if completed else None
        await self.session.commit()
        await self.session.refresh(item)

        if completed:
            await event_bus.publish(
                ChecklistItemCompleted(
                    item_id=item.id,
                    student_id=student.id,
                    key=item.key,
                    title=item.title,
                ),
                self.session,
            )
        return item

    async def create_custom(
        self,
        student: User,
        title: str,
        description: str | None = None,
        due_date: date | None = None,
    ) -> StudentChecklistItem:
        item = StudentChecklistItem(
            student_id=student.id,
            title=title,
            description=description,
            due_date=due_date,
            order=CUSTOM_ITEM_ORDER,
            is_custom=True,
        )
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def list_priority(self, student_id: uuid.UUID) -> list[StudentChecklistItem]:
        """The tasks staff have set for this student, newest first."""
        result = await self.session.scalars(
            select(StudentChecklistItem)
            .options(selectinload(StudentChecklistItem.assigner))
            .where(StudentChecklistItem.student_id == student_id, StudentChecklistItem.is_priority.is_(True))
            .order_by(StudentChecklistItem.completed_at.is_not(None), StudentChecklistItem.created_at.desc())
        )
        return list(result)

    async def create_priority(
        self,
        student: User,
        *,
        title: str,
        description: str | None,
        due_date: date | None,
        assigned_by: uuid.UUID,
        stage: str | None = None,
    ) -> StudentChecklistItem:
        """A task a counsellor sets. Order 0 puts it ahead of the student's own
        items wherever the checklist is listed in order; `is_custom` stays false
        so the student can complete it but not reword or delete it."""
        item = StudentChecklistItem(
            student_id=student.id,
            title=title,
            description=description,
            due_date=due_date,
            order=0,
            is_custom=False,
            is_priority=True,
            assigned_by=assigned_by,
            stage=stage,
        )
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item, attribute_names=["assigner"])
        return item

    async def delete(self, item: StudentChecklistItem) -> None:
        """Only the student's own items. A task their counsellor set is not
        theirs to remove — they can complete it or leave it unticked."""
        if not item.is_custom:
            raise BadRequestException("Your advisor set this task, so it cannot be removed. You can leave it unticked.")
        await self.session.delete(item)
        await self.session.commit()


async def get_checklist_service(session: AsyncSession = Depends(get_db_session)) -> ChecklistService:
    return ChecklistService(session)
