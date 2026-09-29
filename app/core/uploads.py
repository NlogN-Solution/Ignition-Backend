from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode
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

#: What may be attached to a message. Everything a document may be, plus the
#: audio formats `MediaRecorder` actually produces — `audio/webm` in Chromium
#: and Firefox, `audio/mp4` in Safari. Both are needed or voice notes work in
#: one browser family and silently fail in the other.
MESSAGE_ATTACHMENT_EXTENSIONS = DOCUMENT_EXTENSIONS | frozenset(
    {".webm", ".m4a", ".mp4", ".mp3", ".ogg", ".wav"}
)

_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

_READ_CHUNK_BYTES = 1024 * 1024

#: Cloudinary folders, one per kind of upload — keeps the dashboard on
#: Cloudinary's side navigable and lets each kind carry its own retention
#: policy later without touching the others.
AVATAR_FOLDER = "ignition/avatars"
DOCUMENT_FOLDER = "ignition/documents"
LEAVE_ATTACHMENT_FOLDER = "ignition/leave-attachments"
#: Files and voice notes sent inside a correspondence thread.
#:
#: Its own folder, and deliberately *not* `ignition/documents`: the document
#: vault is the student's verifiable paperwork — the passports and transcripts
#: staff approve — and a screenshot pasted into a reply has no business turning
#: up in a verification queue. Same private delivery, same signed access, a
#: different meaning.
MESSAGE_ATTACHMENT_FOLDER = "ignition/message-attachments"
#: Public site media — the one folder uploaded with `private=False`, because a
#: university logo or a guide's hero image has to be fetchable by anyone
#: reading the page. Everything else here is private and served only through a
#: signed URL this server mints after an ownership check.
CONTENT_FOLDER = "ignition/content"

#: Characters Cloudinary will carry safely inside an `fl_attachment:` flag.
#: The flag is interpolated into the URL *path*, and the path is what the
#: signature is computed over — so a filename with a space in it ("offer
#: letter.pdf") produces a URL the browser must re-encode and Cloudinary then
#: reads back differently from the string that was signed.
_ATTACHMENT_NAME_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def _attachment_name(download_name: str) -> str:
    """A download filename that survives being part of a signed URL path."""
    cleaned = "".join(c if c in _ATTACHMENT_NAME_SAFE else "_" for c in Path(download_name).stem)
    return cleaned.strip("_") or "document"


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
    private: bool,
) -> StoredFile:
    """Validate an upload and push it to Cloudinary under a generated name.

    The client's filename never reaches storage — only its extension does, and
    only from the allowlist. ED360 interpolates `Path(file.filename).suffix`
    straight into the stored name with no check and no size limit.

    `private=True` uploads under Cloudinary's `authenticated` delivery type,
    which serves no public URL: the asset is only reachable through a signed
    URL (`build_download_url`), minted only after whatever ownership check the
    caller applies first.

    `private` has **no default**. It used to default to False, and the leave
    route took that default for employees' medical notes, which became public
    CDN assets (FAPI-SEC-006). Every caller now has to say which it means.

    The file is read in chunks and abandoned the moment it passes the size
    limit, rather than read whole and measured afterwards (FAPI-SEC-012).
    Starlette has already spooled large parts to disk, so this bounds what is
    held in memory to the limit itself.
    """
    extension = Path(file.filename or "").suffix.lower()
    if extension not in allowed_extensions:
        raise BadRequestException(f"Unsupported file type. Allowed: {', '.join(sorted(allowed_extensions))}")

    limit = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise BadRequestException(f"File exceeds the {settings.MAX_UPLOAD_SIZE_MB} MB limit")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise BadRequestException("Uploaded file is empty")

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


