"""Services for the public catalogue and CMS entities.

All of these subclass `CatalogService` (`academic_service.py`), which already
provides get / paginate / create / update / delete and the PATCH-null
semantics. Only the `list` filters differ, so they are the only thing defined
here — the same reasoning that collapsed ED360's four catalog services into one
base class.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..api.deps import get_db_session
from ..models import (
    BlogPost,
    ContentBlock,
    ContentPage,
    CountryGuide,
    CourseProfile,
    MediaAsset,
    Scholarship,
    UniversityRoute,
)
from .academic_service import CatalogService


class UniversityRouteService(CatalogService[UniversityRoute]):
    model = UniversityRoute
    order_by_field = "display_order"

    async def list(
        self,
        page: int,
        limit: int,
        university_id: UUID | None = None,
        route_key: str | None = None,
        is_published: bool | None = None,
    ) -> tuple[list[UniversityRoute], int]:
        conditions: list[ColumnElement[bool]] = []
        if university_id:
            conditions.append(UniversityRoute.university_id == university_id)
        if route_key:
            conditions.append(UniversityRoute.route_key == route_key)
        if is_published is not None:
            conditions.append(UniversityRoute.is_published == is_published)
        return await self.paginate(page, limit, conditions)


class CourseProfileService(CatalogService[CourseProfile]):
    model = CourseProfile
    order_by_field = "title"

    async def get_by_slug(self, slug: str) -> CourseProfile | None:
        return await self.session.scalar(select(CourseProfile).where(CourseProfile.slug == slug))

    async def list(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        subject: str | None = None,
        course_level: str | None = None,
        is_published: bool | None = None,
    ) -> tuple[list[CourseProfile], int]:
        conditions: list[ColumnElement[bool]] = []
        if search and search.strip():
            conditions.append(self._search(search, CourseProfile.title, CourseProfile.slug))
        if subject:
            conditions.append(CourseProfile.subject == subject)
        if course_level:
            conditions.append(CourseProfile.course_level == course_level)
        if is_published is not None:
            conditions.append(CourseProfile.is_published == is_published)
        return await self.paginate(page, limit, conditions)


class ScholarshipService(CatalogService[Scholarship]):
    model = Scholarship
    order_by_field = "name"

    async def get_by_slug(self, slug: str) -> Scholarship | None:
        return await self.session.scalar(select(Scholarship).where(Scholarship.slug == slug))

    async def list(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        university_id: UUID | None = None,
        kind: str | None = None,
        is_published: bool | None = None,
    ) -> tuple[list[Scholarship], int]:
        conditions: list[ColumnElement[bool]] = []
        if search and search.strip():
            conditions.append(self._search(search, Scholarship.name, Scholarship.provider))
        if university_id:
            conditions.append(Scholarship.university_id == university_id)
        if kind:
            conditions.append(Scholarship.kind == kind)
        if is_published is not None:
            conditions.append(Scholarship.is_published == is_published)
        return await self.paginate(page, limit, conditions)


class ContentPageService(CatalogService[ContentPage]):
    model = ContentPage
    order_by_field = "display_order"

    async def get_by_key(self, key: str, *, with_blocks: bool = False) -> ContentPage | None:
        query = select(ContentPage).where(ContentPage.key == key)
        if with_blocks:
            query = query.options(selectinload(ContentPage.blocks))
        return await self.session.scalar(query)

    async def get_with_blocks(self, page_id: UUID) -> ContentPage | None:
        return await self.session.scalar(
            select(ContentPage).where(ContentPage.id == page_id).options(selectinload(ContentPage.blocks))
        )

    async def list(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        kind: str | None = None,
        tag: str | None = None,
        is_published: bool | None = None,
    ) -> tuple[list[ContentPage], int]:
        conditions: list[ColumnElement[bool]] = []
        if search and search.strip():
            conditions.append(self._search(search, ContentPage.title, ContentPage.key))
        if kind:
            conditions.append(ContentPage.kind == kind)
        if tag:
            conditions.append(ContentPage.tag == tag)
        if is_published is not None:
            conditions.append(ContentPage.is_published == is_published)
        return await self.paginate(page, limit, conditions)


class ContentBlockService(CatalogService[ContentBlock]):
    model = ContentBlock
    order_by_field = "display_order"

    # `list` is defined last on purpose. It shadows the builtin inside the
    # class body, so any method declared after it cannot annotate `list[...]`
    # without mypy resolving the name to this method. The other services here
    # have the same shape for the same reason.

    async def for_page(self, page_id: UUID) -> list[ContentBlock]:
        result = await self.session.execute(
            select(ContentBlock)
            .where(ContentBlock.page_id == page_id)
            .order_by(ContentBlock.display_order, ContentBlock.created_at)
        )
        return [*result.scalars().all()]

    async def reorder(self, page_id: UUID, block_ids: list[UUID]) -> list[ContentBlock]:
        """Renumber a page's blocks to the given order.

        Blocks carry no unique constraint on (page_id, display_order) precisely
        so this can renumber in place without deferred constraints. Ids that do
        not belong to the page are ignored rather than raising: a stale tab
        should not be able to reassign another page's blocks.
        """
        blocks = await self.for_page(page_id)
        by_id = {block.id: block for block in blocks}
        position = 0
        for block_id in block_ids:
            block = by_id.pop(block_id, None)
            if block is None:
                continue
            block.display_order = position
            position += 1
        # Anything the client did not mention keeps its relative order, after
        # everything it did.
        for block in by_id.values():
            block.display_order = position
            position += 1

        await self.session.commit()
        return await self.for_page(page_id)

    async def list(
        self,
        page: int,
        limit: int,
        page_id: UUID | None = None,
        block_type: str | None = None,
    ) -> tuple[list[ContentBlock], int]:
        conditions: list[ColumnElement[bool]] = []
        if page_id:
            conditions.append(ContentBlock.page_id == page_id)
        if block_type:
            conditions.append(ContentBlock.block_type == block_type)
        return await self.paginate(page, limit, conditions)


class MediaAssetService(CatalogService[MediaAsset]):
    model = MediaAsset
    order_by_field = "created_at"

    async def list(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        kind: str | None = None,
        folder: str | None = None,
    ) -> tuple[list[MediaAsset], int]:
        conditions: list[ColumnElement[bool]] = []
        if search and search.strip():
            conditions.append(self._search(search, MediaAsset.alt_text, MediaAsset.caption))
        if kind:
            conditions.append(MediaAsset.kind == kind)
        if folder:
            conditions.append(MediaAsset.folder == folder)
        return await self.paginate(page, limit, conditions)


class BlogPostService(CatalogService[BlogPost]):
    """Admin CRUD for blog posts.

    These rows existed and were readable by the student portal, but the only
    thing that could write one was `scripts/import_catalog.py`.
    """

    model = BlogPost
    order_by_field = "title"

    async def get_by_slug(self, slug: str) -> BlogPost | None:
        return await self.session.scalar(select(BlogPost).where(BlogPost.slug == slug))

    async def list(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        category: str | None = None,
        is_published: bool | None = None,
    ) -> tuple[list[BlogPost], int]:
        conditions: list[ColumnElement[bool]] = []
        if search and search.strip():
            conditions.append(self._search(search, BlogPost.title, BlogPost.slug, BlogPost.category))
        if category:
            conditions.append(BlogPost.category == category)
        if is_published is not None:
            conditions.append(BlogPost.is_published == is_published)
        return await self.paginate(page, limit, conditions)


class CountryGuideService(CatalogService[CountryGuide]):
    model = CountryGuide
    order_by_field = "display_order"

    async def get_by_slug(self, slug: str) -> CountryGuide | None:
        return await self.session.scalar(select(CountryGuide).where(CountryGuide.slug == slug))

    async def list(
        self,
        page: int,
        limit: int,
        search: str | None = None,
        country_id: UUID | None = None,
        is_published: bool | None = None,
    ) -> tuple[list[CountryGuide], int]:
        conditions: list[ColumnElement[bool]] = []
        if search and search.strip():
            conditions.append(self._search(search, CountryGuide.title, CountryGuide.slug))
        if country_id:
            conditions.append(CountryGuide.country_id == country_id)
        if is_published is not None:
            conditions.append(CountryGuide.is_published == is_published)
        return await self.paginate(page, limit, conditions)


def _factory(service_cls: type[Any]) -> Any:
    async def dependency(session: AsyncSession = Depends(get_db_session)) -> Any:
        return service_cls(session)

    return dependency


get_university_route_service = _factory(UniversityRouteService)
get_course_profile_service = _factory(CourseProfileService)
get_scholarship_service = _factory(ScholarshipService)
get_content_page_service = _factory(ContentPageService)
get_content_block_service = _factory(ContentBlockService)
get_media_asset_service = _factory(MediaAssetService)
get_blog_post_service = _factory(BlogPostService)
get_country_guide_service = _factory(CountryGuideService)
