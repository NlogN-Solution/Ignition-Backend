"""Correspondence, for staff and students, over one thread store.

Two role-shaped surfaces on one service, which is the whole point: the lead
page, the student page, the application tab and the student's own mailbox are
four views of the *same* rows, not four stores that have to be kept in step.

Authorisation is asymmetric and deliberately so. Staff may read any thread and
may write internal notes; a student may read a thread only if it is theirs
**and** shared. That second check runs through
`CommunicationService.student_can_see`, never inline here, because "theirs"
means "theirs or their pre-conversion lead's" and that is not something a
route handler should be re-deriving.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response

from ..api.auth import get_current_user, require_role
from ..api.exceptions import BadRequestException, ForbiddenException, NotFoundException
from ..core.uploads import (
    MESSAGE_ATTACHMENT_EXTENSIONS,
    MESSAGE_ATTACHMENT_FOLDER,
    build_download_response,
    build_download_url,
    store_upload,
)
from ..models import MessageAttachment, ThreadMessage, User
from ..models.communication import MessageAttachmentKind
from ..models.enums import UserRole
from ..schemas.communication import (
    ThreadCreate,
    ThreadDetail,
    ThreadList,
    ThreadMessageRead,
    ThreadRead,
)
from ..schemas.document import DocumentLinkRead
from ..services.communication_service import CommunicationService, get_communication_service

router = APIRouter(prefix="/communication", tags=["Communication"])

#: Staff who work correspondence. Same list as the documents review roles —
#: anybody who can see a student's file can talk to them about it.
_STAFF = require_role(
    UserRole.ADMIN,
    UserRole.SUPER_ADMIN,
    UserRole.COUNSELLOR,
    UserRole.ADMISSIONS,
    UserRole.MANAGER,
)

#: How many attachments one message may carry. Not a storage limit — a limit on
#: how much a single reply can be, so a thread stays readable.
MAX_ATTACHMENTS = 10


async def _voice_or_file(upload: UploadFile) -> MessageAttachmentKind:
    """Classify an attachment from its declared type.

    Used only to decide what the client renders — a player, a thumbnail or a
    download link. It is not a security boundary: what may be stored at all is
    decided by extension in `store_upload`, against an allowlist.
    """
    mime = (upload.content_type or "").lower()
    if mime.startswith("audio/"):
        return MessageAttachmentKind.VOICE
    if mime.startswith("image/"):
        return MessageAttachmentKind.IMAGE
    return MessageAttachmentKind.FILE


async def _store_attachments(
    files: list[UploadFile],
    voice: UploadFile | None,
    voice_duration: int | None,
) -> list[dict]:
    if len(files) + (1 if voice else 0) > MAX_ATTACHMENTS:
        raise BadRequestException(f"A message can carry at most {MAX_ATTACHMENTS} attachments.")

    stored: list[dict] = []
    for upload in files:
        if not upload.filename:
            continue
        result = await store_upload(
            upload,
            MESSAGE_ATTACHMENT_EXTENSIONS,
            folder=MESSAGE_ATTACHMENT_FOLDER,
            private=True,
        )
        stored.append(
            {
                "kind": await _voice_or_file(upload),
                "original_file_name": upload.filename,
                "stored_file_name": result.stored_file_name,
                "mime_type": upload.content_type,
                "file_size": result.size,
            }
        )

    if voice is not None and voice.filename:
        result = await store_upload(
            voice,
            MESSAGE_ATTACHMENT_EXTENSIONS,
            folder=MESSAGE_ATTACHMENT_FOLDER,
            private=True,
        )
        stored.append(
            {
                "kind": MessageAttachmentKind.VOICE,
                "original_file_name": voice.filename,
                "stored_file_name": result.stored_file_name,
                "mime_type": voice.content_type,
                "file_size": result.size,
                "duration_seconds": voice_duration,
            }
        )
    return stored


# --- Staff surface ------------------------------------------------------------------


@router.get("/threads", response_model=ThreadList, summary="Staff mailbox")
async def list_threads(
    search: str | None = None,
    unread_only: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    service: CommunicationService = Depends(get_communication_service),
    user: User = Depends(_STAFF),
) -> ThreadList:
    threads, total = await service.staff_inbox(
        search=search, unread_only=unread_only, page=page, limit=limit
    )
    return ThreadList(
        items=[ThreadRead.of(thread, viewer_is_student=False) for thread in threads],
        total=total,
        page=page,
        limit=limit,
    )


@router.get(
    "/leads/{lead_id}/threads",
    response_model=list[ThreadRead],
    summary="A lead's correspondence, including after conversion",
)
async def list_lead_threads(
    lead_id: UUID,
    service: CommunicationService = Depends(get_communication_service),
    user: User = Depends(_STAFF),
) -> list[ThreadRead]:
    """The lead page's Communication tab.

    Includes threads opened *after* the lead converted, resolved through
    `leads.converted_user_id`. Without that the discontinuity simply points the
    other way: the student page would show everything and the lead page would
    stop at the conversion date.
    """
    threads = await service.threads_for_lead(lead_id)
    return [ThreadRead.of(thread, viewer_is_student=False) for thread in threads]


@router.get(
    "/students/{student_id}/threads",
    response_model=list[ThreadRead],
    summary="A student's correspondence, including from before they converted",
)
async def list_student_threads(
    student_id: UUID,
    include_internal: bool = True,
    service: CommunicationService = Depends(get_communication_service),
    user: User = Depends(_STAFF),
) -> list[ThreadRead]:
    """The student page's Communication tab.

    This is the screen the whole feature exists for. It used to open empty for
    every converted lead, because the conversation was filed under the lead and
    nothing joined the two. `threads_for_student` resolves by person, so three
    weeks of pre-application correspondence is still here.
    """
    threads = await service.threads_for_student(student_id, include_internal=include_internal)
    return [ThreadRead.of(thread, viewer_is_student=False) for thread in threads]


@router.get(
    "/applications/{application_id}/threads",
    response_model=list[ThreadRead],
    summary="Correspondence about one application",
)
async def list_application_threads(
    application_id: UUID,
    service: CommunicationService = Depends(get_communication_service),
    user: User = Depends(_STAFF),
) -> list[ThreadRead]:
    threads = await service.threads_for_application(application_id)
    return [ThreadRead.of(thread, viewer_is_student=False) for thread in threads]


@router.post("/threads", response_model=ThreadDetail, status_code=201, summary="Open a thread")
async def create_thread(
    payload: ThreadCreate,
    service: CommunicationService = Depends(get_communication_service),
    user: User = Depends(_STAFF),
) -> ThreadDetail:
    if payload.student_id is None and payload.lead_id is None:
        raise BadRequestException("A thread needs either a student or a lead.")

    thread = await service.create_thread(
        subject=payload.subject,
        student_id=payload.student_id,
        lead_id=payload.lead_id,
        application_id=payload.application_id,
        visibility=payload.visibility,
        created_by=user.id,
    )
    await service.post_message(
        thread,
        author=user,
        body=payload.body,
        body_html=payload.body_html,
        is_from_student=False,
    )
    return ThreadDetail.of(await service.get_thread(thread.id), viewer_is_student=False)


@router.get("/threads/{thread_id}", response_model=ThreadDetail, summary="One thread")
async def get_thread(
    thread_id: UUID,
    service: CommunicationService = Depends(get_communication_service),
    user: User = Depends(get_current_user),
) -> ThreadDetail:
    thread = await service.get_thread(thread_id)
    if thread is None:
        raise NotFoundException("Thread not found")

    is_student = user.role is UserRole.STUDENT
    if is_student and not await service.student_can_see(thread, user.id):
        # 404, not 403: whether a thread exists is itself information about
        # somebody else's correspondence.
        raise NotFoundException("Thread not found")
    if not is_student and user.role not in _STAFF_ROLES:
        raise ForbiddenException("Forbidden")

    await service.mark_read(thread, by_student=is_student)
    return ThreadDetail.of(await service.get_thread(thread_id), viewer_is_student=is_student)


@router.post(
    "/threads/{thread_id}/messages",
    response_model=ThreadMessageRead,
    status_code=201,
    summary="Reply in a thread",
)
async def post_message(
    thread_id: UUID,
    body: str = Form(..., min_length=1),
    body_html: str | None = Form(None),
    files: list[UploadFile] = File(default_factory=list),
    voice: UploadFile | None = File(None),
    voice_duration: int | None = Form(None),
    service: CommunicationService = Depends(get_communication_service),
    user: User = Depends(get_current_user),
) -> ThreadMessageRead:
    """Multipart, always — see the note where `MessageCreate` would have been.

    Both sides use this one route. `is_from_student` is taken from the caller's
    role rather than from the request, so a student cannot post a message
    attributed to their counsellor.
    """
    thread = await service.get_thread(thread_id)
    if thread is None:
        raise NotFoundException("Thread not found")

    is_student = user.role is UserRole.STUDENT
    if is_student:
        if not await service.student_can_see(thread, user.id):
            raise NotFoundException("Thread not found")
    elif user.role not in _STAFF_ROLES:
        raise ForbiddenException("Forbidden")

    if thread.is_closed:
        raise BadRequestException("This conversation has been closed.")

    attachments = await _store_attachments(files, voice, voice_duration)
    message = await service.post_message(
        thread,
        author=user,
        body=body,
        body_html=body_html,
        is_from_student=is_student,
        attachments=attachments,
    )
    return ThreadMessageRead.model_validate(message)


@router.get(
    "/attachments/{attachment_id}/link",
    response_model=DocumentLinkRead,
    summary="A signed link to an attachment",
)
async def get_attachment_link(
    attachment_id: UUID,
    disposition: str = "inline",
    service: CommunicationService = Depends(get_communication_service),
    user: User = Depends(get_current_user),
) -> DocumentLinkRead:
    """Same shape and the same reasoning as the document link endpoint.

    The attachment's own URL is authenticated, so a `window.open` on it 401s;
    this hands back a signed URL after checking the caller may see the thread
    the attachment hangs off. Ownership is resolved through the thread, never
    from the attachment id — an id is not a permission.
    """
    attachment, thread = await _attachment_for(service, attachment_id, user)
    url = build_download_url(
        attachment.stored_file_name,
        folder=MESSAGE_ATTACHMENT_FOLDER,
        download_name=attachment.original_file_name,
        inline=disposition != "attachment",
    )
    return DocumentLinkRead(
        url=url or f"/api/v1/communication/attachments/{attachment_id}/download",
        file_name=attachment.original_file_name,
        mime_type=attachment.mime_type,
    )


@router.get("/attachments/{attachment_id}/download", summary="Download an attachment")
async def download_attachment(
    attachment_id: UUID,
    disposition: str = "attachment",
    service: CommunicationService = Depends(get_communication_service),
    user: User = Depends(get_current_user),
) -> Response:
    attachment, thread = await _attachment_for(service, attachment_id, user)
    return build_download_response(
        attachment.stored_file_name,
        folder=MESSAGE_ATTACHMENT_FOLDER,
        mime_type=attachment.mime_type,
        download_name=attachment.original_file_name,
        inline=disposition == "inline",
    )


#: Staff roles, as a plain set for the inline checks above. `require_role`
#: returns a dependency, which cannot also be used as a membership test.
_STAFF_ROLES = frozenset(
    {
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
        UserRole.COUNSELLOR,
        UserRole.ADMISSIONS,
        UserRole.MANAGER,
    }
)


async def _attachment_for(
    service: CommunicationService, attachment_id: UUID, user: User
) -> tuple[MessageAttachment, object]:
    """Load an attachment and authorise it through its thread."""
    from sqlalchemy import select  # local: this is the only query in the module

    attachment = await service.session.scalar(
        select(MessageAttachment).where(MessageAttachment.id == attachment_id)
    )
    if attachment is None:
        raise NotFoundException("Attachment not found")
    message = await service.session.scalar(
        select(ThreadMessage).where(ThreadMessage.id == attachment.message_id)
    )
    thread = await service.get_thread(message.thread_id) if message else None
    if thread is None:
        raise NotFoundException("Attachment not found")

    if user.role is UserRole.STUDENT:
        if not await service.student_can_see(thread, user.id):
            raise NotFoundException("Attachment not found")
    elif user.role not in _STAFF_ROLES:
        raise ForbiddenException("Forbidden")
    return attachment, thread
