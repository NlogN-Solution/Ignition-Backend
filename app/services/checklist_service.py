"""The student's journey checklist.

Phase 6. Items are materialised from the template on first read rather than at
signup: a student who registered before a rung existed still gets it, and adding
a rung to the template does not need a backfill script.

Completion is client-driven — it is the student's own to-do list — but what a
completed item is *worth* is not: the service emits `ChecklistItemCompleted` and
a subscriber decides. That keeps the Phase 6 rule that points only ever move
through the event bus.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..api.exceptions import BadRequestException
from ..core.events import ChecklistItemCompleted, event_bus
from ..models import ChecklistTemplateItem, StudentChecklistItem, User

#: Custom items sort after every seeded one. A student's own additions belong at
#: the end of the ladder rather than interleaved into a journey they did not
#: define the order of.
CUSTOM_ITEM_ORDER = 1000


class ChecklistService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def materialise(self, student: User) -> None:
        """Give this student a copy of every active template item they lack.

        Idempotent, and safe against a concurrent first request: the unique
        constraint on (student_id, key) turns a double-materialise into an
        IntegrityError we swallow, rather than a duplicated checklist.
        """
        templates = (
            await self.session.scalars(
                select(ChecklistTemplateItem)
                .where(ChecklistTemplateItem.is_active.is_(True))
                .order_by(ChecklistTemplateItem.order)
            )
        ).all()
        if not templates:
            return

        existing_keys = set(
            (
                await self.session.scalars(
                    select(StudentChecklistItem.key).where(
                        StudentChecklistItem.student_id == student.id,
                        StudentChecklistItem.key.is_not(None),
                    )
                )
            ).all()
        )
        missing = [template for template in templates if template.key not in existing_keys]
        if not missing:
            return

        # Deadlines are relative to when the student joined, so a template
        # offset of "60 days" means sixty days into *their* journey.
        joined = student.created_at or datetime.now(UTC)
        for template in missing:
            self.session.add(
                StudentChecklistItem(
                    student_id=student.id,
                    template_item_id=template.id,
                    key=template.key,
                    title=template.title,
                    description=template.description,
                    stage=template.stage,
                    order=template.order,
                    depends_on_key=template.depends_on_key,
                    due_date=(
                        (joined + timedelta(days=template.due_after_days)).date()
                        if template.due_after_days is not None
                        else None
                    ),
                )
            )
        try:
            await self.session.commit()
        except IntegrityError:
            # Another request materialised the same student first; their rows
            # are as good as ours.
            await self.session.rollback()

    async def list_items(self, student: User) -> list[StudentChecklistItem]:
        await self.materialise(student)
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

    async def delete(self, item: StudentChecklistItem) -> None:
        """Only the student's own items. A seeded rung is part of the journey
        every student is measured against, so deleting it is not theirs to do —
        and would be an easy way to reach an empty, "complete" checklist."""
        if not item.is_custom:
            raise BadRequestException("This is part of your journey and cannot be removed. You can leave it unticked.")
        await self.session.delete(item)
        await self.session.commit()


async def get_checklist_service(session: AsyncSession = Depends(get_db_session)) -> ChecklistService:
    return ChecklistService(session)
