"""Progress and points.

Phase 6. Both advance from domain events, never from a client call: the student
portal shows progress and points but cannot set them, so `award` and
`complete_milestone` are reached from subscribers rather than from any route.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..models import PointsRule, ProgressMilestone, StudentMilestone, StudentPointsLedger


class ProgressService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def milestones_for(self, student_id: UUID) -> list[tuple[ProgressMilestone, StudentMilestone | None]]:
        """Every active milestone with this student's standing on it.

        Returns the full ladder, not just what they have reached — the portal
        shows what is still ahead, so absent rows are part of the answer.
        """
        milestones = (
            await self.session.scalars(
                select(ProgressMilestone).where(ProgressMilestone.is_active.is_(True)).order_by(ProgressMilestone.order)
            )
        ).all()
        achieved = (
            await self.session.scalars(
                select(StudentMilestone)
                .options(selectinload(StudentMilestone.milestone))
                .where(StudentMilestone.student_id == student_id)
            )
        ).all()
        by_milestone = {row.milestone_id: row for row in achieved}
        return [(milestone, by_milestone.get(milestone.id)) for milestone in milestones]

    async def completion_percentage(self, student_id: UUID) -> int:
        """Weighted, so "visa granted" counts for more than "profile filled in".

        Returns 0 rather than dividing by zero when no milestones are
        configured — a fresh database should not 500 the dashboard.
        """
        pairs = await self.milestones_for(student_id)
        total_weight = sum(milestone.weight for milestone, _ in pairs)
        if total_weight == 0:
            return 0
        earned = sum(milestone.weight for milestone, standing in pairs if standing and standing.is_complete)
        return round(earned / total_weight * 100)

    async def complete_milestone(self, student_id: UUID, key: str, source: str) -> StudentMilestone | None:
        """Mark a milestone reached. Idempotent — reaching it twice is one row.

        Returns None when no milestone with that key is configured, rather than
        raising: a subscriber firing for a milestone the deployment has not
        defined is not an error worth failing the request over.
        """
        milestone = await self.session.scalar(select(ProgressMilestone).where(ProgressMilestone.key == key))
        if milestone is None:
            return None

        existing = await self.session.scalar(
            select(StudentMilestone).where(
                StudentMilestone.student_id == student_id,
                StudentMilestone.milestone_id == milestone.id,
            )
        )
        if existing is not None:
            if existing.completed_at is None:
                existing.completed_at = datetime.now(UTC)
                existing.source = source
                await self.session.commit()
            return existing

        record = StudentMilestone(
            student_id=student_id,
            milestone_id=milestone.id,
            completed_at=datetime.now(UTC),
            source=source,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record


class PointsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def balance(self, student_id: UUID) -> int:
        """Summed from the ledger, never stored — a cached total can drift from
        the rows that justify it."""
        total = await self.session.scalar(
            select(func.coalesce(func.sum(StudentPointsLedger.points), 0)).where(
                StudentPointsLedger.student_id == student_id
            )
        )
        return int(total or 0)

    async def history(self, student_id: UUID, limit: int = 50) -> list[StudentPointsLedger]:
        result = await self.session.scalars(
            select(StudentPointsLedger)
            .where(StudentPointsLedger.student_id == student_id)
            .order_by(StudentPointsLedger.created_at.desc())
            .limit(limit)
        )
        return list(result)

    async def award(
        self,
        student_id: UUID,
        action: str,
        reference_id: UUID | None = None,
        description: str | None = None,
    ) -> StudentPointsLedger | None:
        """Award points for an action, at most once per source row.

        Returns None when the action has no active rule, or when this student
        has already been paid for this action on this row — re-approving a
        document must not pay twice. The uniqueness is enforced by a database
        constraint as well as checked here, so a concurrent double-fire loses
        the race rather than double-crediting.
        """
        rule = await self.session.scalar(
            select(PointsRule).where(PointsRule.action == action, PointsRule.is_active.is_(True))
        )
        if rule is None:
            return None

        duplicate_check = select(StudentPointsLedger).where(
            StudentPointsLedger.student_id == student_id,
            StudentPointsLedger.action == action,
        )
        if not rule.once_per_student:
            duplicate_check = duplicate_check.where(StudentPointsLedger.reference_id == reference_id)
        if await self.session.scalar(duplicate_check) is not None:
            return None

        entry = StudentPointsLedger(
            student_id=student_id,
            rule_id=rule.id,
            action=action,
            points=rule.points,
            description=description or rule.label,
            reference_id=reference_id,
        )
        self.session.add(entry)
        try:
            await self.session.commit()
        except IntegrityError:
            # Lost a race against a concurrent award for the same source row.
            await self.session.rollback()
            return None
        await self.session.refresh(entry)
        return entry


async def get_progress_service(session: AsyncSession = Depends(get_db_session)) -> ProgressService:
    return ProgressService(session)


async def get_points_service(session: AsyncSession = Depends(get_db_session)) -> PointsService:
    return PointsService(session)
