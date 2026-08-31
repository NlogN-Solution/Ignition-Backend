"""Schemas for the CMS (CATALOGUE-CMS-PLAN.md §5.6, §5.7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import BlockType, ContentKind


class ContentBlockBase(BaseModel):
    block_type: BlockType
    #: Shape depends on `block_type` and is validated by the renderer, not
    #: here: adding a block type must not require a schema migration, which is
    #: the property that makes shipping `faq`/`prose`/`callout` first and the
    #: rest later a real option.
    data: dict[str, Any]
    display_order: int = 0
    is_visible: bool = True


class ContentBlockCreate(ContentBlockBase):
    page_id: UUID


class ContentBlockUpdate(BaseModel):
    block_type: BlockType | None = None
    data: dict[str, Any] | None = None
    display_order: int | None = None
    is_visible: bool | None = None


class ContentBlockRead(ContentBlockBase):
    id: UUID
    page_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContentBlockList(BaseModel):
    items: list[ContentBlockRead]
    total: int
    page: int
    limit: int


class ContentBlockReorder(BaseModel):
    """New order for a page's blocks, as ids in the order they should render.

    Reordering is a single call rather than N patches so the renumbering is
    atomic — a half-applied reorder is a visibly broken page.
    """

    block_ids: list[UUID] = Field(min_length=1)


class ContentPageBase(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    kind: ContentKind
    slug: str | None = Field(default=None, max_length=200)
    title: str = Field(min_length=1, max_length=250)
    excerpt: str | None = None
    tag: str | None = Field(default=None, max_length=60)
    hero: dict[str, Any] | None = None
    seo: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    related: list[dict[str, Any]] | None = None
    reading_minutes: int | None = None
    published_at: datetime | None = None
    is_published: bool = False
    author_id: UUID | None = None
    display_order: int = 0


class ContentPageCreate(ContentPageBase):
    pass


class ContentPageUpdate(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=120)
    kind: ContentKind | None = None
    slug: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=250)
    excerpt: str | None = None
    tag: str | None = Field(default=None, max_length=60)
    hero: dict[str, Any] | None = None
    seo: dict[str, Any] | None = None
    source: dict[str, Any] | None = None
    related: list[dict[str, Any]] | None = None
    reading_minutes: int | None = None
    published_at: datetime | None = None
    is_published: bool | None = None
    author_id: UUID | None = None
    display_order: int | None = None


class ContentPageRead(ContentPageBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ContentPageDetail(ContentPageRead):
    """A page with its blocks, in render order."""

    blocks: list[ContentBlockRead] = []


class ContentPageList(BaseModel):
    items: list[ContentPageRead]
    total: int
    page: int
    limit: int


class MediaAssetBase(BaseModel):
    url: str = Field(min_length=1)
    public_id: str | None = Field(default=None, max_length=255)
    kind: str | None = Field(default=None, max_length=20)
    width: int | None = None
    height: int | None = None
    bytes: int | None = None
    alt_text: str | None = Field(default=None, max_length=300)
    caption: str | None = Field(default=None, max_length=300)
    folder: str | None = Field(default=None, max_length=120)
    uploaded_by: UUID | None = None


class MediaAssetCreate(MediaAssetBase):
    pass


class MediaAssetUpdate(BaseModel):
    url: str | None = Field(default=None, min_length=1)
    public_id: str | None = Field(default=None, max_length=255)
    kind: str | None = Field(default=None, max_length=20)
    width: int | None = None
    height: int | None = None
    bytes: int | None = None
    alt_text: str | None = Field(default=None, max_length=300)
    caption: str | None = Field(default=None, max_length=300)
    folder: str | None = Field(default=None, max_length=120)
    uploaded_by: UUID | None = None


class MediaAssetRead(MediaAssetBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MediaAssetList(BaseModel):
    items: list[MediaAssetRead]
    total: int
    page: int
    limit: int


class ContentPagePublish(BaseModel):
    """Publish or retract a page.

    Separate from `ContentPageUpdate` because publishing is not the same kind
    of act as editing: it stamps `published_at`, and it tells the public site
    to drop its cache. A PATCH that happened to include `is_published` would
    do neither, which is how a CMS ends up with a page that is live in the
    database and stale on the site.
    """

    is_published: bool = True


class ContentPagePublishResult(BaseModel):
    page: ContentPageRead
    #: False when the landing was never called (no shared secret configured) or
    #: could not be reached. The page is published either way; the site catches
    #: up on its own revalidation timer.
    revalidated: bool


class ContentPagePreview(BaseModel):
    """A signed, short-lived link to the draft on the public site."""

    #: None when no shared secret is configured — there is nowhere to preview.
    url: str | None = None
