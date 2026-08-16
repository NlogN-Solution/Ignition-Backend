from __future__ import annotations

from typing import Any, ClassVar, Generic, TypeVar
from uuid import UUID

from fastapi import Depends
from sqlalchemy import ColumnElement, func, inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from ..api.deps import get_db_session
from ..api.exceptions import BadRequestException
from ..db.base import Base
from ..models import Country, Intake, Program, University

ModelT = TypeVar("ModelT", bound=Base)


class CatalogService(Generic[ModelT]):
    """Shared CRUD for the four catalog tables.

    ED360 writes `CountryService`, `UniversityService`, `ProgramService` and
    `IntakeService` as four copies of the same 60 lines, differing only in the
    model and the `list` filters. Only the filters differ meaningfully, so they
    are the only thing the subclasses below define.

    The catalog is global — no tenancy to strip here (Bucket B), which is why
    this is the one place the port is a pure copy.
    """

    model: ClassVar[type[Any]]

    #: Name of the column to sort listings by, as a string rather than the
    #: column itself. A mapped column is a descriptor, so a class attribute
    #: holding one would be resolved by `self.order_by` as an *instance*
    #: attribute lookup against the service — which fails with
    #: "Class 'CountryService' is not mapped".
    order_by_field: ClassVar[str]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: UUID) -> ModelT | None:
        result: ModelT | None = await self.session.scalar(select(self.model).where(self.model.id == entity_id))
        return result

    async def paginate(
        self,
        page: int,
        limit: int,
        conditions: list[ColumnElement[bool]],
    ) -> tuple[list[ModelT], int]:
        query = select(self.model)
        count_query = select(func.count()).select_from(self.model)
        for condition in conditions:
            query = query.where(condition)
            count_query = count_query.where(condition)

        total = await self.session.scalar(count_query) or 0
        query = query.order_by(getattr(self.model, self.order_by_field)).offset((page - 1) * limit).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all()), total

    async def create(self, data: dict[str, Any]) -> ModelT:
        entity: ModelT = self.model(**data)
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def update(self, entity: ModelT, data: dict[str, Any]) -> ModelT:
        """Apply a partial update.

        Callers pass `exclude_unset=True`, so a key being present means the
        client sent it. ED360 then skips any `None`, which makes clearing an
        optional field impossible — `{"website": null}` silently does nothing.
        Here an explicit null clears a nullable column and is rejected outright
        on a non-nullable one, rather than reaching the database as a 500.
        """
        columns = inspect(self.model).columns
        for key, value in data.items():
            if value is None and key in columns and not columns[key].nullable:
                raise BadRequestException(f"'{key}' cannot be null")
            setattr(entity, key, value)

        await self.session.commit()
        await self.session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> ModelT:
        await self.session.delete(entity)
        await self.session.commit()
        return entity

    @staticmethod
    def _search(term: str, *fields: InstrumentedAttribute[Any]) -> ColumnElement[bool]:
        value = f"%{term.strip().lower()}%"
        return or_(*(func.lower(field).like(value) for field in fields))


class CountryService(CatalogService[Country]):
    model = Country
    order_by_field = "name"

    async def list(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[Country], int]:
        conditions: list[ColumnElement[bool]] = []
        if search and search.strip():
            conditions.append(self._search(search, Country.name, Country.iso2, Country.iso3))
        if is_active is not None:
            conditions.append(Country.is_active == is_active)
        return await self.paginate(page, limit, conditions)


class UniversityService(CatalogService[University]):
    model = University
    order_by_field = "name"

    async def list(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        country_id: UUID | None = None,
        is_active: bool | None = None,
        is_partner: bool | None = None,
    ) -> tuple[list[University], int]:
        conditions: list[ColumnElement[bool]] = []
        if search and search.strip():
            conditions.append(self._search(search, University.name, University.short_name, University.city))
        if country_id:
            conditions.append(University.country_id == country_id)
        if is_active is not None:
            conditions.append(University.is_active == is_active)
        if is_partner is not None:
            conditions.append(University.is_partner == is_partner)
        return await self.paginate(page, limit, conditions)


class ProgramService(CatalogService[Program]):
    model = Program
    order_by_field = "name"

    async def list(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        university_id: UUID | None = None,
        degree_level: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[Program], int]:
        conditions: list[ColumnElement[bool]] = []
        if search and search.strip():
            conditions.append(self._search(search, Program.name, Program.field_of_study))
        if university_id:
            conditions.append(Program.university_id == university_id)
        if degree_level:
            conditions.append(Program.degree_level == degree_level)
        if is_active is not None:
            conditions.append(Program.is_active == is_active)
        return await self.paginate(page, limit, conditions)


class IntakeService(CatalogService[Intake]):
    model = Intake
    order_by_field = "name"

    async def list(
        self,
        page: int,
        limit: int,
        program_id: UUID | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[Intake], int]:
        conditions: list[ColumnElement[bool]] = []
        if program_id:
            conditions.append(Intake.program_id == program_id)
        if is_active is not None:
            conditions.append(Intake.is_active == is_active)
        return await self.paginate(page, limit, conditions)


async def get_country_service(session: AsyncSession = Depends(get_db_session)) -> CountryService:
    return CountryService(session)


async def get_university_service(session: AsyncSession = Depends(get_db_session)) -> UniversityService:
    return UniversityService(session)


async def get_program_service(session: AsyncSession = Depends(get_db_session)) -> ProgramService:
    return ProgramService(session)


async def get_intake_service(session: AsyncSession = Depends(get_db_session)) -> IntakeService:
    return IntakeService(session)
