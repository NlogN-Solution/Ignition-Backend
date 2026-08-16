from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import selectinload

from ..api.exceptions import BadRequestException, NotFoundException
from ..api.student import StudentScopedRepository, get_current_student, get_student_repository
from ..models import (
    Application,
    ApplicationChecklistItem,
    ApplicationStatusHistory,
    Appointment,
    BlogPost,
    CountryGuide,
    Document,
    Message,
    Notification,
    Payment,
    Program,
    StudentChecklistItem,
    StudentCompareCourse,
    StudentSavedCourse,
    StudentSavedUniversity,
    Task,
    University,
    User,
)
from ..models.enums import AppointmentStatus, NotificationType, SavingsGoalKind, TaskStatus
from ..models.student_portal import MAX_COMPARE_COURSES
from ..schemas.academic import (
    BlogPostList,
    BlogPostRead,
    CountryGuideList,
    CountryGuideRead,
    CountryList,
    ProgramList,
    UniversityList,
)
from ..schemas.activity import ActivityEntryRead, ActivityList
from ..schemas.application import (
    ApplicationCounsellorSummary,
    ApplicationProgramSummary,
    ApplicationStatusHistoryRead,
    StudentApplicationList,
    StudentApplicationRead,
)
from ..schemas.appointment import (
    AppointmentCounsellorSummary,
    AppointmentRead,
    AppointmentRequestCreate,
    StudentAppointmentList,
    StudentAppointmentRead,
)
from ..schemas.checklist import (
    ChecklistItemCreate,
    ChecklistItemRead,
    ChecklistItemUpdate,
    ChecklistRead,
)
from ..schemas.dashboard import DashboardRead
from ..schemas.document import DocumentList, DocumentRead
from ..schemas.finance import (
    BudgetRead,
    BudgetReplace,
    CountryCostOfLivingRead,
    CurrencyRateRead,
    FundingSourceCreate,
    FundingSourceRead,
    FundingSourceUpdate,
    LoanRead,
    SavingsGoalRead,
    SavingsGoalUpsert,
)
from ..schemas.interview import (
    InterviewAnswerCreate,
    InterviewAnswerRead,
    InterviewSessionCreate,
    InterviewSessionList,
    InterviewSessionRead,
    InterviewSessionSummary,
    InterviewTypeRead,
)
from ..schemas.message import MessageCreate, MessageList, MessageRead, MessageSenderSummary
from ..schemas.notification import NotificationList, NotificationRead
from ..schemas.payment import PaymentList, PaymentRead
from ..schemas.preferences import DashboardSettingsRead, DashboardSettingsUpdate
from ..schemas.progress import MilestoneRead, PointsEntryRead, PointsRead, ProgressRead
from ..schemas.student_portal import (
    CompareCourseList,
    SavedCourseList,
    SavedCourseRead,
    SavedUniversityList,
    SavedUniversityRead,
    SaveItemRequest,
)
from ..schemas.student_profile import StudentProfileRead, StudentProfileUpsert
from ..schemas.task import TaskList, TaskRead
from ..schemas.user import UserRead
from ..schemas.visa import (
    DepartureChecklistItemRead,
    DepartureChecklistItemUpdate,
    VisaAppointmentBook,
    VisaAppointmentRead,
    VisaCaseRead,
    VisaFeeRead,
    VisaFeeUpdate,
)
from ..schemas.workflow import ApplicationChecklistItemRead
from ..services.academic_service import (
    CountryService,
    ProgramService,
    UniversityService,
    get_country_service,
    get_program_service,
    get_university_service,
)
from ..services.activity_service import ActivityService, get_activity_service
from ..services.checklist_service import ChecklistService, get_checklist_service
from ..services.dashboard_service import DashboardService, get_dashboard_service
from ..services.finance_service import (
    BudgetService,
    FinanceCatalogService,
    FundingSourceService,
    LoanService,
    SavingsService,
    get_budget_service,
    get_finance_catalog_service,
    get_funding_source_service,
    get_loan_service,
    get_savings_service,
)
from ..services.interview_service import InterviewService, get_interview_service
from ..services.notification_service import NotificationService, get_notification_service
from ..services.preferences_service import PreferencesService, get_preferences_service
from ..services.progress_service import (
    PointsService,
    ProgressService,
    get_points_service,
    get_progress_service,
)
from ..services.student_profile_service import StudentProfileService, get_student_profile_service
from ..services.visa_service import VisaService, get_visa_service

router = APIRouter(prefix="/student", tags=["Student Portal"])

# Every handler resolves its rows through StudentScopedRepository, which applies
# the ownership filter inside the query builder. No handler below writes its own
# `student_id ==` clause, and none should: the point of Phase 5a is that
# forgetting is not possible rather than merely discouraged.


# --- Me -----------------------------------------------------------------------


@router.get("/me", response_model=UserRead, summary="My account")
async def get_me(student: User = Depends(get_current_student)) -> UserRead:
    return UserRead.model_validate(student)


@router.get("/me/profile", response_model=StudentProfileRead, summary="My student profile")
async def get_my_profile(repo: StudentScopedRepository = Depends(get_student_repository)) -> StudentProfileRead:
    return StudentProfileRead.model_validate(await repo.profile_or_404())


