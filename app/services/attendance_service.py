from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..models import AttendancePolicy, AttendanceRecord, EmployeeProfile, User
from ..models.enums import AttendanceSource, AttendanceStatus

DEFAULT_WORK_DAYS = [0, 1, 2, 3, 4]  # Monday..Friday


def work_day_dates(start_date: date, end_date: date, work_days: set[int]) -> list[date]:
    """Pure date-math shared by Leave (day-count a request) and Payroll (day-count
    an approved leave's overlap with a pay period) — kept here rather than on
    either service so neither has to instantiate the other just for this."""
    dates: list[date] = []
    current = start_date
    while current <= end_date:
        if current.weekday() in work_days:
            dates.append(current)
        current += timedelta(days=1)
    return dates


class AttendanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Policy -----------------------------------------------------------

    async def get_policy(self) -> AttendancePolicy | None:
        result = await self.session.execute(select(AttendancePolicy).limit(1))
        return result.scalar_one_or_none()

    async def upsert_policy(self, data: dict[str, Any]) -> AttendancePolicy:
        policy = await self.get_policy()
        if policy is None:
            # `is_singleton` is unique and CHECK-constrained, so the database
            # enforces the one-policy rule ED360 got from a per-org unique index.
            policy = AttendancePolicy(is_singleton=True, **data)
            self.session.add(policy)
        else:
            for key, value in data.items():
                if value is not None:
                    setattr(policy, key, value)
        await self.session.commit()
        await self.session.refresh(policy)
        return policy

    @staticmethod
    def _resolve_timezone() -> ZoneInfo | dt_timezone:
        """Local wall-clock zone for attendance.

        ED360 reads `Organization.timezone` per row; single-tenant this is
        `settings.TIMEZONE`. Falls back to UTC on an unknown IANA name rather
        than failing check-in over a typo in configuration.
        """
        try:
            return ZoneInfo(get_settings().TIMEZONE)
        except ZoneInfoNotFoundError:
            return UTC

    # --- Check-in / check-out ----------------------------------------------

    async def get_today(self, user_id: UUID) -> AttendanceRecord | None:
        tz = self._resolve_timezone()
        today = datetime.now(tz).date()
        result = await self.session.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.user_id == user_id,
                AttendanceRecord.date == today,
            )
        )
        return result.scalar_one_or_none()

    async def check_in(self, user_id: UUID) -> AttendanceRecord:
        """Caller must already have verified no record exists for today (via
        get_today) — this still relies on the DB's unique (org, user, date)
        constraint as the final word if a race slips through, which the route
        surfaces as a 409 on IntegrityError."""
        tz = self._resolve_timezone()
        now_local = datetime.now(tz)
        today = now_local.date()

        policy = await self.get_policy()
        status = AttendanceStatus.PRESENT
        if policy is not None:
            expected_start = datetime.combine(today, policy.expected_start_time, tzinfo=tz)
            grace_deadline = expected_start + timedelta(minutes=policy.grace_period_minutes)
            if now_local > grace_deadline:
                status = AttendanceStatus.LATE

        record = AttendanceRecord(
            user_id=user_id,
            date=today,
            check_in_at=now_local,
            status=status,
            source=AttendanceSource.WEB,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def check_out(self, record: AttendanceRecord) -> AttendanceRecord:
        now = datetime.now(UTC)
        record.check_out_at = now
        check_in_at = record.check_in_at
        if check_in_at is not None:
            if check_in_at.tzinfo is None:
                check_in_at = check_in_at.replace(tzinfo=UTC)
            worked = int((now - check_in_at).total_seconds())
            record.worked_seconds = max(0, worked)

            policy = await self.get_policy()
            if policy is not None:
                expected_seconds = (
                    datetime.combine(date.min, policy.expected_end_time)
                    - datetime.combine(date.min, policy.expected_start_time)
                ).total_seconds()
                record.overtime_seconds = max(0, int(worked - expected_seconds))

        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def mark_leave_days(self, user_id: UUID, dates: list[date], recorded_by: UUID) -> None:
        """Called when a leave request is approved — upserts an on_leave
        AttendanceRecord for each covered work day, but never overwrites a
        day that already has a real check-in."""
        if not dates:
            return
        result = await self.session.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.user_id == user_id,
                AttendanceRecord.date.in_(dates),
            )
        )
        existing_by_date = {record.date: record for record in result.scalars().all()}

        for target_date in dates:
            existing = existing_by_date.get(target_date)
            if existing is not None:
                if existing.check_in_at is not None:
                    continue
                existing.status = AttendanceStatus.ON_LEAVE
                existing.source = AttendanceSource.MANUAL
                existing.recorded_by = recorded_by
            else:
                self.session.add(
                    AttendanceRecord(
                        user_id=user_id,
                        date=target_date,
                        status=AttendanceStatus.ON_LEAVE,
                        source=AttendanceSource.MANUAL,
                        recorded_by=recorded_by,
                    )
                )
        await self.session.commit()

    # --- Records ------------------------------------------------------------

    async def get_record(self, record_id: UUID) -> AttendanceRecord | None:
        query = select(AttendanceRecord).where(AttendanceRecord.id == record_id)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_records(
        self,
        page: int,
        limit: int,
        user_id: UUID | None = None,
        department_id: UUID | None = None,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[list[AttendanceRecord], int]:
        query = select(AttendanceRecord)
        count_query = select(func.count()).select_from(AttendanceRecord)

        if department_id is not None:
            query = query.join(EmployeeProfile, EmployeeProfile.user_id == AttendanceRecord.user_id)
            count_query = count_query.join(EmployeeProfile, EmployeeProfile.user_id == AttendanceRecord.user_id)

        conditions = []
        if user_id is not None:
            conditions.append(AttendanceRecord.user_id == user_id)
        if status:
            conditions.append(AttendanceRecord.status == status)
        if date_from is not None:
            conditions.append(AttendanceRecord.date >= date_from)
        if date_to is not None:
            conditions.append(AttendanceRecord.date <= date_to)
        if department_id is not None:
            conditions.append(EmployeeProfile.department_id == department_id)

        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        query = (
            query.order_by(AttendanceRecord.date.desc(), AttendanceRecord.check_in_at.desc())
            .limit(limit)
            .offset((page - 1) * limit)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def update_record(
        self, record: AttendanceRecord, data: dict[str, Any], recorded_by: UUID
    ) -> AttendanceRecord:
        for key, value in data.items():
            if value is not None:
                setattr(record, key, value)
        record.source = AttendanceSource.MANUAL
        record.recorded_by = recorded_by
        await self.session.commit()
        await self.session.refresh(record)
        return record

    # --- Summaries ------------------------------------------------------------

    async def dashboard_summary(self, target_date: date) -> dict[str, Any]:
        policy = await self.get_policy()
        work_days = set(policy.work_days) if policy is not None else set(DEFAULT_WORK_DAYS)
        is_work_day = target_date.weekday() in work_days

        status_query = (
            select(AttendanceRecord.status, func.count())
            .where(AttendanceRecord.date == target_date)
            .group_by(AttendanceRecord.status)
        )
        counts: dict[AttendanceStatus, int] = dict((await self.session.execute(status_query)).all())  # type: ignore[arg-type]

        currently_working = (
            await self.session.scalar(
                select(func.count())
                .select_from(AttendanceRecord)
                .where(
                    AttendanceRecord.date == target_date,
                    AttendanceRecord.check_in_at.isnot(None),
                    AttendanceRecord.check_out_at.is_(None),
                )
            )
            or 0
        )

        absent = 0
        if is_work_day:
            total_staff = (
                await self.session.scalar(
                    select(func.count()).select_from(User).where(User.role != "student", User.deleted_at.is_(None))
                )
                or 0
            )
            absent = max(0, total_staff - sum(counts.values()))

        return {
            "date": target_date,
            "is_work_day": is_work_day,
            "present": counts.get(AttendanceStatus.PRESENT, 0) + counts.get(AttendanceStatus.LATE, 0),
            "late": counts.get(AttendanceStatus.LATE, 0),
            "currently_working": currently_working,
            "absent": absent,
        }

    async def employee_summary(self, user_id: UUID, year: int, month: int) -> dict[str, Any]:
        month_start = date(year, month, 1)
        month_end = date(year, month, calendar.monthrange(year, month)[1])

        status_query = (
            select(AttendanceRecord.status, func.count())
            .where(
                AttendanceRecord.user_id == user_id,
                AttendanceRecord.date >= month_start,
                AttendanceRecord.date <= month_end,
            )
            .group_by(AttendanceRecord.status)
        )
        counts: dict[AttendanceStatus, int] = dict((await self.session.execute(status_query)).all())  # type: ignore[arg-type]

        total_seconds = (
            await self.session.scalar(
                select(func.coalesce(func.sum(AttendanceRecord.worked_seconds), 0)).where(
                    AttendanceRecord.user_id == user_id,
                    AttendanceRecord.date >= month_start,
                    AttendanceRecord.date <= month_end,
                )
            )
            or 0
        )

        policy = await self.get_policy()
        work_days = set(policy.work_days) if policy is not None else set(DEFAULT_WORK_DAYS)
        range_end = min(month_end, date.today())
        expected_work_days = 0
        if range_end >= month_start:
            day = month_start
            while day <= range_end:
                if day.weekday() in work_days:
                    expected_work_days += 1
                day += timedelta(days=1)

        recorded_days = sum(counts.values())
        return {
            "present_days": counts.get(AttendanceStatus.PRESENT, 0) + counts.get(AttendanceStatus.LATE, 0),
            "late_days": counts.get(AttendanceStatus.LATE, 0),
            "absent_days": max(0, expected_work_days - recorded_days),
            "total_worked_seconds": int(total_seconds),
        }
