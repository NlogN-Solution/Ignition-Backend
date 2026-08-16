from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from ..api.auth import require_role
from ..api.exceptions import ForbiddenException, NotFoundException
from ..core.uploads import DOCUMENT_EXTENSIONS, resolve_stored_path, store_upload
from ..models import Document, User
from ..models.enums import DocumentType, NotificationType, UserRole
from ..schemas.document import (
    DocumentCommentRequest,
    DocumentCreate,
    DocumentExtractionResult,
    DocumentFolderList,
    DocumentFolderRead,
    DocumentList,
    DocumentRead,
    DocumentRejectRequest,
    DocumentUpdate,
    DocumentVerifyRequest,
)
from ..services.document_service import DocumentService, get_document_service
from ..services.notification_service import NotificationService, get_notification_service

router = APIRouter(prefix="/documents", tags=["Documents"])

#: Everyone who may reach the document surface — students included, since an
#: applicant uploads and tracks their own paperwork here.
_VIEW_ROLES = require_role(
    UserRole.ADMIN,
    UserRole.COUNSELLOR,
    UserRole.ADMISSIONS,
    UserRole.MANAGER,
    UserRole.STUDENT,
)

#: Staff who review documents and browse the per-student folders.
_REVIEW_ROLES = require_role(UserRole.ADMIN, UserRole.COUNSELLOR, UserRole.ADMISSIONS, UserRole.MANAGER)


def _assert_visible_to(user: User, document: Document) -> None:
    if user.role is UserRole.STUDENT and document.student_id != user.id:
        raise ForbiddenException("Forbidden")


@router.get("", response_model=DocumentList, summary="List documents")
async def list_documents(
    page: int = 1,
    limit: int = 20,
    student_id: UUID | None = None,
    uploaded_by: UUID | None = None,
    status: str | None = None,
    document_type: str | None = None,
    search: str | None = None,
    service: DocumentService = Depends(get_document_service),
    user: User = Depends(_VIEW_ROLES),
) -> DocumentList:
    if user.role is UserRole.STUDENT:
        student_id = user.id

    documents, total = await service.list_documents(
        page,
        limit,
        student_id=student_id,
        uploaded_by=uploaded_by,
        status=status,
        document_type=document_type,
        search=search,
    )
    return DocumentList(
        items=[DocumentRead.model_validate(d) for d in documents],
        total=total,
        page=page,
        limit=limit,
    )


