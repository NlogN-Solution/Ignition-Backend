"""Which student conversations a staff caller may read.

`/messages/threads` used to select every message in the database and group it.
Any of the six staff roles saw every student's conversation, so one counsellor
read what a student had told another — the clearest privacy failure in the
console, because a support thread is where students write the things they would
not put in a form.

The rule is the one already used for leads and applications (`api/scoping.py`),
extended with the notion of *involvement*, because a message thread has no
`assigned_to` column to key on:

* **Admins and super-admins see everything.** Oversight of what staff say to
  students is exactly their job.
* **Everyone else sees their own, plus anything unclaimed.** Their own means the
  student is assigned to them, or they have already replied in that thread.
  Unclaimed means no staff member has replied and no counsellor owns the
  student.

The unclaimed half is not a loophole, it is what stops the rule causing harm. A
new student's first message arrives before anyone is assigned to them; if
"yours" were the whole rule, that message would be invisible to every counsellor
and sit unanswered until an admin happened to look.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, and_, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.exceptions import NotFoundException
from ..models import Application, Lead, Message, MessageThread, ThreadMessage, User
from ..models.enums import UserRole

#: Roles that read every conversation.
FULL_INBOX_ROLES = frozenset({UserRole.ADMIN, UserRole.SUPER_ADMIN})


def visible_students_condition(user: User) -> ColumnElement[bool] | None:
    """A condition on `Message.student_id`, or `None` meaning "no restriction"."""
    if user.role in FULL_INBOX_ROLES:
        return None

    # Students this caller owns: through an application they counsel, or a lead
    # they were assigned that has since converted into a portal account.
    own_by_application = select(Application.student_id).where(Application.counsellor_id == user.id)
    own_by_lead = select(Lead.converted_user_id).where(
        Lead.assigned_to == user.id, Lead.converted_user_id.is_not(None)
    )
    # ...and students they are already talking to.
    own_by_reply = select(Message.student_id).where(
        Message.sender_id == user.id, Message.is_from_student.is_(False)
    )

    # Claimed by *anyone*: someone counsels them, someone owns their lead, or
    # some staff member has already answered. Everything else is the shared
    # queue.
    claimed_by_application = select(Application.student_id).where(Application.counsellor_id.is_not(None))
    claimed_by_lead = select(Lead.converted_user_id).where(
        Lead.assigned_to.is_not(None), Lead.converted_user_id.is_not(None)
    )
    claimed_by_reply = select(Message.student_id).where(Message.is_from_student.is_(False))

    return or_(
        Message.student_id.in_(own_by_application.union(own_by_lead, own_by_reply)),
        Message.student_id.notin_(
            claimed_by_application.union(claimed_by_lead, claimed_by_reply)
        ),
    )


async def assert_thread_visible(session: AsyncSession, user: User, student_id: UUID) -> None:
    """Refuse a by-id read of a conversation this caller may not see.

    404 rather than 403, for the same reason the lead and application routes do
    it: a distinguishable refusal turns the id into a way to confirm which
    students exist and who is working them.

    A student with no messages yet is allowed through — there is nothing to
    disclose, and this is the path staff take to open a conversation.
    """
    condition = visible_students_condition(user)
    if condition is None:
        return

    thread_exists = await session.scalar(select(Message.id).where(Message.student_id == student_id).limit(1))
    if thread_exists is None:
        return

    visible = await session.scalar(
        select(Message.id).where(Message.student_id == student_id, condition).limit(1)
    )
    if visible is None:
        raise NotFoundException("Conversation not found")


# --- /communication threads ----------------------------------------------------
#
# The same rule, for the thread store that replaced the flat `messages` table
# (FAPI-SEC-005). `/communication` shipped without it: every staff role saw
# every thread — internal notes included — although `/messages`, `/leads` and
# `/applications` all hid a colleague's records. A thread knows more about who
# owns it than a legacy message did, so "own" and "claimed" are read off every
# link it carries: the student, the lead (and whoever that lead became), the
# application, and who has written in it.
#
# Everything is EXISTS/IN over non-null keys on purpose. A thread opened
# against a lead has `student_id IS NULL`, and `NULL IN (...)` is NULL, not
# false — inside the `NOT (claimed)` half that would silently hide every
# unclaimed lead thread from the people meant to pick it up.


def _thread_person_students(ids: Any) -> ColumnElement[bool]:
    """The thread's person is one of `ids` — directly, or through its lead."""
    return or_(
        and_(MessageThread.student_id.is_not(None), MessageThread.student_id.in_(ids)),
        select(Lead.id)
        .where(
            Lead.id == MessageThread.lead_id,
            Lead.converted_user_id.is_not(None),
            Lead.converted_user_id.in_(ids),
        )
        .exists(),
    )


