"""Admin CRUD for the CMS (CATALOGUE-CMS-PLAN.md Phase 2).

Pages and their blocks, the media library, and the blog/guide CRUD that has
never existed — until now the only thing that could write a `blog_posts` row
was `scripts/import_catalog.py`.

As in `routes/catalogue.py`, reads here are staff reads. The public surface is
`routes/public.py` (Phase 3) and serves published records only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile

from ..api.auth import get_current_user, require_role
from ..api.exceptions import NotFoundException
from ..core.landing import TAG_CONTENT, preview_url, revalidate
from ..core.uploads import AVATAR_EXTENSIONS, CONTENT_FOLDER, store_upload
from ..models import User
from ..models.enums import UserRole
from ..schemas.academic import (
    BlogPostCreate,
    BlogPostList,
    BlogPostRead,
    BlogPostUpdate,
    CountryGuideCreate,
    CountryGuideList,
    CountryGuideRead,
    CountryGuideUpdate,
)
from ..schemas.content import (
    ContentBlockCreate,
    ContentBlockList,
    ContentBlockRead,
    ContentBlockReorder,
    ContentBlockUpdate,
    ContentPageCreate,
    ContentPageDetail,
    ContentPageList,
    ContentPagePreview,
    ContentPagePublish,
    ContentPagePublishResult,
    ContentPageRead,
    ContentPageUpdate,
    MediaAssetCreate,
    MediaAssetList,
    MediaAssetRead,
    MediaAssetUpdate,
)
from ..services.catalogue_service import (
    BlogPostService,
    ContentBlockService,
    ContentPageService,
    CountryGuideService,
    MediaAssetService,
    get_blog_post_service,
    get_content_block_service,
    get_content_page_service,
    get_country_guide_service,
    get_media_asset_service,
)

router = APIRouter(tags=["Content"])

_MANAGE_WEBSITE = require_role(UserRole.ADMIN, UserRole.MARKETING)


# --- Pages -------------------------------------------------------------------


@router.get("/content-pages", response_model=ContentPageList, summary="List content pages")
async def list_content_pages(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    kind: str | None = None,
    tag: str | None = None,
    is_published: bool | None = None,
    service: ContentPageService = Depends(get_content_page_service),
    _: object = Depends(get_current_user),
) -> ContentPageList:
    items, total = await service.list(page, limit, search=search, kind=kind, tag=tag, is_published=is_published)
    return ContentPageList(items=items, total=total, page=page, limit=limit)


@router.get("/content-pages/{page_id}", response_model=ContentPageDetail, summary="Get page with blocks")
async def get_content_page(
    page_id: UUID,
    service: ContentPageService = Depends(get_content_page_service),
    _: object = Depends(get_current_user),
) -> ContentPageDetail:
    content_page = await service.get_with_blocks(page_id)
    if content_page is None:
        raise NotFoundException("Content page not found")
    return ContentPageDetail.model_validate(content_page)


@router.post("/content-pages", response_model=ContentPageRead, summary="Create content page")
async def create_content_page(
    payload: ContentPageCreate,
    service: ContentPageService = Depends(get_content_page_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ContentPageRead:
    return ContentPageRead.model_validate(await service.create(payload.model_dump()))


@router.patch("/content-pages/{page_id}", response_model=ContentPageRead, summary="Update content page")
async def update_content_page(
    page_id: UUID,
    payload: ContentPageUpdate,
    service: ContentPageService = Depends(get_content_page_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ContentPageRead:
    content_page = await service.get(page_id)
    if content_page is None:
        raise NotFoundException("Content page not found")
    return ContentPageRead.model_validate(await service.update(content_page, payload.model_dump(exclude_unset=True)))


@router.delete("/content-pages/{page_id}", response_model=ContentPageRead, summary="Delete content page")
async def delete_content_page(
    page_id: UUID,
    service: ContentPageService = Depends(get_content_page_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ContentPageRead:
    content_page = await service.get(page_id)
    if content_page is None:
        raise NotFoundException("Content page not found")
    return ContentPageRead.model_validate(await service.delete(content_page))


@router.post(
    "/content-pages/{page_id}/publish",
    response_model=ContentPagePublishResult,
    summary="Publish or retract a content page",
)
async def publish_content_page(
    page_id: UUID,
    payload: ContentPagePublish,
    service: ContentPageService = Depends(get_content_page_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ContentPagePublishResult:
    """Flip the flag, stamp the date, and tell the public site to forget.

    `published_at` is set the first time only: it is the date printed on an
    article, so retracting a post to fix a typo and putting it back must not
    make a month-old piece look like today's.
    """
    content_page = await service.get(page_id)
    if content_page is None:
        raise NotFoundException("Content page not found")

    changes: dict[str, object] = {"is_published": payload.is_published}
    if payload.is_published and content_page.published_at is None:
        changes["published_at"] = datetime.now(UTC)

    updated = await service.update(content_page, changes)
    revalidated = await revalidate(TAG_CONTENT)
    return ContentPagePublishResult(page=ContentPageRead.model_validate(updated), revalidated=revalidated)


@router.get(
    "/content-pages/{page_id}/preview",
    response_model=ContentPagePreview,
    summary="Signed preview link for a draft",
)
async def preview_content_page(
    page_id: UUID,
    service: ContentPageService = Depends(get_content_page_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ContentPagePreview:
    """Mint a link that shows the draft on the public site.

    The token is minted per request rather than stored, so it expires by
    elapsed time alone and revoking it is a matter of waiting rather than of
    bookkeeping.
    """
    content_page = await service.get(page_id)
    if content_page is None:
        raise NotFoundException("Content page not found")
    return ContentPagePreview(url=preview_url(content_page.key, content_page.slug))


@router.put(
    "/content-pages/{page_id}/blocks/order",
    response_model=list[ContentBlockRead],
    summary="Reorder a page's blocks",
)
async def reorder_content_blocks(
    page_id: UUID,
    payload: ContentBlockReorder,
    pages: ContentPageService = Depends(get_content_page_service),
    blocks: ContentBlockService = Depends(get_content_block_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> list[ContentBlockRead]:
    """Renumber in one call, so a drag never leaves the page half-ordered."""
    content_page = await pages.get(page_id)
    if content_page is None:
        raise NotFoundException("Content page not found")
    ordered = await blocks.reorder(page_id, payload.block_ids)
    return [ContentBlockRead.model_validate(block) for block in ordered]


# --- Blocks ------------------------------------------------------------------


@router.get("/content-blocks", response_model=ContentBlockList, summary="List content blocks")
async def list_content_blocks(
    page: int = 1,
    limit: int = 50,
    page_id: UUID | None = None,
    block_type: str | None = None,
    service: ContentBlockService = Depends(get_content_block_service),
    _: object = Depends(get_current_user),
) -> ContentBlockList:
    items, total = await service.list(page, limit, page_id=page_id, block_type=block_type)
    return ContentBlockList(items=items, total=total, page=page, limit=limit)


@router.get("/content-blocks/{block_id}", response_model=ContentBlockRead, summary="Get content block")
async def get_content_block(
    block_id: UUID,
    service: ContentBlockService = Depends(get_content_block_service),
    _: object = Depends(get_current_user),
) -> ContentBlockRead:
    block = await service.get(block_id)
    if block is None:
        raise NotFoundException("Content block not found")
    return ContentBlockRead.model_validate(block)


@router.post("/content-blocks", response_model=ContentBlockRead, summary="Create content block")
async def create_content_block(
    payload: ContentBlockCreate,
    service: ContentBlockService = Depends(get_content_block_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ContentBlockRead:
    return ContentBlockRead.model_validate(await service.create(payload.model_dump()))


@router.patch("/content-blocks/{block_id}", response_model=ContentBlockRead, summary="Update content block")
async def update_content_block(
    block_id: UUID,
    payload: ContentBlockUpdate,
    service: ContentBlockService = Depends(get_content_block_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ContentBlockRead:
    block = await service.get(block_id)
    if block is None:
        raise NotFoundException("Content block not found")
    return ContentBlockRead.model_validate(await service.update(block, payload.model_dump(exclude_unset=True)))


@router.delete("/content-blocks/{block_id}", response_model=ContentBlockRead, summary="Delete content block")
async def delete_content_block(
    block_id: UUID,
    service: ContentBlockService = Depends(get_content_block_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> ContentBlockRead:
    block = await service.get(block_id)
    if block is None:
        raise NotFoundException("Content block not found")
    return ContentBlockRead.model_validate(await service.delete(block))


# --- Media -------------------------------------------------------------------


@router.get("/media-assets", response_model=MediaAssetList, summary="List media assets")
async def list_media_assets(
    page: int = 1,
    limit: int = 40,
    search: str | None = None,
    kind: str | None = None,
    folder: str | None = None,
    service: MediaAssetService = Depends(get_media_asset_service),
    _: object = Depends(get_current_user),
) -> MediaAssetList:
    items, total = await service.list(page, limit, search=search, kind=kind, folder=folder)
    return MediaAssetList(items=items, total=total, page=page, limit=limit)


@router.get("/media-assets/{asset_id}", response_model=MediaAssetRead, summary="Get media asset")
async def get_media_asset(
    asset_id: UUID,
    service: MediaAssetService = Depends(get_media_asset_service),
    _: object = Depends(get_current_user),
) -> MediaAssetRead:
    asset = await service.get(asset_id)
    if asset is None:
        raise NotFoundException("Media asset not found")
    return MediaAssetRead.model_validate(asset)


@router.post("/media-assets", response_model=MediaAssetRead, summary="Register media asset")
async def create_media_asset(
    payload: MediaAssetCreate,
    service: MediaAssetService = Depends(get_media_asset_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> MediaAssetRead:
    """Record an already-uploaded asset.

    The upload endpoint that puts bytes in Cloudinary and calls this is Phase 3
    work (`CONTENT_FOLDER` in `app/core/uploads.py`); this exists first so the
    library has somewhere to store what it lists.
    """
    return MediaAssetRead.model_validate(await service.create(payload.model_dump()))


@router.post("/media-assets/upload", response_model=MediaAssetRead, summary="Upload media asset")
async def upload_media_asset(
    file: UploadFile = File(...),
    service: MediaAssetService = Depends(get_media_asset_service),
    user: User = Depends(_MANAGE_WEBSITE),
) -> MediaAssetRead:
    """Put a file on the CDN and record it in one call.

    This is the only upload path in the codebase that passes `private=False`.
    Everything else here — avatars, student documents, leave attachments — is
    private and reachable only through a short-lived signed URL. A university
    logo or a guide's hero image is different: it has to be fetchable by any
    visitor reading the public site, so it needs a real public `secure_url`,
    which no endpoint produced until now.
    """
    stored = await store_upload(file, AVATAR_EXTENSIONS, folder=CONTENT_FOLDER, private=False)
    asset = await service.create(
        {
            "url": stored.url,
            "public_id": stored.stored_file_name,
            "kind": "image",
            "bytes": stored.size,
            "folder": CONTENT_FOLDER,
            "uploaded_by": user.id,
        }
    )
    return MediaAssetRead.model_validate(asset)


@router.patch("/media-assets/{asset_id}", response_model=MediaAssetRead, summary="Update media asset")
async def update_media_asset(
    asset_id: UUID,
    payload: MediaAssetUpdate,
    service: MediaAssetService = Depends(get_media_asset_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> MediaAssetRead:
    asset = await service.get(asset_id)
    if asset is None:
        raise NotFoundException("Media asset not found")
    return MediaAssetRead.model_validate(await service.update(asset, payload.model_dump(exclude_unset=True)))


@router.delete("/media-assets/{asset_id}", response_model=MediaAssetRead, summary="Delete media asset")
async def delete_media_asset(
    asset_id: UUID,
    service: MediaAssetService = Depends(get_media_asset_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> MediaAssetRead:
    asset = await service.get(asset_id)
    if asset is None:
        raise NotFoundException("Media asset not found")
    return MediaAssetRead.model_validate(await service.delete(asset))


# --- Blog posts --------------------------------------------------------------


@router.get("/blog-posts", response_model=BlogPostList, summary="List blog posts")
async def list_blog_posts(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    category: str | None = None,
    is_published: bool | None = None,
    service: BlogPostService = Depends(get_blog_post_service),
    _: object = Depends(get_current_user),
) -> BlogPostList:
    items, total = await service.list(page, limit, search=search, category=category, is_published=is_published)
    return BlogPostList(items=items, total=total, page=page, limit=limit)


@router.get("/blog-posts/{post_id}", response_model=BlogPostRead, summary="Get blog post")
async def get_blog_post(
    post_id: UUID,
    service: BlogPostService = Depends(get_blog_post_service),
    _: object = Depends(get_current_user),
) -> BlogPostRead:
    post = await service.get(post_id)
    if post is None:
        raise NotFoundException("Blog post not found")
    return BlogPostRead.model_validate(post)


@router.post("/blog-posts", response_model=BlogPostRead, summary="Create blog post")
async def create_blog_post(
    payload: BlogPostCreate,
    service: BlogPostService = Depends(get_blog_post_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> BlogPostRead:
    return BlogPostRead.model_validate(await service.create(payload.model_dump()))


@router.patch("/blog-posts/{post_id}", response_model=BlogPostRead, summary="Update blog post")
async def update_blog_post(
    post_id: UUID,
    payload: BlogPostUpdate,
    service: BlogPostService = Depends(get_blog_post_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> BlogPostRead:
    post = await service.get(post_id)
    if post is None:
        raise NotFoundException("Blog post not found")
    return BlogPostRead.model_validate(await service.update(post, payload.model_dump(exclude_unset=True)))


@router.delete("/blog-posts/{post_id}", response_model=BlogPostRead, summary="Delete blog post")
async def delete_blog_post(
    post_id: UUID,
    service: BlogPostService = Depends(get_blog_post_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> BlogPostRead:
    post = await service.get(post_id)
    if post is None:
        raise NotFoundException("Blog post not found")
    return BlogPostRead.model_validate(await service.delete(post))


# --- Country guides ----------------------------------------------------------


@router.get("/country-guides", response_model=CountryGuideList, summary="List country guides")
async def list_country_guides(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    country_id: UUID | None = None,
    is_published: bool | None = None,
    service: CountryGuideService = Depends(get_country_guide_service),
    _: object = Depends(get_current_user),
) -> CountryGuideList:
    items, total = await service.list(page, limit, search=search, country_id=country_id, is_published=is_published)
    return CountryGuideList(items=items, total=total, page=page, limit=limit)


@router.get("/country-guides/{guide_id}", response_model=CountryGuideRead, summary="Get country guide")
async def get_country_guide(
    guide_id: UUID,
    service: CountryGuideService = Depends(get_country_guide_service),
    _: object = Depends(get_current_user),
) -> CountryGuideRead:
    guide = await service.get(guide_id)
    if guide is None:
        raise NotFoundException("Country guide not found")
    return CountryGuideRead.model_validate(guide)


@router.post("/country-guides", response_model=CountryGuideRead, summary="Create country guide")
async def create_country_guide(
    payload: CountryGuideCreate,
    service: CountryGuideService = Depends(get_country_guide_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> CountryGuideRead:
    return CountryGuideRead.model_validate(await service.create(payload.model_dump()))


@router.patch("/country-guides/{guide_id}", response_model=CountryGuideRead, summary="Update country guide")
async def update_country_guide(
    guide_id: UUID,
    payload: CountryGuideUpdate,
    service: CountryGuideService = Depends(get_country_guide_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> CountryGuideRead:
    guide = await service.get(guide_id)
    if guide is None:
        raise NotFoundException("Country guide not found")
    return CountryGuideRead.model_validate(await service.update(guide, payload.model_dump(exclude_unset=True)))


@router.delete("/country-guides/{guide_id}", response_model=CountryGuideRead, summary="Delete country guide")
async def delete_country_guide(
    guide_id: UUID,
    service: CountryGuideService = Depends(get_country_guide_service),
    _: object = Depends(_MANAGE_WEBSITE),
) -> CountryGuideRead:
    guide = await service.get(guide_id)
    if guide is None:
        raise NotFoundException("Country guide not found")
    return CountryGuideRead.model_validate(await service.delete(guide))
