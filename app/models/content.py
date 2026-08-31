"""The CMS core (CATALOGUE-CMS-PLAN.md §5.6, §5.7).

A page is an ordered list of typed blocks, and every block type renders through
a component the public site already has. That is what makes moving the guides
behind an editor tractable rather than a rewrite: the landing's existing UI
components *are* the block library.

Importing the spreadsheet fixes fictional data but not the second problem —
that only a developer can change a headline. Both, or the first has to be
redone in six months.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin
from ..db.types import enum_type
from .enums import BlockType, ContentKind

if TYPE_CHECKING:
    from .user import User


class ContentPage(Base, UUIDPKMixin, TimestampMixin):
    """A page, guide, post or fragment.

    `key` is the stable handle code refers to ("home.hero", "guide.visa"), and
    `slug` is what appears in a URL. They are separate because a fragment has a
    key and no URL, and an editor renaming a guide's slug must not break the
    code that asks for it.

    Absent keys fall back to the code default in the landing, so the site never
    renders a blank section and developers can keep shipping copy in code.
    """

    __tablename__ = "content_pages"

    key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    kind: Mapped[ContentKind] = mapped_column(
        enum_type(ContentKind, "content_kind", create_type=False),
        nullable=False,
    )
    slug: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    #: Blog tag or guide group — one of the site's closed vocabularies.
    tag: Mapped[str | None] = mapped_column(String(60))

    #: {eyebrow, title, intro, crumbs, image}
    hero: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: {title, description, ogImage, noindex}
    seo: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: {label, href} — the authority this page defers to.
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: [{label, href}]
    related: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    reading_minutes: Mapped[int | None] = mapped_column(Integer)

    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    author: Mapped[User | None] = relationship()
    blocks: Mapped[list[ContentBlock]] = relationship(
        back_populates="page",
        cascade="all, delete-orphan",
        order_by="ContentBlock.display_order, ContentBlock.created_at",
    )

    __table_args__ = (
        Index("idx_content_pages_key", "key"),
        Index("idx_content_pages_kind", "kind"),
        Index("idx_content_pages_is_published", "is_published"),
    )

    def __repr__(self) -> str:
        return f"<ContentPage id={self.id} key={self.key}>"


class ContentBlock(Base, UUIDPKMixin, TimestampMixin):
    """One typed block of a page.

    There is deliberately **no** unique constraint on (page_id,
    display_order): reordering in the admin would then need deferred
    constraints to avoid colliding mid-swap. Order by (display_order,
    created_at) and renumber on save instead.
    """

    __tablename__ = "content_blocks"

    page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    block_type: Mapped[BlockType] = mapped_column(
        enum_type(BlockType, "content_block_type", create_type=False),
        nullable=False,
    )
    #: Shape depends on `block_type`; the renderer switches on it. Stored as a
    #: constrained document rather than HTML — this codebase has zero instances
    #: of `dangerouslySetInnerHTML` and this is not the place to add the first.
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    page: Mapped[ContentPage] = relationship(back_populates="blocks")

    __table_args__ = (Index("idx_content_blocks_page_id", "page_id"),)

    def __repr__(self) -> str:
        return f"<ContentBlock id={self.id} page_id={self.page_id} type={self.block_type}>"


class MediaAsset(Base, UUIDPKMixin, TimestampMixin):
    """An uploaded image, PDF or other file used by the public site.

    Backed by the existing Cloudinary path (`app/core/uploads.py`) with the
    private flag flipped: `store_upload(..., private=False)` returns a public
    `secure_url`, which is exactly what `logo_url` / `image_url` /
    `hero_image_url` want and what no endpoint produced before.
    """

    __tablename__ = "media_assets"

    url: Mapped[str] = mapped_column(Text, nullable=False)
    #: Cloudinary public id, retained so the asset can be deleted later.
    public_id: Mapped[str | None] = mapped_column(String(255))
    #: image | pdf | other
    kind: Mapped[str | None] = mapped_column(String(20))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bytes: Mapped[int | None] = mapped_column(Integer)
    alt_text: Mapped[str | None] = mapped_column(String(300))
    caption: Mapped[str | None] = mapped_column(String(300))
    folder: Mapped[str | None] = mapped_column(String(120))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    uploader: Mapped[User | None] = relationship()

    __table_args__ = (
        Index("idx_media_assets_kind", "kind"),
        Index("idx_media_assets_folder", "folder"),
    )

    def __repr__(self) -> str:
        return f"<MediaAsset id={self.id} url={self.url}>"
