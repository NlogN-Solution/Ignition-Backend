from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from ..api.exceptions import BadRequestException, NotFoundException
from .config import get_settings

settings = get_settings()

#: Avatars are rendered in an <img>; anything that can carry script must not be
#: storable as one.
AVATAR_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})

#: Supporting documents: passports, transcripts, offer letters, bank statements.
DOCUMENT_EXTENSIONS = frozenset(
    {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".doc", ".docx", ".xls", ".xlsx", ".txt"}
)


async def store_upload(file: UploadFile, allowed_extensions: frozenset[str]) -> tuple[str, bytes]:
    """Validate an upload and write it under a generated name.

    Returns the stored filename and the bytes read. The client's filename never
    reaches the filesystem — only its extension does, and only from the
    allowlist. ED360 interpolates `Path(file.filename).suffix` straight into the
    stored name with no check and no size limit.
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
    (settings.upload_dir / stored_file_name).write_bytes(content)
    return stored_file_name, content


def resolve_stored_path(stored_file_name: str) -> Path:
    """Map a stored filename back to a path inside the upload directory.

    Takes only the basename and then confirms the resolved path really is under
    `upload_dir`, so a `stored_file_name` of `../../etc/passwd` — which a staff
    account could otherwise put in the column via `POST /documents` — cannot
    read outside it.
    """
    upload_dir = settings.upload_dir.resolve()
    candidate = (upload_dir / Path(stored_file_name).name).resolve()
    if candidate.parent != upload_dir or not candidate.is_file():
        raise NotFoundException("File not found")
    return candidate
