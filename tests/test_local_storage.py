"""Local file storage (STORAGE_BACKEND=local): what stands in for Cloudinary.

The property that matters is the one Cloudinary gave for free: a private file
is reachable only through a link this API minted after an ownership check, and
that link names one file, cannot be altered, and expires.
"""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core import uploads
from app.core.config import get_settings
from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio


def _private_file(content: bytes = b"%PDF-1.4 secret") -> str:
    name = f"{uuid4()}.pdf"
    (uploads.local_dir(private=True) / name).write_bytes(content)
    return name


def _path_of(url: str) -> str:
    return url.removeprefix(get_settings().BACKEND_PUBLIC_URL.rstrip("/"))


async def test_tests_and_development_store_locally() -> None:
    assert get_settings().uses_local_storage


async def test_a_signed_link_opens_the_file(client: AsyncClient) -> None:
    name = _private_file()
    url = uploads.build_local_file_url(name, download_name="offer.pdf", inline=False)

    response = await client.get(_path_of(url))
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 secret"
    assert "attachment" in response.headers["content-disposition"]
    assert "offer.pdf" in response.headers["content-disposition"]


async def test_a_tampered_link_is_refused(client: AsyncClient) -> None:
    mine = _private_file()
    theirs = _private_file(b"someone else's passport")
    url = uploads.build_local_file_url(mine, download_name="mine.pdf", inline=True)
    payload, signature = _path_of(url).rsplit("/", 1)[1].split(".", 1)

    forged_payload = uploads._b64(uploads._unb64(payload).replace(mine.encode(), theirs.encode()))
    response = await client.get(f"/api/v1/files/{forged_payload}.{signature}")
    assert response.status_code == 404


async def test_an_expired_link_is_refused(client: AsyncClient, monkeypatch) -> None:
    name = _private_file()
    url = uploads.build_local_file_url(name, download_name="a.pdf", inline=True)
    real_time = time.time
    monkeypatch.setattr(uploads.time, "time", lambda: real_time() + get_settings().CLOUDINARY_URL_TTL_SECONDS + 5)
    assert (await client.get(_path_of(url))).status_code == 404


async def test_a_link_cannot_reach_outside_the_private_directory(client: AsyncClient) -> None:
    url = uploads.build_local_file_url("../../../../etc/passwd", download_name="x", inline=True)
    assert (await client.get(_path_of(url))).status_code == 404


async def test_garbage_is_a_plain_404(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/files/not-a-token")).status_code == 404


async def test_private_and_public_uploads_land_in_separate_directories(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    student = await user_factory(UserRole.STUDENT)
    uploaded = await client.post(
        "/api/v1/documents/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("passport.pdf", b"%PDF-1.4 passport", "application/pdf")},
        headers=await auth_headers(student),
    )
    assert uploaded.status_code == 200, uploaded.text
    stored = uploaded.json()["stored_file_name"]
    assert (uploads.local_dir(private=True) / stored).is_file()
    assert not (uploads.local_dir(private=False) / stored).exists()
