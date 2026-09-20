"""Whose records a staff caller may see.

Every list in the console used to be the whole book of business: a counsellor
opening Leads saw every lead in the agency, including the ones another
counsellor was working, and the same for applications and student message
threads. Role checks answered "may you use this screen", never "which rows".

The rule here answers the second question, in one place, so the three surfaces
cannot drift apart.

**Own work, plus anything unclaimed.** A counsellor sees the records assigned to
them and the records assigned to nobody — the unclaimed ones because that is how
work gets picked up: a lead nobody owns is a lead this counsellor may take, and
hiding it would mean new enquiries sat unseen until an admin dealt them out.
What they must not see is a record with someone else's name on it.

Everyone senior to that — admin, super_admin, manager — sees everything, because
oversight is their job. Roles that are not narrowed here (marketing on leads,
admissions on applications) keep the visibility they already had; this module
narrows one role deliberately rather than quietly re-permissioning the console.
"""

from __future__ import annotations

from uuid import UUID

from ..models import User
from ..models.enums import UserRole

#: Roles whose lists are narrowed to their own records plus unassigned ones.
OWN_WORK_ONLY_ROLES = frozenset({UserRole.COUNSELLOR})


def own_work_scope(user: User) -> UUID | None:
    """The owner id this caller's rows must match, or `None` for "see everything".

    `None` is deliberately the unrestricted answer rather than "match rows owned
    by nobody": a service reading this has to treat the two cases differently,
    and a falsy-check bug would otherwise silently widen a counsellor's view
    instead of narrowing it.
    """
    return user.id if user.role in OWN_WORK_ONLY_ROLES else None


def may_see_record(user: User, owner_id: UUID | None) -> bool:
    """Whether `user` may open one record owned by `owner_id`.

    Unowned records (`owner_id is None`) are visible to everyone who may use the
    screen at all — they are the queue everybody picks from.
    """
    scope = own_work_scope(user)
    return scope is None or owner_id is None or owner_id == scope