@router.patch("/me/profile", response_model=StudentProfileRead, summary="Update my student profile")
async def update_my_profile(
    payload: StudentProfileUpsert,
    student: User = Depends(get_current_student),
    service: StudentProfileService = Depends(get_student_profile_service),
) -> StudentProfileRead:
    try:
        profile = await service.upsert(student.id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    return StudentProfileRead.model_validate(profile)


# --- Applications --------------------------------------------------------------


def _with_program_summary(application: Application) -> StudentApplicationRead:
    read = StudentApplicationRead.model_validate(application)
    if application.program:
        university = application.program.university
        read.program = ApplicationProgramSummary(
            id=application.program.id,
            name=application.program.name,
            degree_level=application.program.degree_level,
            intake=application.program.intake,
            university_id=application.program.university_id,
            university_name=university.name if university else "",
            university_country=university.country.name if university and university.country else None,
        )
    if application.counsellor:
        read.counsellor = ApplicationCounsellorSummary(
            id=application.counsellor.id,
            full_name=f"{application.counsellor.first_name} {application.counsellor.last_name}".strip(),
        )
    return read


_APPLICATION_DETAIL_OPTIONS = [
    selectinload(Application.program).selectinload(Program.university).selectinload(University.country),
    selectinload(Application.counsellor),
]


@router.get("/me/applications", response_model=StudentApplicationList, summary="My applications")
async def list_my_applications(
    page: int = 1,
    limit: int = 20,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> StudentApplicationList:
    items, total = await repo.list(
        Application,
        order_by=Application.created_at.desc(),
        page=page,
        limit=limit,
        options=_APPLICATION_DETAIL_OPTIONS,
    )
    return StudentApplicationList(items=[_with_program_summary(a) for a in items], total=total, page=page, limit=limit)


@router.get(
    "/me/applications/{application_id}", response_model=StudentApplicationRead, summary="One of my applications"
)
async def get_my_application(
    application_id: UUID,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> StudentApplicationRead:
    application = await repo.get_or_404(
        Application,
        application_id,
        detail="Application not found",
        options=_APPLICATION_DETAIL_OPTIONS,
    )
    return _with_program_summary(application)


@router.get(
    "/me/applications/{application_id}/timeline",
    response_model=list[ApplicationStatusHistoryRead],
    summary="My application's status timeline",
)
async def get_my_application_timeline(
    application_id: UUID,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> list[ApplicationStatusHistoryRead]:
    """The payoff of the single-database port: a counsellor moving an
    application in the staff dashboard writes the same history row the student
    reads here."""
    application = await repo.get_or_404(Application, application_id, detail="Application not found")
    result = await repo.session.execute(
        ApplicationStatusHistory.__table__.select()
        .where(ApplicationStatusHistory.application_id == application.id)
        .order_by(ApplicationStatusHistory.created_at)
    )
    return [ApplicationStatusHistoryRead.model_validate(row, from_attributes=True) for row in result.mappings()]


@router.get(
    "/me/applications/{application_id}/checklist",
    response_model=list[ApplicationChecklistItemRead],
    summary="My application's checklist",
)
async def get_my_application_checklist(
    application_id: UUID,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> list[ApplicationChecklistItemRead]:
    application = await repo.get_or_404(Application, application_id, detail="Application not found")
    result = await repo.session.execute(
        ApplicationChecklistItem.__table__.select()
        .where(ApplicationChecklistItem.application_id == application.id)
        .order_by(ApplicationChecklistItem.created_at)
    )
    return [ApplicationChecklistItemRead.model_validate(row, from_attributes=True) for row in result.mappings()]


# --- Documents ------------------------------------------------------------------


@router.get("/me/documents", response_model=DocumentList, summary="My documents")
async def list_my_documents(
    page: int = 1,
    limit: int = 20,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> DocumentList:
    items, total = await repo.list(Document, order_by=Document.created_at.desc(), page=page, limit=limit)
    return DocumentList(items=[DocumentRead.model_validate(d) for d in items], total=total, page=page, limit=limit)


@router.get("/me/documents/{document_id}", response_model=DocumentRead, summary="One of my documents")
async def get_my_document(
    document_id: UUID,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> DocumentRead:
    return DocumentRead.model_validate(await repo.get_or_404(Document, document_id, detail="Document not found"))


# --- Appointments ----------------------------------------------------------------


@router.get("/me/appointments", response_model=StudentAppointmentList, summary="My appointments")
async def list_my_appointments(
    page: int = 1,
    limit: int = 20,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> StudentAppointmentList:
    items, total = await repo.list(
        Appointment,
        order_by=Appointment.start_time.desc().nullslast(),
        page=page,
        limit=limit,
        options=[selectinload(Appointment.counsellor)],
    )
    read_items = []
    for appointment in items:
        read = StudentAppointmentRead.model_validate(appointment)
        if appointment.counsellor:
            read.counsellor = AppointmentCounsellorSummary(
                id=appointment.counsellor.id,
                full_name=f"{appointment.counsellor.first_name} {appointment.counsellor.last_name}".strip(),
            )
        read_items.append(read)
    return StudentAppointmentList(items=read_items, total=total, page=page, limit=limit)


# --- Tasks -------------------------------------------------------------------------


@router.get("/me/tasks", response_model=TaskList, summary="Tasks assigned to me")
async def list_my_tasks(
    page: int = 1,
    limit: int = 20,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> TaskList:
    items, total = await repo.list(Task, order_by=Task.due_date.asc().nullslast(), page=page, limit=limit)
    return TaskList(items=[TaskRead.model_validate(t) for t in items], total=total, page=page, limit=limit)


@router.patch("/me/tasks/{task_id}", response_model=TaskRead, summary="Complete a task assigned to me")
async def complete_my_task(
    task_id: UUID,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> TaskRead:
    """A student may mark their own task done — and nothing else about it.

    Deliberately not a general PATCH: exposing the staff `TaskUpdate` here would
    let a student reassign work or change its priority.
    """
    task = await repo.get_or_404(Task, task_id, detail="Task not found")
    task.status = TaskStatus.COMPLETED
    await repo.session.commit()
    await repo.session.refresh(task)
    return TaskRead.model_validate(task)


# --- Payments ------------------------------------------------------------------------


@router.get("/me/payments", response_model=PaymentList, summary="My payments")
async def list_my_payments(
    page: int = 1,
    limit: int = 20,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> PaymentList:
    items, total = await repo.list(Payment, order_by=Payment.created_at.desc(), page=page, limit=limit)
    return PaymentList(items=[PaymentRead.model_validate(p) for p in items], total=total, page=page, limit=limit)


# --- Notifications ----------------------------------------------------------------------


@router.get("/me/notifications", response_model=NotificationList, summary="My notifications")
async def list_my_notifications(
    page: int = 1,
    limit: int = 20,
    is_read: bool | None = None,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> NotificationList:
    # Notifications hang off `user_id`, not `student_id` — the scoping column is
    # a parameter precisely so this stays one code path.
    conditions = [] if is_read is None else [Notification.is_read == is_read]
    items, total = await repo.list(
        Notification,
        column="user_id",
        order_by=Notification.created_at.desc(),
        conditions=conditions,
        page=page,
        limit=limit,
    )
    return NotificationList(
        items=[NotificationRead.model_validate(n) for n in items], total=total, page=page, limit=limit
    )


@router.post("/me/notifications/read-all", summary="Mark all my notifications read")
async def mark_all_my_notifications_read(
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> dict[str, int]:
    result = cast(
        "CursorResult[Any]",
        await repo.session.execute(
            update(Notification)
            .where(Notification.user_id == repo.student.id, Notification.is_read.is_(False))
            .values(is_read=True)
        ),
    )
    await repo.session.commit()
    return {"updated": result.rowcount}


# --- Support messages (general thread, any staff can see/reply) ------------------


def _with_sender_summary(message: Message) -> MessageRead:
    read = MessageRead.model_validate(message)
    if message.sender:
        read.sender = MessageSenderSummary(
            id=message.sender.id,
            full_name=f"{message.sender.first_name} {message.sender.last_name}".strip(),
        )
    return read


@router.get("/me/messages", response_model=MessageList, summary="My support messages")
async def list_my_messages(
    limit: int = 200,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> MessageList:
    items, total = await repo.list(
        Message,
        order_by=Message.created_at.asc(),
        limit=limit,
        options=[selectinload(Message.sender)],
    )
    unread_count = (
        await repo.session.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.student_id == repo.student.id,
                Message.is_from_student.is_(False),
                Message.is_read.is_(False),
            )
        )
        or 0
    )
    return MessageList(items=[_with_sender_summary(m) for m in items], total=total, unread_count=unread_count)


@router.post("/me/messages", response_model=MessageRead, summary="Send a support message")
async def send_my_message(
    payload: MessageCreate,
    student: User = Depends(get_current_student),
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> MessageRead:
    message = Message(
        student_id=student.id,
        sender_id=student.id,
        is_from_student=True,
        body=payload.body,
        # `is_read` tracks whether the *recipient* has read it — staff, for a
        # student-authored message — so it starts unread, same as a staff
        # reply starts unread from the student's side.
        is_read=False,
    )
    repo.session.add(message)
    await repo.session.commit()
    reloaded = await repo.session.scalar(
        select(Message).options(selectinload(Message.sender)).where(Message.id == message.id)
    )
    assert reloaded is not None
    return _with_sender_summary(reloaded)


@router.post("/me/messages/read-all", summary="Mark all my messages read")
async def mark_all_my_messages_read(
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> dict[str, int]:
    result = cast(
        "CursorResult[Any]",
        await repo.session.execute(
            update(Message)
            .where(Message.student_id == repo.student.id, Message.is_read.is_(False))
            .values(is_read=True)
        ),
    )
    await repo.session.commit()
    return {"updated": result.rowcount}


@router.post(
    "/me/appointments/request",
    response_model=AppointmentRead,
    summary="Request an appointment",
)
async def request_my_appointment(
    payload: AppointmentRequestCreate,
    student: User = Depends(get_current_student),
    notification_service: NotificationService = Depends(get_notification_service),
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> AppointmentRead:
    """Mirrors the staff router's student branch: a request, never a booking.

    Time, location and link are set by the counsellor on confirmation, so they
    are not accepted here at all.
    """
    appointment = Appointment(
        student_id=student.id,
        created_by=student.id,
        appointment_type=payload.appointment_type,
        title=payload.title,
        description=payload.description,
        preferred_date=payload.preferred_date,
        status=AppointmentStatus.REQUESTED,
    )
    repo.session.add(appointment)
    await repo.session.commit()
    await repo.session.refresh(appointment)

    await notification_service.notify_many(
        [student.id],
        notification_type=NotificationType.APPOINTMENT,
        title="Appointment requested",
        message=f"We received your request: {appointment.title}",
    )
    return AppointmentRead.model_validate(appointment)


# --- Catalog -------------------------------------------------------------------------

# Read-only. The staff catalog router already serves these to any authenticated
# user, but the student portal gets its own paths so Phase 7 can wire one base
# path and so published/active filtering is applied here rather than trusted to
# the caller.


@router.get("/catalog/countries", response_model=CountryList, summary="Browse destinations")
async def browse_countries(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    service: CountryService = Depends(get_country_service),
    _: User = Depends(get_current_student),
) -> CountryList:
    items, total = await service.list(page, limit, search=search, is_active=True)
    return CountryList(items=items, total=total, page=page, limit=limit)


@router.get("/catalog/universities", response_model=UniversityList, summary="Browse universities")
async def browse_universities(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    country_id: UUID | None = None,
    service: UniversityService = Depends(get_university_service),
    _: User = Depends(get_current_student),
) -> UniversityList:
    items, total = await service.list(page, limit, search=search, country_id=country_id, is_active=True)
    return UniversityList(items=items, total=total, page=page, limit=limit)


@router.get("/catalog/programs", response_model=ProgramList, summary="Search courses")
async def browse_programs(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    university_id: UUID | None = None,
    degree_level: str | None = None,
    service: ProgramService = Depends(get_program_service),
    _: User = Depends(get_current_student),
) -> ProgramList:
    items, total = await service.list(
        page, limit, search=search, university_id=university_id, degree_level=degree_level, is_active=True
    )
    return ProgramList(items=items, total=total, page=page, limit=limit)


@router.get("/catalog/guides", response_model=CountryGuideList, summary="Destination guides")
async def browse_country_guides(
    page: int = 1,
    limit: int = 20,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> CountryGuideList:
    # Unpublished drafts must not reach students.
    query = select(CountryGuide).where(CountryGuide.is_published.is_(True))
    total = await repo.session.scalar(
        select(func.count()).select_from(CountryGuide).where(CountryGuide.is_published.is_(True))
    )
    result = await repo.session.scalars(
        query.order_by(CountryGuide.display_order, CountryGuide.title).limit(limit).offset((page - 1) * limit)
    )
    return CountryGuideList(
        items=[CountryGuideRead.model_validate(g) for g in result], total=total or 0, page=page, limit=limit
    )


@router.get("/catalog/blog", response_model=BlogPostList, summary="Articles")
async def browse_blog_posts(
    page: int = 1,
    limit: int = 20,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> BlogPostList:
    total = await repo.session.scalar(select(func.count()).select_from(BlogPost).where(BlogPost.is_published.is_(True)))
    result = await repo.session.scalars(
        select(BlogPost)
        .where(BlogPost.is_published.is_(True))
        .order_by(BlogPost.published_at.desc().nullslast())
        .limit(limit)
        .offset((page - 1) * limit)
    )
    return BlogPostList(
        items=[BlogPostRead.model_validate(b) for b in result], total=total or 0, page=page, limit=limit
    )


# --- Saved items and comparison ---------------------------------------------------------


async def _reload_with(repo: StudentScopedRepository, model: Any, entity_id: UUID, relationship: Any) -> Any:
    """Re-read a just-inserted row with its relationship loaded.

    `session.refresh` reloads columns but not relationships, so serializing the
    returned object would lazy-load outside the async greenlet.
    """
    return await repo.session.scalar(select(model).options(selectinload(relationship)).where(model.id == entity_id))


async def _exists_or_404(repo: StudentScopedRepository, model: Any, item_id: UUID, label: str) -> None:
    """Confirm the catalog item exists before saving a reference to it.

    Without this a typo'd id becomes a foreign-key error surfacing as a 500,
    instead of the 404 the client can act on.
    """
    if await repo.session.scalar(select(model.id).where(model.id == item_id)) is None:
        raise NotFoundException(f"{label} not found")


@router.get("/me/saved/courses", response_model=SavedCourseList, summary="My saved courses")
async def list_saved_courses(repo: StudentScopedRepository = Depends(get_student_repository)) -> SavedCourseList:
    items, total = await repo.list(
        StudentSavedCourse,
        column="student_id",
        order_by=StudentSavedCourse.created_at.desc(),
        options=[selectinload(StudentSavedCourse.program)],
        limit=100,
    )
    return SavedCourseList(items=[SavedCourseRead.model_validate(i) for i in items], total=total)


@router.post("/me/saved/courses", response_model=SavedCourseRead, summary="Save a course")
async def save_course(
    payload: SaveItemRequest,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> SavedCourseRead:
    await _exists_or_404(repo, Program, payload.item_id, "Course")
    existing = await repo.session.scalar(
        repo.scoped(StudentSavedCourse).where(StudentSavedCourse.program_id == payload.item_id)
    )
    if existing is not None:
        # Saving twice is the same intent as saving once — idempotent rather
        # than a 409 the UI would have to special-case.
        return SavedCourseRead.model_validate(
            await _reload_with(repo, StudentSavedCourse, existing.id, StudentSavedCourse.program)
        )

    saved = StudentSavedCourse(student_id=repo.student.id, program_id=payload.item_id)
    repo.session.add(saved)
    await repo.session.commit()
    loaded = await _reload_with(repo, StudentSavedCourse, saved.id, StudentSavedCourse.program)
    return SavedCourseRead.model_validate(loaded)


@router.delete("/me/saved/courses/{program_id}", summary="Unsave a course")
async def unsave_course(
    program_id: UUID,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> dict[str, bool]:
    saved = await repo.session.scalar(
        repo.scoped(StudentSavedCourse).where(StudentSavedCourse.program_id == program_id)
    )
    if saved is None:
        raise NotFoundException("Saved course not found")
    await repo.session.delete(saved)
    await repo.session.commit()
    return {"success": True}


@router.get("/me/saved/universities", response_model=SavedUniversityList, summary="My saved universities")
async def list_saved_universities(
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> SavedUniversityList:
    items, total = await repo.list(
        StudentSavedUniversity,
        column="student_id",
        order_by=StudentSavedUniversity.created_at.desc(),
        options=[selectinload(StudentSavedUniversity.university)],
        limit=100,
    )
    return SavedUniversityList(items=[SavedUniversityRead.model_validate(i) for i in items], total=total)


@router.post("/me/saved/universities", response_model=SavedUniversityRead, summary="Save a university")
async def save_university(
    payload: SaveItemRequest,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> SavedUniversityRead:
    await _exists_or_404(repo, University, payload.item_id, "University")
    existing = await repo.session.scalar(
        repo.scoped(StudentSavedUniversity).where(StudentSavedUniversity.university_id == payload.item_id)
    )
    if existing is not None:
        return SavedUniversityRead.model_validate(
            await _reload_with(repo, StudentSavedUniversity, existing.id, StudentSavedUniversity.university)
        )

    saved = StudentSavedUniversity(student_id=repo.student.id, university_id=payload.item_id)
    repo.session.add(saved)
    await repo.session.commit()
    loaded = await _reload_with(repo, StudentSavedUniversity, saved.id, StudentSavedUniversity.university)
    return SavedUniversityRead.model_validate(loaded)


@router.delete("/me/saved/universities/{university_id}", summary="Unsave a university")
async def unsave_university(
    university_id: UUID,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> dict[str, bool]:
    saved = await repo.session.scalar(
        repo.scoped(StudentSavedUniversity).where(StudentSavedUniversity.university_id == university_id)
    )
    if saved is None:
        raise NotFoundException("Saved university not found")
    await repo.session.delete(saved)
    await repo.session.commit()
    return {"success": True}


@router.get("/me/compare", response_model=CompareCourseList, summary="My comparison tray")
async def list_compare_courses(
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> CompareCourseList:
    items, total = await repo.list(
        StudentCompareCourse,
        column="student_id",
        order_by=StudentCompareCourse.created_at,
        options=[selectinload(StudentCompareCourse.program)],
        limit=MAX_COMPARE_COURSES,
    )
    return CompareCourseList(
        items=[SavedCourseRead.model_validate(i) for i in items], total=total, max_items=MAX_COMPARE_COURSES
    )


@router.post("/me/compare", response_model=SavedCourseRead, summary="Add a course to the comparison tray")
async def add_compare_course(
    payload: SaveItemRequest,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> SavedCourseRead:
    await _exists_or_404(repo, Program, payload.item_id, "Course")
    existing = await repo.session.scalar(
        repo.scoped(StudentCompareCourse).where(StudentCompareCourse.program_id == payload.item_id)
    )
    if existing is not None:
        return SavedCourseRead.model_validate(
            await _reload_with(repo, StudentCompareCourse, existing.id, StudentCompareCourse.program)
        )

    current = await repo.session.scalar(
        select(func.count()).select_from(StudentCompareCourse).where(StudentCompareCourse.student_id == repo.student.id)
    )
    # The cap lives here rather than in DDL: a per-student row limit is not
    # expressible as a CHECK without a trigger.
    if (current or 0) >= MAX_COMPARE_COURSES:
        raise BadRequestException(f"You can compare at most {MAX_COMPARE_COURSES} courses. Remove one first.")

    entry = StudentCompareCourse(student_id=repo.student.id, program_id=payload.item_id)
    repo.session.add(entry)
    await repo.session.commit()
    loaded = await _reload_with(repo, StudentCompareCourse, entry.id, StudentCompareCourse.program)
    return SavedCourseRead.model_validate(loaded)


@router.delete("/me/compare/{program_id}", summary="Remove a course from the comparison tray")
async def remove_compare_course(
    program_id: UUID,
    repo: StudentScopedRepository = Depends(get_student_repository),
) -> dict[str, bool]:
    entry = await repo.session.scalar(
        repo.scoped(StudentCompareCourse).where(StudentCompareCourse.program_id == program_id)
    )
    if entry is None:
        raise NotFoundException("Course not in the comparison tray")
    await repo.session.delete(entry)
    await repo.session.commit()
    return {"success": True}


# --- Progress and points (Phase 6) ------------------------------------------------

# Read-only by design. Progress and points move through the event subscribers in
# `app/core/subscribers.py`; there is deliberately no endpoint that awards
# points or completes a milestone, because a student must not be able to.


@router.get("/me/progress", response_model=ProgressRead, summary="My journey progress")
async def get_my_progress(
    student: User = Depends(get_current_student),
    service: ProgressService = Depends(get_progress_service),
) -> ProgressRead:
    pairs = await service.milestones_for(student.id)
    milestones = [
        MilestoneRead(
            key=milestone.key,
            label=milestone.label,
            description=milestone.description,
            weight=milestone.weight,
            order=milestone.order,
            is_complete=bool(standing and standing.is_complete),
            completed_at=standing.completed_at if standing else None,
        )
        for milestone, standing in pairs
    ]
    return ProgressRead(
        completion_percentage=await service.completion_percentage(student.id),
        next_milestone=next((m for m in milestones if not m.is_complete), None),
        milestones=milestones,
    )


@router.get("/me/points", response_model=PointsRead, summary="My points")
async def get_my_points(
    student: User = Depends(get_current_student),
    service: PointsService = Depends(get_points_service),
) -> PointsRead:
    return PointsRead(
        balance=await service.balance(student.id),
        history=[PointsEntryRead.model_validate(e) for e in await service.history(student.id)],
    )


# --- Journey checklist (Phase 6) --------------------------------------------------

# Writable, unlike progress and points: this is the student's own to-do list, and
# ticking "passport secured" is a claim about their life rather than a fact the
# system observed. What they cannot do is decide what a tick is *worth* — that
# runs through `ChecklistItemCompleted` and its subscriber.


def _checklist_payload(items: list[StudentChecklistItem], locked: set[str]) -> ChecklistRead:
    read = []
    for item in items:
        entry = ChecklistItemRead.model_validate(item)
        entry.is_complete = item.is_complete
        entry.is_locked = bool(item.key and item.key in locked)
        read.append(entry)
    return ChecklistRead(
        items=read,
        total=len(read),
        completed=sum(1 for entry in read if entry.is_complete),
    )


@router.get("/me/checklist", response_model=ChecklistRead, summary="My journey checklist")
async def list_my_checklist(
    student: User = Depends(get_current_student),
    service: ChecklistService = Depends(get_checklist_service),
) -> ChecklistRead:
    # The seeded items are materialised here rather than at signup, so a student
    # who registered before a rung existed still receives it.
    items = await service.list_items(student)
    return _checklist_payload(items, service.locked_keys(items))


@router.post("/me/checklist", response_model=ChecklistItemRead, summary="Add my own checklist item")
async def create_my_checklist_item(
    payload: ChecklistItemCreate,
    student: User = Depends(get_current_student),
    service: ChecklistService = Depends(get_checklist_service),
) -> ChecklistItemRead:
    item = await service.create_custom(
        student, title=payload.title, description=payload.description, due_date=payload.due_date
    )
    return ChecklistItemRead.model_validate(item)


@router.patch("/me/checklist/{item_id}", response_model=ChecklistItemRead, summary="Update a checklist item")
async def update_my_checklist_item(
    item_id: UUID,
    payload: ChecklistItemUpdate,
    student: User = Depends(get_current_student),
    repo: StudentScopedRepository = Depends(get_student_repository),
    service: ChecklistService = Depends(get_checklist_service),
) -> ChecklistItemRead:
    item = await repo.get_or_404(StudentChecklistItem, item_id, detail="Checklist item not found")
    fields = payload.model_dump(exclude_unset=True)

    # Re-dating is always the student's call — a deadline they set for
    # themselves. Rewording is not: a seeded rung's wording is the journey
    # everyone is measured against, and only their own items are theirs to edit.
    if ("title" in fields or "description" in fields) and not item.is_custom:
        raise BadRequestException("This is part of your journey. You can change its due date, but not its wording.")
    if "due_date" in fields:
        item.due_date = fields["due_date"]
    if fields.get("title") is not None:
        item.title = fields["title"]
    if "description" in fields:
        item.description = fields["description"]
    if fields.keys() & {"due_date", "title", "description"}:
        await repo.session.commit()
        await repo.session.refresh(item)

    if "completed" in fields and fields["completed"] is not None:
        item = await service.set_completed(student, item, fields["completed"])

    items = await service.list_items(student)
    entry = ChecklistItemRead.model_validate(item)
    entry.is_complete = item.is_complete
    entry.is_locked = bool(item.key and item.key in service.locked_keys(items))
    return entry


@router.delete("/me/checklist/{item_id}", summary="Remove one of my own checklist items")
async def delete_my_checklist_item(
    item_id: UUID,
    repo: StudentScopedRepository = Depends(get_student_repository),
    service: ChecklistService = Depends(get_checklist_service),
) -> dict[str, bool]:
    item = await repo.get_or_404(StudentChecklistItem, item_id, detail="Checklist item not found")
    await service.delete(item)
    return {"success": True}


# --- Mock interviews (Phase 6) -----------------------------------------------


@router.get("/catalog/interview-types", response_model=list[InterviewTypeRead], summary="Available interview types")
async def list_interview_types(
    service: InterviewService = Depends(get_interview_service),
    _: User = Depends(get_current_student),
) -> list[InterviewTypeRead]:
    return [InterviewTypeRead.model_validate(t) for t in await service.list_types()]


@router.get("/me/interviews", response_model=InterviewSessionList, summary="My interview sessions")
async def list_my_interviews(
    student: User = Depends(get_current_student),
    service: InterviewService = Depends(get_interview_service),
) -> InterviewSessionList:
    sessions = await service.list_sessions(student)
    return InterviewSessionList(
        items=[
            InterviewSessionSummary(
                id=s.id,
                status=s.status.value,
                started_at=s.started_at,
                completed_at=s.completed_at,
                score=s.score,
                type_name=s.type.name,
                feedback_band=s.feedback_band.band if s.feedback_band else None,
            )
            for s in sessions
        ],
        total=len(sessions),
    )


@router.post("/me/interviews", response_model=InterviewSessionRead, summary="Start a mock interview")
async def start_my_interview(
    payload: InterviewSessionCreate,
    student: User = Depends(get_current_student),
    service: InterviewService = Depends(get_interview_service),
) -> InterviewSessionRead:
    session_row = await service.start_session(student, payload.type_id)
    return InterviewSessionRead.model_validate(session_row)


@router.get("/me/interviews/{session_id}", response_model=InterviewSessionRead, summary="One interview session")
async def get_my_interview(
    session_id: UUID,
    student: User = Depends(get_current_student),
    service: InterviewService = Depends(get_interview_service),
) -> InterviewSessionRead:
    session_row = await service.get_session(student, session_id)
    return InterviewSessionRead.model_validate(session_row)


@router.post("/me/interviews/{session_id}/answers", response_model=InterviewAnswerRead, summary="Answer one question")
async def answer_my_interview_question(
    session_id: UUID,
    payload: InterviewAnswerCreate,
    student: User = Depends(get_current_student),
    service: InterviewService = Depends(get_interview_service),
) -> InterviewAnswerRead:
    answer = await service.submit_answer(student, session_id, payload.question_id, payload.answer_text)
    return InterviewAnswerRead.model_validate(answer)


@router.post("/me/interviews/{session_id}/complete", response_model=InterviewSessionRead, summary="Finish and score")
async def complete_my_interview(
    session_id: UUID,
    student: User = Depends(get_current_student),
    service: InterviewService = Depends(get_interview_service),
) -> InterviewSessionRead:
    session_row = await service.complete_session(student, session_id)
    return InterviewSessionRead.model_validate(session_row)


# --- Visa and pre-departure (Phase 6) -----------------------------------------

# No creation route: a `VisaCase` is opened by staff when an application
# reaches the visa stage, the same way an `Application` is opened by a
# counsellor rather than a student.


@router.get("/me/visa", response_model=VisaCaseRead, summary="My visa case")
async def get_my_visa_case(
    student: User = Depends(get_current_student),
    service: VisaService = Depends(get_visa_service),
) -> VisaCaseRead:
    return VisaCaseRead.model_validate(await service.get_case(student))


@router.post(
    "/me/visa/appointments/{appointment_id}/book",
    response_model=VisaAppointmentRead,
    summary="Book a visa appointment",
)
async def book_my_visa_appointment(
    appointment_id: UUID,
    payload: VisaAppointmentBook,
    student: User = Depends(get_current_student),
    service: VisaService = Depends(get_visa_service),
) -> VisaAppointmentRead:
    appointment = await service.book_appointment(student, appointment_id, payload.scheduled_at)
    return VisaAppointmentRead.model_validate(appointment)


@router.post(
    "/me/visa/appointments/{appointment_id}/cancel",
    response_model=VisaAppointmentRead,
    summary="Cancel a visa appointment",
)
async def cancel_my_visa_appointment(
    appointment_id: UUID,
    student: User = Depends(get_current_student),
    service: VisaService = Depends(get_visa_service),
) -> VisaAppointmentRead:
    appointment = await service.cancel_appointment(student, appointment_id)
    return VisaAppointmentRead.model_validate(appointment)


@router.patch("/me/visa/fees/{fee_id}", response_model=VisaFeeRead, summary="Mark a visa fee paid")
async def update_my_visa_fee(
    fee_id: UUID,
    payload: VisaFeeUpdate,
    student: User = Depends(get_current_student),
    service: VisaService = Depends(get_visa_service),
) -> VisaFeeRead:
    fee = await service.mark_fee_paid(student, fee_id, payload.paid)
    return VisaFeeRead.model_validate(fee)


@router.patch(
    "/me/visa/departure-checklist/{item_id}",
    response_model=DepartureChecklistItemRead,
    summary="Tick a pre-departure item",
)
async def update_my_departure_checklist_item(
    item_id: UUID,
    payload: DepartureChecklistItemUpdate,
    student: User = Depends(get_current_student),
    service: VisaService = Depends(get_visa_service),
) -> DepartureChecklistItemRead:
    item = await service.set_departure_item_completed(student, item_id, payload.completed)
    return DepartureChecklistItemRead.model_validate(item)


# --- Finance (Phase 6) ---------------------------------------------------------

# Almost everything here is student-writable: a funding source, a budget, a
# savings goal are the student's own plan, not facts the platform observed.
# `StudentLoan` is the one exception — the lender's numbers are read-only —
# and real money that moves through the platform stays on `/me/payments`
# (Phase 5a's `Payment`), never duplicated here.


@router.get("/me/finance/funding-sources", response_model=list[FundingSourceRead], summary="My funding sources")
async def list_my_funding_sources(
    student: User = Depends(get_current_student),
    service: FundingSourceService = Depends(get_funding_source_service),
) -> list[FundingSourceRead]:
    return [FundingSourceRead.model_validate(s) for s in await service.list_for(student)]


@router.post("/me/finance/funding-sources", response_model=FundingSourceRead, summary="Add a funding source")
async def create_my_funding_source(
    payload: FundingSourceCreate,
    student: User = Depends(get_current_student),
    service: FundingSourceService = Depends(get_funding_source_service),
) -> FundingSourceRead:
    source = await service.create(student, payload.source_type, payload.provider, payload.amount_usd, payload.remarks)
    return FundingSourceRead.model_validate(source)


@router.patch(
    "/me/finance/funding-sources/{source_id}", response_model=FundingSourceRead, summary="Edit a funding source"
)
async def update_my_funding_source(
    source_id: UUID,
    payload: FundingSourceUpdate,
    student: User = Depends(get_current_student),
    service: FundingSourceService = Depends(get_funding_source_service),
) -> FundingSourceRead:
    fields = payload.model_dump(exclude_unset=True)
    source = await service.update(
        student,
        source_id,
        provider=fields.get("provider"),
        amount_usd=fields.get("amount_usd"),
        remarks=fields.get("remarks"),
    )
    return FundingSourceRead.model_validate(source)


@router.delete("/me/finance/funding-sources/{source_id}", summary="Remove a funding source")
async def delete_my_funding_source(
    source_id: UUID,
    student: User = Depends(get_current_student),
    service: FundingSourceService = Depends(get_funding_source_service),
) -> dict[str, bool]:
    await service.delete(student, source_id)
    return {"success": True}


@router.get("/me/finance/loan", response_model=LoanRead, summary="My education loan")
async def get_my_loan(
    student: User = Depends(get_current_student),
    service: LoanService = Depends(get_loan_service),
) -> LoanRead:
    return LoanRead.model_validate(await service.get_for(student))


@router.get("/me/finance/budget", response_model=BudgetRead, summary="My monthly budget")
async def get_my_budget(
    student: User = Depends(get_current_student),
    service: BudgetService = Depends(get_budget_service),
) -> BudgetRead:
    return BudgetRead.model_validate(await service.get_or_create(student))


@router.put("/me/finance/budget", response_model=BudgetRead, summary="Replan my monthly budget")
async def replace_my_budget(
    payload: BudgetReplace,
    student: User = Depends(get_current_student),
    service: BudgetService = Depends(get_budget_service),
) -> BudgetRead:
    budget = await service.replace(
        student, payload.planned_monthly_income, [c.model_dump() for c in payload.categories]
    )
    return BudgetRead.model_validate(budget)


@router.get("/me/finance/savings", response_model=list[SavingsGoalRead], summary="My savings goal and emergency fund")
async def list_my_savings(
    student: User = Depends(get_current_student),
    service: SavingsService = Depends(get_savings_service),
) -> list[SavingsGoalRead]:
    return [SavingsGoalRead.model_validate(g) for g in await service.list_for(student)]


@router.put(
    "/me/finance/savings/{kind}", response_model=SavingsGoalRead, summary="Set my savings goal or emergency fund"
)
async def upsert_my_savings(
    kind: SavingsGoalKind,
    payload: SavingsGoalUpsert,
    student: User = Depends(get_current_student),
    service: SavingsService = Depends(get_savings_service),
) -> SavingsGoalRead:
    goal = await service.upsert(
        student,
        kind,
        current_savings=payload.current_savings,
        monthly_contribution=payload.monthly_contribution,
        target_date=payload.target_date,
        recommended_months=payload.recommended_months,
    )
    return SavingsGoalRead.model_validate(goal)


@router.get(
    "/catalog/countries/{country_id}/cost-of-living",
    response_model=CountryCostOfLivingRead,
    summary="Cost of living for a destination",
)
async def get_country_cost_of_living(
    country_id: UUID,
    service: FinanceCatalogService = Depends(get_finance_catalog_service),
    _: User = Depends(get_current_student),
) -> CountryCostOfLivingRead:
    return CountryCostOfLivingRead.model_validate(await service.cost_of_living_for(country_id))


@router.get("/catalog/currency-rates", response_model=list[CurrencyRateRead], summary="Currency rates against USD")
async def list_currency_rates(
    service: FinanceCatalogService = Depends(get_finance_catalog_service),
    _: User = Depends(get_current_student),
) -> list[CurrencyRateRead]:
    return [CurrencyRateRead.model_validate(r) for r in await service.currency_rates()]


# --- Activity feed (Phase 6) ---------------------------------------------------

# No new tables — a projection over ActivityLog and ApplicationStatusHistory,
# scoped to the student through the entity each row names.


@router.get("/me/activity", response_model=ActivityList, summary="My activity feed")
async def list_my_activity(
    limit: int = 50,
    student: User = Depends(get_current_student),
    service: ActivityService = Depends(get_activity_service),
) -> ActivityList:
    entries = await service.list_for(student, limit=limit)
    return ActivityList(items=[ActivityEntryRead.model_validate(e) for e in entries], total=len(entries))


# --- Dashboard preferences (Phase 6) --------------------------------------------


@router.get("/me/preferences", response_model=DashboardSettingsRead, summary="My dashboard preferences")
async def get_my_preferences(
    student: User = Depends(get_current_student),
    service: PreferencesService = Depends(get_preferences_service),
) -> DashboardSettingsRead:
    return DashboardSettingsRead.model_validate(await service.get_or_create(student))


@router.patch("/me/preferences", response_model=DashboardSettingsRead, summary="Update my dashboard preferences")
async def update_my_preferences(
    payload: DashboardSettingsUpdate,
    student: User = Depends(get_current_student),
    service: PreferencesService = Depends(get_preferences_service),
) -> DashboardSettingsRead:
    settings = await service.update(student, payload.model_dump(exclude_unset=True))
    return DashboardSettingsRead.model_validate(settings)


# --- Dashboard (Phase 6) --------------------------------------------------------

# The home screen, fanned out in one call: everything below already has its
# own endpoint, and this is the aggregate a page load actually wants instead
# of eight round trips. Redis-cached; invalidated by the same event
# subscribers that drive progress, points, and notifications.


@router.get("/me/dashboard", response_model=DashboardRead, summary="My dashboard")
async def get_my_dashboard(
    student: User = Depends(get_current_student),
    service: DashboardService = Depends(get_dashboard_service),
) -> DashboardRead:
    return await service.get_dashboard(student)