# Registered before `/{document_id}` so these literal paths always win.
@router.get("/folders", response_model=DocumentFolderList, summary="List student document folders")
async def list_document_folders(
    page: int = 1,
    limit: int = 24,
    search: str | None = None,
    sort: str = "recent",
    service: DocumentService = Depends(get_document_service),
    user: User = Depends(_REVIEW_ROLES),
) -> DocumentFolderList:
    folders, total = await service.list_student_folders(page, limit, search=search, sort=sort)
    return DocumentFolderList(
        items=[DocumentFolderRead(**dict(folder)) for folder in folders],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/folders/{student_id}", response_model=DocumentFolderRead, summary="Get a student's folder summary")
async def get_document_folder(
    student_id: UUID,
    service: DocumentService = Depends(get_document_service),
    user: User = Depends(_REVIEW_ROLES),
) -> DocumentFolderRead:
    folder = await service.get_student_folder(student_id)
    if folder is None:
        raise NotFoundException("No documents found for this student")
    return DocumentFolderRead(**dict(folder))


@router.post("/upload", response_model=DocumentRead, summary="Upload document file")
async def upload_document(
    student_id: UUID = Form(...),
    document_type: DocumentType = Form(...),
    file: UploadFile = File(...),
    title: str | None = Form(None),
    remarks: str | None = Form(None),
    service: DocumentService = Depends(get_document_service),
    notification_service: NotificationService = Depends(get_notification_service),
    user: User = Depends(_VIEW_ROLES),
) -> DocumentRead:
    # Overwritten rather than defaulted, so a student cannot file paperwork
    # against another student's record. ED360 drops `uploaded_by` from the form
    # too — it let any caller attribute an upload to someone else.
    if user.role is UserRole.STUDENT:
        student_id = user.id

    stored_file_name, content = await store_upload(file, DOCUMENT_EXTENSIONS)

    # `file_url` now points at the authenticated download route, which needs the
    # row's own id — so the id is generated here rather than by the column
    # default, keeping this to a single insert.
    document_id = uuid4()
    document = await service.create_document(
        {
            "id": document_id,
            "student_id": student_id,
            "uploaded_by": user.id,
            "document_type": document_type,
            "title": title or file.filename,
            "original_file_name": file.filename or stored_file_name,
            "stored_file_name": stored_file_name,
            "file_url": f"/api/v1/documents/{document_id}/download",
            "mime_type": file.content_type,
            "file_size": len(content),
            "remarks": remarks,
        }
    )

    return DocumentRead.model_validate(document)


@router.get("/{document_id}/download", summary="Download a document's file")
async def download_document(
    document_id: UUID,
    service: DocumentService = Depends(get_document_service),
    user: User = Depends(_VIEW_ROLES),
) -> FileResponse:
    """Authenticated, ownership-checked file access.

    ED360 serves uploads straight off a public `/uploads` static mount, so any
    passport scan or bank statement is retrievable by URL with no token at all —
    and those URLs travel in referrers, logs and shared links. Here the bytes
    are reachable only through this handler, which applies the same visibility
    rule as the metadata.
    """
    document = await service.get_document(document_id)
    if document is None:
        raise NotFoundException("Document not found")
    _assert_visible_to(user, document)

    return FileResponse(
        resolve_stored_path(document.stored_file_name),
        media_type=document.mime_type or "application/octet-stream",
        filename=document.original_file_name,
    )


@router.get("/{document_id}", response_model=DocumentRead, summary="Get document")
async def get_document(
    document_id: UUID,
    service: DocumentService = Depends(get_document_service),
    user: User = Depends(_VIEW_ROLES),
) -> DocumentRead:
    document = await service.get_document(document_id)
    if document is None:
        raise NotFoundException("Document not found")
    _assert_visible_to(user, document)
    return DocumentRead.model_validate(document)


@router.post("", response_model=DocumentRead, summary="Create document record")
async def create_document(
    payload: DocumentCreate,
    service: DocumentService = Depends(get_document_service),
    notification_service: NotificationService = Depends(get_notification_service),
    user: User = Depends(_REVIEW_ROLES),
) -> DocumentRead:
    """Staff-only — see `DocumentCreate` for why this is not open to students."""
    document = await service.create_document(payload.model_dump())
    return DocumentRead.model_validate(document)


@router.patch("/{document_id}", response_model=DocumentRead, summary="Update document")
async def update_document(
    document_id: UUID,
    payload: DocumentUpdate,
    service: DocumentService = Depends(get_document_service),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> DocumentRead:
    document = await service.get_document(document_id)
    if document is None:
        raise NotFoundException("Document not found")
    updated = await service.update_document(document, payload.model_dump(exclude_unset=True))
    return DocumentRead.model_validate(updated)


@router.post("/{document_id}/verify", response_model=DocumentRead, summary="Verify (approve) a document")
async def verify_document(
    document_id: UUID,
    payload: DocumentVerifyRequest,
    service: DocumentService = Depends(get_document_service),
    notification_service: NotificationService = Depends(get_notification_service),
    user: User = Depends(_REVIEW_ROLES),
) -> DocumentRead:
    document = await service.get_document(document_id)
    if document is None:
        raise NotFoundException("Document not found")

    # The student notification is raised by the DocumentApproved subscriber, not
    # here — see app/core/subscribers.py. Keeping it inline meant any other
    # caller of verify_document silently skipped it.
    verified = await service.verify_document(document, verified_by=user.id, remarks=payload.remarks)
    return DocumentRead.model_validate(verified)


@router.post("/{document_id}/reject", response_model=DocumentRead, summary="Reject a document")
async def reject_document(
    document_id: UUID,
    payload: DocumentRejectRequest,
    service: DocumentService = Depends(get_document_service),
    notification_service: NotificationService = Depends(get_notification_service),
    user: User = Depends(_REVIEW_ROLES),
) -> DocumentRead:
    document = await service.get_document(document_id)
    if document is None:
        raise NotFoundException("Document not found")

    rejected = await service.reject_document(
        document,
        verified_by=user.id,
        reason=payload.reason,
        remarks=payload.remarks,
    )
    # Notification raised by the DocumentRejected subscriber.
    return DocumentRead.model_validate(rejected)


@router.post("/{document_id}/comment", response_model=DocumentRead, summary="Add a comment to a document")
async def comment_document(
    document_id: UUID,
    payload: DocumentCommentRequest,
    service: DocumentService = Depends(get_document_service),
    notification_service: NotificationService = Depends(get_notification_service),
    user: User = Depends(_REVIEW_ROLES),
) -> DocumentRead:
    document = await service.get_document(document_id)
    if document is None:
        raise NotFoundException("Document not found")

    commented = await service.comment_document(document, payload.remarks)
    await notification_service.notify_many(
        [commented.student_id],
        notification_type=NotificationType.DOCUMENT,
        title="New comment on your document",
        message=payload.remarks,
    )
    return DocumentRead.model_validate(commented)


@router.post(
    "/{document_id}/extract",
    response_model=DocumentExtractionResult,
    summary="Extract structured data from a document",
)
async def extract_document_data(
    document_id: UUID,
    service: DocumentService = Depends(get_document_service),
    user: User = Depends(_VIEW_ROLES),
) -> DocumentExtractionResult:
    document = await service.get_document(document_id)
    if document is None:
        raise NotFoundException("Document not found")
    _assert_visible_to(user, document)
    return DocumentExtractionResult(**await service.extract_profile_data(document))


@router.delete("/{document_id}", response_model=DocumentRead, summary="Delete document")
async def delete_document(
    document_id: UUID,
    service: DocumentService = Depends(get_document_service),
    user: User = Depends(require_role(UserRole.ADMIN)),
) -> DocumentRead:
    document = await service.get_document(document_id)
    if document is None:
        raise NotFoundException("Document not found")
    return DocumentRead.model_validate(await service.delete_document(document))
