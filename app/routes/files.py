"""Signed links to locally stored private files (development only).

With `STORAGE_BACKEND=local` there is no Cloudinary to hand a browser a signed,
expiring URL, so this API does it instead: the link routes (`/documents/{id}/
link` and friends) check ownership and mint a link with
`core.uploads.build_local_file_url`, and this route serves it. The token *is*
the authorisation — a browser tab opened on it sends no bearer token — so it is
signed, expires, and names one file.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from ..core.uploads import serve_local_file_link

router = APIRouter(tags=["Files"])


@router.get("/files/{token}", summary="Open a locally stored private file", include_in_schema=False)
async def open_local_file(token: str) -> Response:
    return serve_local_file_link(token)
