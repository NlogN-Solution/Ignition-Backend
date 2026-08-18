from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from fastapi import UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response

from ..api.exceptions import BadRequestException, NotFoundException
from .config import get_settings

settings = get_settings()

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)

#: Avatars are rendered in an <img>; anything that can carry script must not be
#: storable as one.
AVATAR_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

#: Supporting documents: passports, transcripts, offer letters, bank statements.
DOCUMENT_EXTENSIONS = frozenset(
    {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".doc", ".docx", ".xls", ".xlsx", ".txt"}
)

_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

#: Cloudinary folders, one per kind of upload — keeps the dashboard on
#: Cloudinary's side navigable and lets each kind carry its own retention
#: policy later without touching the others.
AVATAR_FOLDER = "ignition/avatars"
DOCUMENT_FOLDER = "ignition/documents"
LEAVE_ATTACHMENT_FOLDER = "ignition/leave-attachments"

#: How long a signed document-download URL stays valid. Short: it's handed
#: straight to the browser as a redirect target, never stored or shared.
_SIGNED_URL_TTL_SECONDS = 300


def _resource_type_for(extension: str) -> str:
    """Cloudinary buckets uploads by kind; anything that isn't an image must go
    through the `raw` pipeline or the upload is rejected."""
    return "image" if extension in _IMAGE_EXTENSIONS else "raw"


@dataclass(frozen=True)
class StoredFile:
    stored_file_name: str
    #: Public, CDN-servable URL. None for `private=True` uploads — those have
    #: no public URL at all; see `build_download_response`.
    url: str | None
    size: int


async def store_upload(
    file: UploadFile,
    allowed_extensions: frozenset[str],
    *,
    folder: str,
    private: bool = False,
) -> StoredFile:
    """Validate an upload and push it to Cloudinary under a generated name.

    The client's filename never reaches storage — only its extension does, and
    only from the allowlist. ED360 interpolates `Path(file.filename).suffix`
    straight into the stored name with no check and no size limit.

    `private=True` uploads under Cloudinary's `authenticated` delivery type,
    which serves no public URL: the asset is only reachable through a
    short-lived signed URL (`build_download_response`), gated on whatever
    ownership check the caller applies first.
    """
    extension = Path(file.filename or "").suffix.lower()
    if extension not in allowed_extensions:
        raise BadRequestException(f"Unsupported file type. Allowed: {', '.join(sorted(allowed_extensions))}")

    content = await file.read()
    if not content:
        raise BadRequestException("Uploaded file is empty")
    if len(content) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise BadRequestException(f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB} MB limit")

    stored_file_name = f"{uuid4()}{extension}"

    if settings.ENVIRONMENT == "test":
        # Cloudinary needs real credentials and a network round-trip; the test
        # suite stays hermetic by writing to a local scratch dir instead — the
        # same trade-off core/rate_limit.py makes for the rate limiter under
        # ENVIRONMENT=test.
        (settings.upload_dir / stored_file_name).write_bytes(content)
        return StoredFile(
            stored_file_name=stored_file_name,
            url=None if private else f"/uploads/{stored_file_name}",
            size=len(content),
        )

    result = await asyncio.to_thread(
        cloudinary.uploader.upload,
        content,
        public_id=stored_file_name,
        folder=folder,
        resource_type=_resource_type_for(extension),
        type="authenticated" if private else "upload",
        use_filename=False,
        unique_filename=False,
        overwrite=False,
    )
    return StoredFile(
        stored_file_name=stored_file_name,
        url=None if private else result.get("secure_url"),
        size=result.get("bytes", len(content)),
    )


def build_download_response(
    stored_file_name: str,
    *,
    folder: str,
    mime_type: str | None,
    download_name: str,
) -> Response:
    """Serve a `private=True` upload, after the caller has already checked
    the requester may see it.

    Only the basename of `stored_file_name` is ever used — `POST /documents`
    lets staff set this column directly, so a value like `../../etc/passwd`
    (or, on Cloudinary, a `folder`-escaping public_id) must not reach storage
    unsanitized. In production this redirects to a signed Cloudinary URL that
    expires in a few minutes; in tests it resolves the file straight off the
    hermetic local scratch dir used by `store_upload`.
    """
    safe_name = Path(stored_file_name).name

    if settings.ENVIRONMENT == "test":
        upload_dir = settings.upload_dir.resolve()
        candidate = (upload_dir / safe_name).resolve()
        if candidate.parent != upload_dir or not candidate.is_file():
            raise NotFoundException("File not found")
        return FileResponse(candidate, media_type=mime_type or "application/octet-stream", filename=download_name)

    extension = Path(safe_name).suffix.lower()
    public_id = f"{folder}/{safe_name}"
    url, _ = cloudinary.utils.cloudinary_url(
        public_id,
        resource_type=_resource_type_for(extension),
        type="authenticated",
        sign_url=True,
        secure=True,
        expires_at=int(time.time()) + _SIGNED_URL_TTL_SECONDS,
        flags=f"attachment:{download_name}",
    )
    return RedirectResponse(url, status_code=307)