def build_download_url(
    stored_file_name: str,
    *,
    folder: str,
    download_name: str,
    inline: bool = False,
) -> str | None:
    """A short-lived, signed URL for a `private=True` upload.

    Returns `None` under `ENVIRONMENT=test`, where uploads live on a local
    scratch dir with no URL of their own — callers fall back to the
    authenticated download route in that case.

    `inline=False` serves the file as an attachment named after
    `download_name`, which is what a Download button wants. `inline=True`
    serves it inline so a PDF or an image opens in the browser's own viewer,
    which is what a Preview wants — the same bytes, a different
    Content-Disposition.

    Only the basename of `stored_file_name` is ever used: `POST /documents`
    lets staff set that column directly, so a `folder`-escaping public_id must
    not reach Cloudinary unsanitized.

    ## Why the download API and not a delivery URL

    This used to mint a signed *delivery* URL (`res.cloudinary.com/...`), and
    neither View nor Download worked for most files:

    * **Every PDF was a 401** ("deny or ACL failure"). Cloudinary accounts
      block delivery of PDF and ZIP files by default, signed or not, and PDFs
      are most of what the document vault holds.
    * **Images were a 404.** `store_upload` keeps the extension in the
      public_id (`ignition/documents/<uuid>.jpg`), and a delivery URL reads a
      trailing `.jpg` as the *format*, so it asked for `<uuid>` — which does
      not exist.

    The download API (`api.cloudinary.com/.../download`) addresses the asset by
    its exact public_id and is not subject to the delivery restriction, so it
    serves both. It is signed with the API secret, and unlike a delivery URL it
    genuinely expires: `expires_at` is enforced, after
    `CLOUDINARY_URL_TTL_SECONDS`. The URL is still a bearer credential until
    then — do not log, store or share it.
    """
    safe_name = Path(stored_file_name).name
    if settings.ENVIRONMENT == "test":
        return None

    extension = Path(safe_name).suffix.lower()
    resource_type = _resource_type_for(extension)
    params: dict[str, object] = {
        "timestamp": cloudinary.utils.now(),
        "public_id": f"{folder}/{safe_name}",
        # Images are addressed as public_id + format; a raw asset's public_id
        # already carries its extension and takes no format.
        "format": extension.lstrip(".") if resource_type == "image" else "",
        "type": "authenticated",
        "expires_at": int(time.time()) + settings.CLOUDINARY_URL_TTL_SECONDS,
    }
    if not inline:
        params["attachment"] = True
        # Not accepted by the SDK's `private_download_url`, but honoured by the
        # API when signed: without it every download is saved as "file.pdf".
        params["target_filename"] = f"{_attachment_name(download_name)}{extension}"
    signed = cloudinary.utils.sign_request(params, {})
    return f"{cloudinary.utils.cloudinary_api_url('download', resource_type=resource_type)}?{urlencode(signed)}"


def build_download_response(
    stored_file_name: str,
    *,
    folder: str,
    mime_type: str | None,
    download_name: str,
    inline: bool = False,
) -> Response:
    """Serve a `private=True` upload, after the caller has already checked
    the requester may see it.

    In production this redirects to a signed, expiring Cloudinary download
    URL; see `build_download_url`. In tests it resolves the file straight
    off the hermetic local scratch dir used by `store_upload`.
    """
    safe_name = Path(stored_file_name).name

    if settings.ENVIRONMENT == "test":
        upload_dir = settings.upload_dir.resolve()
        candidate = (upload_dir / safe_name).resolve()
        if candidate.parent != upload_dir or not candidate.is_file():
            raise NotFoundException("File not found")
        return FileResponse(
            candidate,
            media_type=mime_type or "application/octet-stream",
            filename=download_name,
            content_disposition_type="inline" if inline else "attachment",
        )

    url = build_download_url(safe_name, folder=folder, download_name=download_name, inline=inline)
    if url is None:  # pragma: no cover - only reachable if ENVIRONMENT flips mid-request
        raise NotFoundException("File not found")
    # The redirect target is a bearer credential for the file; keep it out of
    # every cache between here and the browser.
    return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
