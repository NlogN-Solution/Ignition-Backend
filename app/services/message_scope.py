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

from uuid import UUID

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.exceptions import NotFoundException
from ..models import Application, Lead, Message, User
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
