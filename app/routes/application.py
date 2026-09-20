from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, UploadFile

from ..api.auth import require_role
from ..api.exceptions import BadRequestException, ForbiddenException, NotFoundException
from ..api.scoping import may_see_record, own_work_scope
from ..core.uploads import DOCUMENT_EXTENSIONS, DOCUMENT_FOLDER, store_upload
from ..models import Application, Document, User
from ..models.enums import ApplicationStatus, DocumentStatus, DocumentType, UserRole
from ..schemas.application import (
    ApplicationCreate,
    ApplicationList,
    ApplicationRead,
    ApplicationStatusHistoryRead,
    ApplicationStatusUpdate,
    ApplicationUpdate,
    StatusRequirementRead,
)
from ..services.application_service import ApplicationService, get_application_service
from ..services.document_service import DocumentService, get_document_service
from ..services.milestone_service import MilestoneService, get_milestone_service
from ..services.notification_service import NotificationService, get_notification_service
from ..services.status_requirements import requirement_for, requirements_payload

router = APIRouter(prefix="/applications", tags=["Applications"])

#: Roles that may see applications at all. Students are included because an
#: applicant tracks their own application here — every handler below therefore
#: has to narrow a student to their own rows.
_VIEW_ROLES = require_role(UserRole.ADMIN, UserRole.COUNSELLOR, UserRole.ADMISSIONS, UserRole.STUDENT)

#: Staff who may create and edit applications on a student's behalf.
_MANAGE_ROLES = require_role(UserRole.ADMIN, UserRole.COUNSELLOR, UserRole.ADMISSIONS)


def _assert_visible_to(user: User, application: Application) -> None:
    """The by-id counterpart of the list's scoping.

    Two different rules, because the two roles are wrong in different ways: a
    student reaching for another student's file is a straightforward refusal,
    while a counsellor reaching for a colleague's file must not even learn that
    it exists — otherwise the narrowed list is undone by typing an id into the
    address bar, and the id becomes an oracle for the agency's caseload.
    """
    if user.role is UserRole.STUDENT:
        if application.student_id != user.id:
            raise ForbiddenException("Forbidden")
        return
    if not may_see_record(user, application.counsellor_id):
        raise NotFoundException("Application not found")


@router.get("", response_model=ApplicationList, summary="List applications")
async def list_applications(
    page: int = 1,
    limit: int = 20,
    student_id: UUID | None = None,
    counsellor_id: UUID | None = None,
    program_id: UUID | None = None,
    status: str | None = None,
    search: str | None = None,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(_VIEW_ROLES),
) -> ApplicationList:
    # Overwritten, not defaulted: a student passing someone else's student_id
    # still gets only their own.
    if user.role is UserRole.STUDENT:
        student_id = user.id

    applications, total = await service.list_applications(
        page,
        limit,
        student_id=student_id,
        counsellor_id=counsellor_id,
        program_id=program_id,
        status=status,
        search=search,
        # A student is already pinned to their own rows above; scoping is about
        # which *staff* see which files.
        visible_to=None if user.role is UserRole.STUDENT else own_work_scope(user),
    )
    return ApplicationList(
        items=[ApplicationRead.model_validate(a) for a in applications],
        total=total,
        page=page,
        limit=limit,
    )


@router.get(
    "/status-requirements",
    response_model=list[StatusRequirementRead],
    summary="What each milestone status needs",
)
async def get_status_requirements(
    user: User = Depends(_MANAGE_ROLES),
) -> list[StatusRequirementRead]:
    """The config behind the status dialog.

    Served rather than duplicated in TypeScript so the form and the validator
    cannot disagree about what recording an offer requires.

    **Declared before `/{application_id}`, not merely before
    `/{application_id}/status`.** It used to sit between the two, which reads
    right and is wrong: a literal segment only wins if its route is registered
    first, and `/{application_id}` is registered above. Every call therefore
    parsed "status-requirements" as an id and came back 422 — so the console
    never learned which statuses need a date and a letter, offered
    `offer_received` on the plain status dropdown, and handed the counsellor
    the backend's refusal ("Use POST /applications/{id}/milestone") as if it
    were advice they could act on. `tests/test_applications.py` now pins the
    order.
    """
    return [StatusRequirementRead(**item) for item in requirements_payload()]


