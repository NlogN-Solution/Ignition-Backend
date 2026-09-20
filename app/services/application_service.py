from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import ColumnElement, String, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..core.events import ApplicationCreated, ApplicationStatusChanged, event_bus
from ..models import Application, ApplicationStatusHistory, Program, University, User
from ..models.enums import ApplicationStatus
from .partial_update import reject_null_on_required

#: Date fields stored as `String(10)` ISO strings — an ED360 wart documented on
#: the model — while the API schemas type them as `date`. asyncpg will not coerce
#: a `date` into a VARCHAR parameter ("expected str, got date"), so every write
#: path converts them here. `cas_received_date` and the deadline columns are
#: real `Date` columns and are left alone.
STRING_DATE_FIELDS = frozenset(
    {"application_date", "submission_date", "offer_received_date", "visa_applied_date", "visa_decision_date", "enrollment_date"}
)


def normalise_string_dates(data: dict[str, Any]) -> dict[str, Any]:
    """Render the `String(10)` date fields as ISO strings; everything else untouched."""
    return {
        key: value.isoformat() if key in STRING_DATE_FIELDS and isinstance(value, date) else value
        for key, value in data.items()
    }


#: `IGN-2026-461935` as the console renders it, or just the six-digit serial.
#: The serial is `int(id.hex[:6], 16) % 1_000_000` — see
#: `admin-frontend/src/modules/applications/reference.ts`, which is where it is
#: displayed and therefore what staff will type back in.
_REFERENCE = re.compile(r"^(?:IGN-\d{4}-)?(\d{1,6})$", re.IGNORECASE)


def _reference_serial(search: str) -> int | None:
    """The numeric serial in `search`, if it reads like an application reference."""
    match = _REFERENCE.match(search.strip())
    return int(match.group(1)) if match else None


def _uuid_prefix(search: str) -> str | None:
    """`search` as a leading chunk of a UUID, or None.

    Four hex characters is the shortest worth matching; below that almost any
    short word qualifies and the clause stops narrowing anything.
    """
    candidate = re.sub(r"[\s-]", "", search).lower()
    return candidate if re.fullmatch(r"[0-9a-f]{4,32}", candidate) else None


class ApplicationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_application(self, application_id: UUID, include_deleted: bool = False) -> Application | None:
        query = select(Application).where(Application.id == application_id)
        if not include_deleted:
            query = query.where(Application.deleted_at.is_(None))
        return await self.session.scalar(query)

    @staticmethod
    def _search_clause(search: str) -> ColumnElement[bool]:
        """One box, four things staff actually know a file by.

        The applicant's name, the course, the university, and the reference —
        which is the thing quoted on the phone and so the one most likely to be
        typed. The reference is derived rather than stored (there is no serial
        column), so matching it means recomputing it in SQL: the first six hex
        digits of the id, as an integer, modulo a million. That is not
        indexable, so it is only attempted when the input actually looks like a
        reference and never on an ordinary name search.
        """
        needle = f"%{search.lower()}%"
        clauses: list[ColumnElement[bool]] = [
            func.lower(User.first_name).like(needle),
            func.lower(func.coalesce(User.last_name, "")).like(needle),
            func.lower(func.coalesce(User.email, "")).like(needle),
            func.lower(func.coalesce(Program.name, "")).like(needle),
            func.lower(func.coalesce(University.name, "")).like(needle),
            func.lower(func.coalesce(Application.university_application_id, "")).like(needle),
        ]

        prefix = _uuid_prefix(search)
        if prefix:
            clauses.append(cast(Application.id, String).like(f"{prefix}%"))

        serial = _reference_serial(search)
        if serial is not None:
            # Postgres only, which is what this runs on (tests/conftest.py):
            # take the id's first six hex digits, read them as a 24-bit
            # integer, and reduce modulo a million — the same arithmetic the
            # console does in TypeScript to print the reference.
            clauses.append(
                text(
                    "(('x' || substring(replace(applications.id::text, '-', ''), 1, 6))"
                    "::bit(24)::int % 1000000) = :reference_serial"
                ).bindparams(reference_serial=serial)
            )

        return or_(*clauses)

    async def list_applications(
        self,
        page: int,
        limit: int,
        student_id: UUID | None = None,
        counsellor_id: UUID | None = None,
        program_id: UUID | None = None,
        status: str | None = None,
        search: str | None = None,
        visible_to: UUID | None = None,
    ) -> tuple[list[Application], int]:
        # ED360 builds the filters twice, once for the page and once for the
        # count, which is how the two drift apart.
        conditions: list[ColumnElement[bool]] = [Application.deleted_at.is_(None)]
        if student_id:
            conditions.append(Application.student_id == student_id)
        if counsellor_id:
            conditions.append(Application.counsellor_id == counsellor_id)
        if program_id:
            conditions.append(Application.program_id == program_id)
        if status:
            conditions.append(Application.status == status)
        # The caller's own scope (see `api/scoping.py`), not a filter they chose:
        # their applications plus the ones nobody has picked up.
        if visible_to is not None:
            conditions.append(
                or_(Application.counsellor_id == visible_to, Application.counsellor_id.is_(None))
            )

        if search and search.strip():
            conditions.append(self._search_clause(search.strip()))

        # An outer join, so an application whose program row is missing still
        # appears when searched by student — a join that silently drops rows is
        # worse than one that cannot match on course name.
        query = (
            select(Application)
            .outerjoin(User, User.id == Application.student_id)
            .outerjoin(Program, Program.id == Application.program_id)
            .outerjoin(University, University.id == Program.university_id)
        )
        count_query = (
            select(func.count())
            .select_from(Application)
            .outerjoin(User, User.id == Application.student_id)
            .outerjoin(Program, Program.id == Application.program_id)
            .outerjoin(University, University.id == Program.university_id)
        )
        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        # Unordered in ED360, which makes pagination non-deterministic.
        query = query.order_by(Application.created_at.desc(), Application.id).limit(limit).offset((page - 1) * limit)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def create_application(self, data: dict[str, Any]) -> Application:
        application = Application(**normalise_string_dates(data))
        self.session.add(application)
        await self.session.commit()
        await self.session.refresh(application)
        await event_bus.publish(
            ApplicationCreated(
                application_id=application.id,
                student_id=application.student_id,
                created_by=application.counsellor_id,
            ),
            self.session,
        )
        return application

    async def update_application(self, application: Application, data: dict[str, Any]) -> Application:
        """Field updates only.

        `status` is deliberately not settable here — see `ApplicationUpdate`.
        It moves through `change_application_status`, which is what writes the
        history row.
        """
        reject_null_on_required(Application, data)
        for key, value in normalise_string_dates(data).items():
            setattr(application, key, value)

        await self.session.commit()
        await self.session.refresh(application)
        return application

    async def change_application_status(
        self,
        application: Application,
        new_status: ApplicationStatus,
        performed_by: UUID | None = None,
        remarks: str | None = None,
    ) -> Application:
        old_status = application.status
        if new_status == old_status:
            return application

        application.status = new_status
        self.session.add(
            ApplicationStatusHistory(
                application_id=application.id,
                old_status=old_status,
                new_status=new_status,
                changed_by=performed_by,
                remarks=remarks,
            )
        )
        await self.session.commit()
        await self.session.refresh(application)

        # After commit: subscribers read the database and must see this change.
        await event_bus.publish(
            ApplicationStatusChanged(
                application_id=application.id,
                student_id=application.student_id,
                old_status=old_status.value if old_status else None,
                new_status=new_status.value,
                changed_by=performed_by,
                remarks=remarks,
            ),
            self.session,
        )
        return application

    async def list_status_history(self, application_id: UUID) -> list[ApplicationStatusHistory]:
        result = await self.session.execute(
            select(ApplicationStatusHistory)
            .where(ApplicationStatusHistory.application_id == application_id)
            .order_by(ApplicationStatusHistory.created_at)
        )
        return list(result.scalars().all())

    async def delete_application(self, application: Application) -> Application:
        """Soft delete: gone from the product, kept in the database.

        A hard `session.delete()` here cascaded to the application's status
        history, its documents and its payments — the paper trail behind an
        offer or a fee, which outlives any reason for removing the application
        from a list.
        """
        application.deleted_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(application)
        return application


async def get_application_service(session: AsyncSession = Depends(get_db_session)) -> ApplicationService:
    return ApplicationService(session)
