"""Student portal saved items and course comparison.

Phase 4 (0003_student_portal_foundations). Three thin join tables rather than
JSONB arrays on `student_profiles`: these are queried from both directions
("what has this student saved", "how many students saved this course"), and the
uniqueness rule is a real constraint the database should hold rather than
something every caller re-checks.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import UUIDPKMixin

if TYPE_CHECKING:
    from .academic import Program, University
    from .user import User

#: The student portal's compare tray holds at most three courses. Enforced in
#: the service (see the Django backend's matching comment) rather than as a
#: CHECK, because a row-count limit is not expressible per-student in DDL
#: without a trigger.
MAX_COMPARE_COURSES = 3


class StudentSavedCourse(Base, UUIDPKMixin):
    __tablename__ = "student_saved_courses"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    student: Mapped[User] = relationship(back_populates="saved_courses")
    program: Mapped[Program] = relationship()

    __table_args__ = (
        UniqueConstraint("student_id", "program_id", name="uq_student_saved_courses_student_id_program_id"),
        Index("idx_student_saved_courses_student_id", "student_id"),
    )

    def __repr__(self) -> str:
        return f"<StudentSavedCourse student_id={self.student_id} program_id={self.program_id}>"


class StudentSavedUniversity(Base, UUIDPKMixin):
    __tablename__ = "student_saved_universities"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    university_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("universities.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    student: Mapped[User] = relationship(back_populates="saved_universities")
    university: Mapped[University] = relationship()

    __table_args__ = (
        UniqueConstraint("student_id", "university_id", name="uq_student_saved_universities_student_id_university_id"),
        Index("idx_student_saved_universities_student_id", "student_id"),
    )

    def __repr__(self) -> str:
        return f"<StudentSavedUniversity student_id={self.student_id} university_id={self.university_id}>"


class StudentCompareCourse(Base, UUIDPKMixin):
    __tablename__ = "student_compare_courses"

    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())

    student: Mapped[User] = relationship(back_populates="compare_courses")
    program: Mapped[Program] = relationship()

    __table_args__ = (
        UniqueConstraint("student_id", "program_id", name="uq_student_compare_courses_student_id_program_id"),
        Index("idx_student_compare_courses_student_id", "student_id"),
    )

    def __repr__(self) -> str:
        return f"<StudentCompareCourse student_id={self.student_id} program_id={self.program_id}>"