@router.get("/{application_id}", response_model=ApplicationRead, summary="Get application")
async def get_application(
    application_id: UUID,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(_VIEW_ROLES),
) -> ApplicationRead:
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    _assert_visible_to(user, application)
    return ApplicationRead.model_validate(application)


@router.post("", response_model=ApplicationRead, summary="Create application")
async def create_application(
    payload: ApplicationCreate,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(_MANAGE_ROLES),
) -> ApplicationRead:
    """Staff only.

    ED360 guards this with a bare `get_current_user`, so any authenticated
    account can post an application naming an arbitrary `student_id`,
    `counsellor_id` and `status` — including a student filing one against
    another student, or opening their own already marked `enrolled`. Students
    get their own application-submission flow in Phase 5; it does not run
    through this staff endpoint.

    **Staff-created applications start at `draft`** — the first phase of the
    journey, "Preparing" — because a counsellor opening a file has already
    decided to work it. Only the student-facing route opens one as `requested`,
    which is the queue this endpoint's caller is on the other side of.
    """
    application = await service.create_application(payload.model_dump())
    return ApplicationRead.model_validate(application)


@router.post(
    "/{application_id}/accept",
    response_model=ApplicationRead,
    summary="Accept an application a student requested",
)
async def accept_application_request(
    application_id: UUID,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(_MANAGE_ROLES),
) -> ApplicationRead:
    """Take a student's request off the queue and start work on it.

    `requested` → `draft`, through `change_application_status`, so the
    acceptance lands in `application_status_history` with the counsellor's id
    against it like every other transition. That matters more here than
    elsewhere: "who agreed to work this, and when" is exactly the question a
    queue exists to answer.

    A separate endpoint rather than letting the generic status route do it,
    because the two say different things. Setting a status is bookkeeping;
    accepting a request is a commitment, and the console should be able to
    offer it as one button rather than as "change the dropdown to Draft", which
    is not a sentence anybody would say out loud.

    Idempotent-ish: accepting something already past `requested` is a
    no-op rather than an error, so a double-click cannot rewind a file that has
    moved on.
    """
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    _assert_visible_to(user, application)

    if application.status is not ApplicationStatus.REQUESTED:
        return ApplicationRead.model_validate(application)

    updated = await service.change_application_status(
        application,
        ApplicationStatus.DRAFT,
        performed_by=user.id,
        remarks="Request accepted",
    )
    return ApplicationRead.model_validate(updated)


@router.post("/{application_id}/status", response_model=ApplicationRead, summary="Update application status")
async def change_application_status(
    application_id: UUID,
    payload: ApplicationStatusUpdate,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.COUNSELLOR)),
) -> ApplicationRead:
    """The plain path: a status and a note.

    Refuses the milestone statuses. They need a date and a document, and
    letting them through here is exactly how an application ended up saying an
    offer existed with nothing behind it — so the refusal names the endpoint
    that does it properly rather than silently accepting half a milestone.
    """
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    _assert_visible_to(user, application)

    if requirement_for(payload.status) is not None:
        raise BadRequestException(
            f"'{payload.status.value}' records something the university issued, so it needs a date "
            "and its letter. Use POST /applications/{id}/milestone."
        )

    updated = await service.change_application_status(
        application,
        payload.status,
        performed_by=user.id,
        remarks=payload.remarks,
    )
    return ApplicationRead.model_validate(updated)


