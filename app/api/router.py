from __future__ import annotations

from fastapi import APIRouter

from ..routes.academic import router as academic_router
from ..routes.activity_log import router as activity_log_router
from ..routes.application import router as application_router
from ..routes.appointment import router as appointment_router
from ..routes.attendance import router as attendance_router
from ..routes.auth import router as auth_router
from ..routes.departments import router as departments_router
from ..routes.document import router as document_router
from ..routes.employee_profile import router as employee_profile_router
from ..routes.employees import router as employees_router
from ..routes.health import router as health_router
from ..routes.leads import router as leads_router
from ..routes.leave import router as leave_router
from ..routes.message import router as message_router
from ..routes.notification import router as notification_router
from ..routes.payment import router as payment_router
from ..routes.payroll import router as payroll_router
from ..routes.student import router as student_router
from ..routes.student_profile import router as student_profile_router
from ..routes.task import router as task_router
from ..routes.users import router as users_router
from ..routes.workflow import router as workflow_router

# Everything is served under /api/v1 from day one. ED360 mounts at the root and
# has to keep that for its existing frontend; Ignition has no legacy clients, so
# there is no reason to inherit that debt.
router = APIRouter(prefix="/api/v1")

router.include_router(health_router)
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(academic_router)
router.include_router(student_profile_router)
router.include_router(employee_profile_router)
router.include_router(departments_router)
router.include_router(employees_router)
router.include_router(application_router)
router.include_router(document_router)
router.include_router(appointment_router)
router.include_router(payment_router)
router.include_router(task_router)
router.include_router(notification_router)
router.include_router(message_router)
router.include_router(workflow_router)
router.include_router(leads_router)
router.include_router(attendance_router)
router.include_router(leave_router)
router.include_router(payroll_router)
router.include_router(activity_log_router)

# Phase 5a: the student portal surface, scoped by StudentScopedRepository.
router.include_router(student_router)

# Step 10 (wiring) complete: every ported staff router is mounted above.
