from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import ColumnElement, String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..core.events import DocumentApproved, DocumentRejected, DocumentUploaded, event_bus
from ..models import Document, User
from ..models.enums import DocumentStatus
from .partial_update import reject_null_on_required
from .staff_resolution import resolve_responsible_staff_ids


class DocumentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_document(self, document_id: UUID) -> Document | None:
        return await self.session.scalar(select(Document).where(Document.id == document_id))

    async def list_documents(
        self,
        page: int,
        limit: int,
        student_id: UUID | None = None,
        uploaded_by: UUID | None = None,
        status: str | None = None,
        document_type: str | None = None,
        search: str | None = None,
    ) -> tuple[list[Document], int]:
        conditions: list[ColumnElement[bool]] = []
        if student_id:
            conditions.append(Document.student_id == student_id)
        if uploaded_by:
            conditions.append(Document.uploaded_by == uploaded_by)
        if status:
            conditions.append(Document.status == status)
        if document_type:
            conditions.append(Document.document_type == document_type)
        if search and search.strip():
            search_value = f"%{search.strip().lower()}%"
            conditions.append(
                or_(
                    func.lower(Document.title).like(search_value),
                    func.lower(Document.original_file_name).like(search_value),
                )
            )

        query = select(Document)
        count_query = select(func.count()).select_from(Document)
        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        query = query.order_by(Document.created_at.desc(), Document.id).limit(limit).offset((page - 1) * limit)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def create_document(self, data: dict[str, Any]) -> Document:
        document = Document(**data)
        self.session.add(document)
        await self.session.commit()
        await self.session.refresh(document)
        await event_bus.publish(
            DocumentUploaded(
                document_id=document.id,
                student_id=document.student_id,
                uploaded_by=document.uploaded_by,
                title=document.title or document.original_file_name,
            ),
            self.session,
        )
        return document

    async def update_document(self, document: Document, data: dict[str, Any]) -> Document:
        reject_null_on_required(Document, data)
        for key, value in data.items():
            setattr(document, key, value)
        await self.session.commit()
        await self.session.refresh(document)
        return document

    async def delete_document(self, document: Document) -> Document:
        await self.session.delete(document)
        await self.session.commit()
        return document

    async def verify_document(self, document: Document, verified_by: UUID, remarks: str | None = None) -> Document:
        document.status = DocumentStatus.APPROVED
        document.verified_by = verified_by
        document.verified_at = datetime.now(UTC)
        document.rejection_reason = None
        if remarks is not None:
            document.remarks = remarks
        await self.session.commit()
        await self.session.refresh(document)
        await event_bus.publish(
            DocumentApproved(
                document_id=document.id,
                student_id=document.student_id,
                verified_by=verified_by,
                title=document.title or document.original_file_name,
            ),
            self.session,
        )
        return document

    async def reject_document(
        self,
        document: Document,
        verified_by: UUID,
        reason: str,
        remarks: str | None = None,
    ) -> Document:
        document.status = DocumentStatus.REJECTED
        document.verified_by = verified_by
        document.verified_at = datetime.now(UTC)
        document.rejection_reason = reason
        if remarks is not None:
            document.remarks = remarks
        await self.session.commit()
        await self.session.refresh(document)
        await event_bus.publish(
            DocumentRejected(
                document_id=document.id,
                student_id=document.student_id,
                verified_by=verified_by,
                title=document.title or document.original_file_name,
                reason=reason,
            ),
            self.session,
        )
        return document

    async def comment_document(self, document: Document, remarks: str) -> Document:
        document.remarks = remarks
        await self.session.commit()
        await self.session.refresh(document)
        return document

    async def extract_profile_data(self, document: Document) -> dict[str, Any]:
        """Pluggable data-extraction hook.

        No OCR/vision provider is configured. This is the single place to wire
        one in later without touching any caller; it returns an honest "not
        configured" result rather than fabricating fields.
        """
        return {
            "configured": False,
            "document_id": document.id,
            "document_type": document.document_type,
            "message": "Automatic data extraction isn't connected to a provider yet — fill this in manually for now.",
            "fields": {},
        }

    async def get_responsible_staff_ids(self, student_id: UUID) -> list[UUID]:
        return await resolve_responsible_staff_ids(self.session, student_id)

    # --- Folders ---------------------------------------------------------------

    @staticmethod
    def _folder_columns() -> Any:
        return (
            func.count(Document.id).label("document_count"),
            func.count(Document.id).filter(Document.status == DocumentStatus.APPROVED).label("approved_count"),
            func.count(Document.id).filter(Document.status == DocumentStatus.PENDING).label("pending_count"),
            func.count(Document.id).filter(Document.status == DocumentStatus.REJECTED).label("rejected_count"),
            func.max(Document.updated_at).label("last_updated"),
        )

    def _folder_query(self) -> Any:
        document_count, approved_count, pending_count, rejected_count, last_updated = self._folder_columns()
        query = (
            select(
                User.id.label("student_id"),
                User.first_name,
                User.last_name,
                User.email,
                User.phone,
                User.avatar_url,
                document_count,
                approved_count,
                pending_count,
                rejected_count,
                func.coalesce(func.sum(Document.file_size), 0).label("total_size"),
                last_updated,
            )
            .select_from(Document)
            .join(User, User.id == Document.student_id)
            .group_by(User.id)
        )
        return query, document_count, pending_count, last_updated

    async def list_student_folders(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        sort: str = "recent",
    ) -> tuple[list[Any], int]:
        """One row per student holding at least one document.

        Computed live with GROUP BY rather than persisted, so a folder appears
        with a student's first document and disappears with their last.
        """
        base, document_count, pending_count, last_updated = self._folder_query()

        if search and search.strip():
            search_value = f"%{search.strip().lower()}%"
            base = base.where(
                or_(
                    func.lower(User.first_name).like(search_value),
                    func.lower(User.last_name).like(search_value),
                    func.lower(User.email).like(search_value),
                    func.lower(User.phone).like(search_value),
                    func.lower(cast(User.id, String)).like(search_value),
                )
            )

        total = await self.session.scalar(select(func.count()).select_from(base.subquery())) or 0

        orderings: dict[str, Any] = {
            "recent": (last_updated.desc(),),
            "name": (User.first_name.asc(), User.last_name.asc()),
            "count": (document_count.desc(),),
            "pending": (pending_count.desc(),),
        }
        query = base.order_by(*orderings.get(sort, orderings["recent"])).limit(limit).offset((page - 1) * limit)
        result = await self.session.execute(query)
        return list(result.mappings().all()), total

    async def get_student_folder(self, student_id: UUID) -> Any | None:
        base, _, _, _ = self._folder_query()
        result = await self.session.execute(base.where(Document.student_id == student_id))
        return result.mappings().one_or_none()


async def get_document_service(session: AsyncSession = Depends(get_db_session)) -> DocumentService:
    return DocumentService(session)
