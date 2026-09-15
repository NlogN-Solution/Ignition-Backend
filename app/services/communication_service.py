"""One correspondence history per person, whatever stage they are at.

## The problem this exists to solve

A lead and a student are the same human being. The database has always known
that — `Lead.converted_user_id` points at the account created on conversion —
but correspondence did not use it. Lead-stage conversation lived in
`lead_activities`, student-stage conversation lived in a flat `messages` table
keyed by `student_id`, and the two never met. So a counsellor who spent three
weeks talking somebody into applying opened the resulting student record and
found an empty inbox. The history was not lost; it was filed under an identity
that screen could not see.

## How continuity works

Threads resolve **by person, not by stage**. `threads_for_student` does not
ask "which threads have this student_id" — it asks "which threads belong to
this person", and a person is a user id *plus every lead that converted into
it*. One query, one `OR`, no copying and no migration step on conversion.

That is the whole trick, and it is why `MessageThread.student_id` and
`lead_id` are both nullable with neither being the "real" one:

    thread opened against the lead     → lead_id set,     student_id null
    lead converts                      → nothing happens to the thread
    counsellor opens the student page  → resolved via converted_user_id
    counsellor replies                 → student_id backfilled, lead_id kept

The backfill in `_attach_student` is a convenience for future queries, not a
correctness requirement — drop it and continuity still holds, because the
join through `leads` is what does the work.

## Visibility

Every student-facing read goes through `_student_visible`, which excludes
`INTERNAL` threads structurally rather than by remembering to filter. Staff
notes about a student live in the same place staff write *to* the student, and
one forgotten `where` clause would be the kind of mistake that cannot be
walked back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..core.events import ThreadMessagePosted, event_bus
from ..core.sanitize import sanitize_message_html
from ..models import (
    Lead,
    MessageAttachment,
    MessageThread,
    ThreadMessage,
    User,
)
from ..models.communication import MessageAttachmentKind, ThreadVisibility

#: Preview length on the mailbox list. Long enough to tell two replies apart.
PREVIEW_LENGTH = 300


class CommunicationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- identity ------------------------------------------------------------

    async def _lead_ids_for(self, student_id: uuid.UUID) -> list[uuid.UUID]:
        """Every lead that became this user.

        Plural on purpose: a person who enquired twice has two lead rows, and
        both halves of their history are theirs.
        """
        return list(
            (
                await self.session.scalars(
                    select(Lead.id).where(Lead.converted_user_id == student_id)
                )
            ).all()
        )

    async def _person_filter(self, student_id: uuid.UUID) -> ColumnElement[bool]:
        """"Threads belonging to this person", across the lifecycle boundary."""
        lead_ids = await self._lead_ids_for(student_id)
        clauses: list[ColumnElement[bool]] = [MessageThread.student_id == student_id]
        if lead_ids:
            clauses.append(MessageThread.lead_id.in_(lead_ids))
        return or_(*clauses)

    # --- reads ---------------------------------------------------------------

    _OPTIONS = (
        selectinload(MessageThread.messages).selectinload(ThreadMessage.attachments),
        selectinload(MessageThread.student),
        selectinload(MessageThread.lead),
        selectinload(MessageThread.application),
    )

    def _base(self) -> Select[Any]:
        return select(MessageThread).options(*self._OPTIONS)

    @staticmethod
    def _student_visible(query: Select[Any]) -> Select[Any]:
        return query.where(MessageThread.visibility == ThreadVisibility.SHARED)

    async def threads_for_student(
        self,
        student_id: uuid.UUID,
        *,
        include_internal: bool = False,
        application_id: uuid.UUID | None = None,
    ) -> list[MessageThread]:
        """Everything this person has ever corresponded about.

        `include_internal` is for staff callers only. It defaults to False so
        that a caller who forgets to think about visibility gets the safe
        answer rather than the leaky one.
        """
        query = self._base().where(await self._person_filter(student_id))
        if not include_internal:
            query = self._student_visible(query)
        if application_id is not None:
            query = query.where(MessageThread.application_id == application_id)
        result = await self.session.execute(query.order_by(MessageThread.last_message_at.desc()))
        return list(result.scalars().unique().all())

    async def threads_for_lead(self, lead_id: uuid.UUID) -> list[MessageThread]:
        """A lead's threads, including any opened after they converted.

        The mirror of `threads_for_student`: once converted, new threads are
        written against the *user*, and the lead page must still show them or
        the discontinuity reappears pointing the other way.
        """
        converted_user_id = await self.session.scalar(
            select(Lead.converted_user_id).where(Lead.id == lead_id)
        )
        clauses: list[ColumnElement[bool]] = [MessageThread.lead_id == lead_id]
        if converted_user_id is not None:
            clauses.append(MessageThread.student_id == converted_user_id)
        result = await self.session.execute(
            self._base().where(or_(*clauses)).order_by(MessageThread.last_message_at.desc())
        )
        return list(result.scalars().unique().all())

    async def threads_for_application(self, application_id: uuid.UUID) -> list[MessageThread]:
        result = await self.session.execute(
            self._base()
            .where(MessageThread.application_id == application_id)
            .order_by(MessageThread.last_message_at.desc())
        )
        return list(result.scalars().unique().all())

    async def staff_inbox(
        self,
        *,
        search: str | None = None,
        unread_only: bool = False,
        page: int = 1,
        limit: int = 30,
    ) -> tuple[list[MessageThread], int]:
        """The console's mailbox: every thread, newest activity first."""
        query = self._base()
        count_query = select(func.count()).select_from(MessageThread)

        conditions: list[ColumnElement[bool]] = []
        if search and search.strip():
            needle = f"%{search.strip().lower()}%"
            conditions.append(
                or_(
                    func.lower(MessageThread.subject).like(needle),
                    func.lower(MessageThread.last_message_preview).like(needle),
                )
            )
        if unread_only:
            # Unread *for staff* means an unread student-authored message.
            conditions.append(
                select(ThreadMessage.id)
                .where(
                    ThreadMessage.thread_id == MessageThread.id,
                    ThreadMessage.is_from_student.is_(True),
                    ThreadMessage.read_at.is_(None),
                )
                .exists()
            )
        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        result = await self.session.execute(
            query.order_by(MessageThread.last_message_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(result.scalars().unique().all()), total

    async def get_thread(self, thread_id: uuid.UUID) -> MessageThread | None:
        return await self.session.scalar(self._base().where(MessageThread.id == thread_id))

    async def student_can_see(self, thread: MessageThread, student_id: uuid.UUID) -> bool:
        """Authorisation, in one place.

        A student may read a thread that is theirs *and* shared. Both halves
        matter: theirs-but-internal is a staff note, and shared-but-not-theirs
        is somebody else's correspondence.
        """
        if thread.visibility is not ThreadVisibility.SHARED:
            return False
        if thread.student_id == student_id:
            return True
        if thread.lead_id is None:
            return False
        return thread.lead_id in await self._lead_ids_for(student_id)

    async def unread_count_for_student(self, student_id: uuid.UUID) -> int:
        person = await self._person_filter(student_id)
        return (
            await self.session.scalar(
                select(func.count())
                .select_from(ThreadMessage)
                .join(MessageThread, MessageThread.id == ThreadMessage.thread_id)
                .where(
                    person,
                    MessageThread.visibility == ThreadVisibility.SHARED,
                    ThreadMessage.is_from_student.is_(False),
                    ThreadMessage.read_at.is_(None),
                )
            )
            or 0
        )

    # --- writes --------------------------------------------------------------

    async def create_thread(
        self,
        *,
        subject: str,
        student_id: uuid.UUID | None,
        lead_id: uuid.UUID | None,
        application_id: uuid.UUID | None,
        visibility: ThreadVisibility,
        created_by: uuid.UUID | None,
    ) -> MessageThread:
        thread = MessageThread(
            subject=subject.strip(),
            student_id=student_id,
            lead_id=lead_id,
            application_id=application_id,
            visibility=visibility,
            created_by=created_by,
            last_message_at=datetime.now(UTC),
        )
        self.session.add(thread)
        await self.session.commit()
        await self.session.refresh(thread)
        return thread

    async def _attach_student(self, thread: MessageThread) -> None:
        """Backfill `student_id` on a lead thread whose lead has converted.

        A convenience, not a correctness requirement: `threads_for_student`
        already finds this thread through `leads.converted_user_id`. Setting it
        keeps the common query on one index instead of an `OR` over two, and
        makes the row self-describing to anyone reading the table directly.
        `lead_id` is kept — the thread really did start at the lead stage, and
        erasing that would lose where the relationship began.
        """
        if thread.student_id is not None or thread.lead_id is None:
            return
        converted_user_id = await self.session.scalar(
            select(Lead.converted_user_id).where(Lead.id == thread.lead_id)
        )
        if converted_user_id is not None:
            thread.student_id = converted_user_id

    async def post_message(
        self,
        thread: MessageThread,
        *,
        author: User | None,
        body: str,
        body_html: str | None = None,
        is_from_student: bool,
        attachments: list[dict[str, Any]] | None = None,
    ) -> ThreadMessage:
        """Add a message, and keep the thread's summary honest.

        `last_message_at`/`last_message_preview` are denormalised onto the
        thread so the mailbox list sorts and previews without touching every
        message. They are written here, in the one place a message can be
        created, which is what stops them drifting.
        """
        message = ThreadMessage(
            thread_id=thread.id,
            author_id=author.id if author else None,
            author_name=(
                f"{author.first_name} {author.last_name}".strip() if author else None
            ),
            is_from_student=is_from_student,
            body=body,
            # Sanitised on the way *in*, not on the way out. Storing what the
            # client sent and cleaning it per-render means every reader has to
            # remember to, and one that forgets is a stored XSS against
            # whoever opens the thread next — which, for a student's message,
            # is a counsellor. See `core/sanitize`.
            body_html=sanitize_message_html(body_html),
        )
        self.session.add(message)
        await self.session.flush()

        for attachment in attachments or []:
            self.session.add(
                MessageAttachment(
                    message_id=message.id,
                    kind=attachment.get("kind", MessageAttachmentKind.FILE),
                    original_file_name=attachment["original_file_name"],
                    stored_file_name=attachment["stored_file_name"],
                    mime_type=attachment.get("mime_type"),
                    file_size=attachment.get("file_size"),
                    duration_seconds=attachment.get("duration_seconds"),
                )
            )

        thread.last_message_at = message.created_at or datetime.now(UTC)
        thread.last_message_preview = body[:PREVIEW_LENGTH]
        await self._attach_student(thread)
        await self.session.commit()

        await event_bus.publish(
            ThreadMessagePosted(
                thread_id=thread.id,
                message_id=message.id,
                student_id=thread.student_id,
                subject=thread.subject,
                is_from_student=is_from_student,
                author_id=author.id if author else None,
                preview=body[:200],
            ),
            self.session,
        )
        return await self.session.scalar(
            select(ThreadMessage)
            .options(selectinload(ThreadMessage.attachments))
            .where(ThreadMessage.id == message.id)
        )

    async def mark_read(self, thread: MessageThread, *, by_student: bool) -> int:
        """Mark the other side's messages read. Returns how many changed."""
        now = datetime.now(UTC)
        unread = await self.session.scalars(
            select(ThreadMessage).where(
                ThreadMessage.thread_id == thread.id,
                ThreadMessage.read_at.is_(None),
                ThreadMessage.is_from_student.is_(not by_student),
            )
        )
        count = 0
        for message in unread:
            message.read_at = now
            count += 1
        if count:
            await self.session.commit()
        return count


async def get_communication_service(
    session: AsyncSession = Depends(get_db_session),
) -> CommunicationService:
    return CommunicationService(session)
