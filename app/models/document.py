from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import DocumentStatus, DocumentType, EnglishTestType

if TYPE_CHECKING:
    from .application import Application
    from .user import StudentProfile, User


class Document(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "documents"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    document_type: Mapped[DocumentType] = mapped_column(
        enum_type(DocumentType, "document_type", create_type=False),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(String(255))
    original_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[DocumentStatus] = mapped_column(
        enum_type(DocumentStatus, "document_status", create_type=False),
        nullable=False,
        server_default=DocumentStatus.PENDING.value,
    )
    expiry_date: Mapped[date | None] = mapped_column(String(10))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    remarks: Mapped[str | None] = mapped_column(Text)

    student: Mapped[User] = relationship(
        "User",
        foreign_keys=[student_id],
        back_populates="documents_owned",
    )
    uploader: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[uploaded_by],
        back_populates="documents_uploaded",
    )
    verifier: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[verified_by],
        back_populates="documents_verified",
    )
    application_documents: Mapped[list[ApplicationDocument]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        overlaps="applications",
    )
    applications: Mapped[list[Application]] = relationship(
        secondary="application_documents",
        back_populates="documents",
        cascade="save-update, merge",
        overlaps="application_documents",
    )

    __table_args__ = (
        CheckConstraint("file_size IS NULL OR file_size >= 0", name="documents_file_size_non_negative"),
        Index("idx_documents_student_id", "student_id"),
        Index("idx_documents_uploaded_by", "uploaded_by"),
        Index("idx_documents_verified_by", "verified_by"),
        Index("idx_documents_status", "status"),
        Index("idx_documents_document_type", "document_type"),
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} title={self.title}>"


class ApplicationDocument(Base, UUIDPKMixin):
    """Associates applications with the documents submitted for them."""

    __tablename__ = "application_documents"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    application: Mapped[Application] = relationship(
        back_populates="application_documents",
        overlaps="applications,documents",
    )
    document: Mapped[Document] = relationship(
        back_populates="application_documents",
        overlaps="applications,documents",
    )

    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "document_id",
            name="uq_application_documents_application_id_document_id",
        ),
        Index("idx_application_documents_application_id", "application_id"),
        Index("idx_application_documents_document_id", "document_id"),
    )

    def __repr__(self) -> str:
        return f"<ApplicationDocument id={self.id}>"


class StudentEnglishTest(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "student_english_tests"

    student_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_type: Mapped[EnglishTestType] = mapped_column(
        enum_type(EnglishTestType, "english_test_type", create_type=False),
        nullable=False,
    )
    test_date: Mapped[date | None] = mapped_column(String(10))
    overall_score: Mapped[float | None] = mapped_column(String(10))
    remarks: Mapped[str | None] = mapped_column(Text)

    student_profile: Mapped[StudentProfile] = relationship(back_populates="english_tests")

    __table_args__ = (
        UniqueConstraint(
            "student_profile_id",
            "test_type",
            "test_date",
            name="uq_student_english_tests_student_profile_id_test_type_test_date",
        ),
        Index("idx_student_english_tests_student_profile_id", "student_profile_id"),
        Index("idx_student_english_tests_test_type", "test_type"),
    )

    def __repr__(self) -> str:
        return f"<StudentEnglishTest id={self.id} test_type={self.test_type}>"