@router.post(
    "/{application_id}/milestone",
    response_model=ApplicationRead,
    summary="Record an offer, a CAS or a visa decision",
)
async def record_application_milestone(
    application_id: UUID,
    status: ApplicationStatus = Form(...),
    remarks: str | None = Form(None),
    offer_received_date: str | None = Form(None),
    cas_received_date: str | None = Form(None),
    visa_decision_date: str | None = Form(None),
    offer_type: str | None = Form(None),
    cas_number: str | None = Form(None),
    tuition_fee: str | None = Form(None),
    scholarship_amount: str | None = Form(None),
    letter: UploadFile | None = File(None),
    applications: ApplicationService = Depends(get_application_service),
    milestones: MilestoneService = Depends(get_milestone_service),
    documents: DocumentService = Depends(get_document_service),
    notifications: NotificationService = Depends(get_notification_service),
    user: User = Depends(_MANAGE_ROLES),
) -> ApplicationRead:
    """One request: the status, the date, the letter and the milestone.

    Multipart because the letter travels with it. That is the whole point — the
    date used to live behind a different dialog and the letter in an unrelated
    upload, so the normal outcome of recording an offer was an application
    claiming one with no evidence attached.

    Ordering is `MilestoneService.record`'s business and documented there; what
    happens here is the one step that cannot join a transaction. The file is
    stored *after* the required fields are known to be present, so a request
    missing a date never writes bytes, and the document is created only if
    there is one to create.
    """
    application = await applications.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")

    requirement = requirement_for(status)
    if requirement is None:
        raise BadRequestException(
            f"'{status.value}' is not a milestone status. Use POST /applications/{{id}}/status."
        )

    fields = {
        key: value
        for key, value in {
            "offer_received_date": offer_received_date,
            "cas_received_date": cas_received_date,
            "visa_decision_date": visa_decision_date,
            "offer_type": offer_type,
            "cas_number": cas_number,
            "tuition_fee": tuition_fee,
            "scholarship_amount": scholarship_amount,
        }.items()
        if value not in (None, "")
    }

    # Cheap validation before the upload: a request with no date must not leave
    # a file in Cloudinary. `record` re-checks everything — this is the early
    # exit, not the authority.
    if requirement.required_date_field and not fields.get(requirement.required_date_field):
        raise BadRequestException(
            f"Record the date before saving '{status.value.replace('_', ' ')}'."
        )

    document: Document | None = None
    if letter is not None and letter.filename:
        stored = await store_upload(
            letter, DOCUMENT_EXTENSIONS, folder=DOCUMENT_FOLDER, private=True
        )
        document_id = uuid4()
        document = await documents.create_document(
            {
                "id": document_id,
                "student_id": application.student_id,
                "uploaded_by": user.id,
                "document_type": requirement.required_document or DocumentType.OTHER,
                "title": requirement.document_label or letter.filename,
                "original_file_name": letter.filename,
                "stored_file_name": stored.stored_file_name,
                "file_url": f"/api/v1/documents/{document_id}/download",
                "mime_type": letter.content_type,
                "file_size": stored.size,
                # Staff filing what the university sent *is* the verification —
                # there is nobody left to check it against. Same rule as
                # `POST /documents/upload`.
                "status": DocumentStatus.APPROVED,
                "verified_by": user.id,
                "verified_at": datetime.now(UTC),
            }
        )

    updated = await milestones.record(
        application,
        status,
        fields=fields,
        document=document,
        performed_by=user.id,
        remarks=remarks,
    )
    return ApplicationRead.model_validate(updated)


@router.get(
    "/{application_id}/status-history",
    response_model=list[ApplicationStatusHistoryRead],
    summary="Get application status history",
)
async def get_application_status_history(
    application_id: UUID,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(_VIEW_ROLES),
) -> list[ApplicationStatusHistoryRead]:
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    _assert_visible_to(user, application)
    history = await service.list_status_history(application_id)
    return [ApplicationStatusHistoryRead.model_validate(entry) for entry in history]


@router.patch("/{application_id}", response_model=ApplicationRead, summary="Update application")
async def update_application(
    application_id: UUID,
    payload: ApplicationUpdate,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.COUNSELLOR)),
) -> ApplicationRead:
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    _assert_visible_to(user, application)
    updated = await service.update_application(application, payload.model_dump(exclude_unset=True))
    return ApplicationRead.model_validate(updated)


@router.delete("/{application_id}", response_model=ApplicationRead, summary="Delete application")
async def delete_application(
    application_id: UUID,
    service: ApplicationService = Depends(get_application_service),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> ApplicationRead:
    application = await service.get_application(application_id)
    if application is None:
        raise NotFoundException("Application not found")
    return ApplicationRead.model_validate(await service.delete_application(application))
