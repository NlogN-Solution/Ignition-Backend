from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import DegreeLevel

if TYPE_CHECKING:
    from .user import StudentProfile


class StudentEducationHistory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "student_education_history"

    student_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    institution_name: Mapped[str] = mapped_column(String(255), nullable=False)
    degree_level: Mapped[DegreeLevel | None] = mapped_column(
        enum_type(DegreeLevel, "degree_level", create_type=False),
    )
    field_of_study: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    grade: Mapped[str | None] = mapped_column(String(50))
    is_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    student_profile: Mapped[StudentProfile] = relationship(back_populates="education_history")

    __table_args__ = (Index("idx_student_education_history_student_profile_id", "student_profile_id"),)

    def __repr__(self) -> str:
        return f"<StudentEducationHistory id={self.id} institution_name={self.institution_name}>"


class StudentWorkExperience(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "student_work_experience"

    student_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    description: Mapped[str | None] = mapped_column(Text)

    student_profile: Mapped[StudentProfile] = relationship(back_populates="work_experience")

    __table_args__ = (Index("idx_student_work_experience_student_profile_id", "student_profile_id"),)

    def __repr__(self) -> str:
        return f"<StudentWorkExperience id={self.id} company_name={self.company_name}>"