def visible_threads_condition(user: User) -> ColumnElement[bool] | None:
    """A condition on `MessageThread`, or `None` meaning "no restriction"."""
    if user.role in FULL_INBOX_ROLES:
        return None

    live_applications = Application.deleted_at.is_(None)
    live_leads = Lead.deleted_at.is_(None)

    own_students = select(Application.student_id).where(Application.counsellor_id == user.id, live_applications).union(
        select(Lead.converted_user_id).where(
            Lead.assigned_to == user.id, Lead.converted_user_id.is_not(None), live_leads
        )
    )
    claimed_students = select(Application.student_id).where(
        Application.counsellor_id.is_not(None), live_applications
    ).union(
        select(Lead.converted_user_id).where(
            Lead.assigned_to.is_not(None), Lead.converted_user_id.is_not(None), live_leads
        )
    )

    own = or_(
        MessageThread.created_by == user.id,
        _thread_person_students(own_students),
        select(Lead.id).where(Lead.id == MessageThread.lead_id, Lead.assigned_to == user.id).exists(),
        select(Application.id)
        .where(Application.id == MessageThread.application_id, Application.counsellor_id == user.id)
        .exists(),
        select(ThreadMessage.id)
        .where(
            ThreadMessage.thread_id == MessageThread.id,
            ThreadMessage.author_id == user.id,
            ThreadMessage.is_from_student.is_(False),
        )
        .exists(),
    )
    claimed = or_(
        _thread_person_students(claimed_students),
        select(Lead.id).where(Lead.id == MessageThread.lead_id, Lead.assigned_to.is_not(None)).exists(),
        select(Application.id)
        .where(Application.id == MessageThread.application_id, Application.counsellor_id.is_not(None))
        .exists(),
        select(ThreadMessage.id)
        .where(ThreadMessage.thread_id == MessageThread.id, ThreadMessage.is_from_student.is_(False))
        .exists(),
    )
    return or_(own, not_(claimed))


async def may_open_thread_about(
    session: AsyncSession,
    user: User,
    *,
    student_id: UUID | None,
    lead_id: UUID | None,
    application_id: UUID | None,
) -> bool:
    """Whether a scoped staff member may start a thread about these records.

    The write-side twin of `visible_threads_condition`: a counsellor may not
    open a conversation with a colleague's student (and so gain sight of it
    through `created_by`). Own or unclaimed only, judged the same way.
    """
    if user.role in FULL_INBOX_ROLES:
        return True

    if application_id is not None:
        counsellor_id = await session.scalar(select(Application.counsellor_id).where(Application.id == application_id))
        if counsellor_id not in (None, user.id):
            return False
    if lead_id is not None:
        assignee = await session.scalar(select(Lead.assigned_to).where(Lead.id == lead_id))
        if assignee not in (None, user.id):
            return False
    if student_id is not None:
        owners = set(
            (
                await session.scalars(
                    select(Application.counsellor_id).where(
                        Application.student_id == student_id,
                        Application.counsellor_id.is_not(None),
                        Application.deleted_at.is_(None),
                    )
                )
            ).all()
        ) | set(
            (
                await session.scalars(
                    select(Lead.assigned_to).where(
                        Lead.converted_user_id == student_id,
                        Lead.assigned_to.is_not(None),
                        Lead.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        if owners and user.id not in owners:
            return False
    return True
