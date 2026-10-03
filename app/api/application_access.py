"""Who may act on one application's workflow, checklist and journey.

Shared by `routes/workflow.py` and `routes/journey.py` so the sub-resources of
an application can never be more visible than the application itself.
"""

from __future__ import annotations

from uuid import UUID

from ..models import Application, User
from ..models.enums import UserRole
from ..services.application_service import ApplicationService
from .exceptions import ForbiddenException, NotFoundException
from .scoping import may_see_record

#: Staff roles trusted with any application's workflow.
APPLICATION_STAFF_ROLES = frozenset({UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.COUNSELLOR, UserRole.ADMISSIONS})


async def assert_application_access(
    application_id: UUID,
    user: User,
    application_service: ApplicationService,
) -> Application:
    """Authorise the caller for one application's workflow and checklist.

    The same rule as `GET /applications/{id}` (`routes/application.py`), so a
    sub-resource can never show what the application itself hides:

    * a student reaches only their own application (403 otherwise, as there);
    * staff on the application-handling roles pass the own-work rule — a
      counsellor sees their own applications and unassigned ones, and gets a
      404 for a colleague's, exactly as the by-id read does (FAPI-SEC-005).
      This helper used to wave every staff member through, so the checklist
      and workflow of an application `/applications/{id}` returned 404 for
      were fully readable and writable here.

    Returns the application so callers that already need it (e.g. to notify
    its student) don't re-fetch it.
    """
    application = await application_service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    if user.role in APPLICATION_STAFF_ROLES:
        if not may_see_record(user, application.counsellor_id):
            raise NotFoundException("Application not found")
        return application
    if user.role is UserRole.STUDENT and application.student_id == user.id:
        return application
    raise ForbiddenException("You do not have access to this application")
