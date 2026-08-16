from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from ..models.enums import DocumentStatus, DocumentType


class DocumentBase(BaseModel):
    student_id: UUID
    uploaded_by: UUID | None = None
    document_type: DocumentType
    title: str | None = None
    original_file_name: str
    stored_file_name: str
    file_url: str
    mime_type: str | None = None
    file_size: int | None = None
    status: DocumentStatus | None = DocumentStatus.PENDING
    expiry_date: date | None = None
    verified_by: UUID | None = None
    verified_at: datetime | None = None
    rejection_reason: str | None = None
    remarks: str | None = None


class DocumentCreate(DocumentBase):
    """Register a document row directly, for a file that already exists.

    Staff-only, because it takes `stored_file_name` and `file_url` from the
    client: a student able to post arbitrary values here could point a row they
    own at another student's stored file and read it through the authenticated
    download endpoint. Students upload via `POST /documents/upload`, where the
    server assigns both.
    """


class DocumentUpdate(BaseModel):
    """Editable metadata.

    `status`, `verified_by` and `verified_at` are absent by design — the same
    reasoning as `ApplicationUpdate`. They are written by the verify and reject
    endpoints, which stamp the acting user; leaving them PATCHable would let an
    approval be recorded against someone who never gave it.
    """

    uploaded_by: UUID | None = None
    document_type: DocumentType | None = None
    title: str | None = None
    original_file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    expiry_date: date | None = None
    remarks: str | None = None


class DocumentRead(DocumentBase):
    id: UUID
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentList(BaseModel):
    items: list[DocumentRead]
    total: int
    page: int
    limit: int


class DocumentVerifyRequest(BaseModel):
    remarks: str | None = None


class DocumentRejectRequest(BaseModel):
    reason: str
    remarks: str | None = None


class DocumentCommentRequest(BaseModel):
    remarks: str


class DocumentExtractionResult(BaseModel):
    configured: bool
    document_id: UUID
    document_type: DocumentType
    message: str
    fields: dict[str, str]


class DocumentFolderRead(BaseModel):
    """One row per student with at least one document — the folder the
    admin/counsellor UI browses.

    Never persisted; always computed live from `documents`, so a student's
    folder appears the moment their first document exists and disappears with
    their last.
    """

    student_id: UUID
    first_name: str
    last_name: str
    email: str
    phone: str | None
    avatar_url: str | None
    document_count: int
    approved_count: int
    pending_count: int
    rejected_count: int
    total_size: int
    last_updated: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentFolderList(BaseModel):
    items: list[DocumentFolderRead]
    total: int
    page: int
    limit: int
