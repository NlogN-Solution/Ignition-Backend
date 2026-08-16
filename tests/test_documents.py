"""Document endpoints.

Documents are passport scans, transcripts and bank statements, so the tests
here centre on who can reach a file — not just its metadata row — plus the
review workflow and the folder aggregation.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

DOCUMENTS = "/api/v1/documents"


async def _upload(client: AsyncClient, headers: dict[str, str], student, **overrides) -> dict:
    data = {"student_id": str(student.id), "document_type": "passport"}
    data.update(overrides)
    response = await client.post(
        f"{DOCUMENTS}/upload",
        data=data,
        files={"file": ("passport.pdf", b"%PDF-1.4 fake passport scan", "application/pdf")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


# ── Upload validation ─────────────────────────────────────────────────────────


async def test_a_student_uploads_their_own_document(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    document = await _upload(client, await auth_headers(student), student)

    assert document["student_id"] == str(student.id)
    assert document["status"] == "pending"
    assert document["uploaded_by"] == str(student.id)
    # The stored name is generated, never the client's.
    assert document["stored_file_name"] != "passport.pdf"
    assert document["stored_file_name"].endswith(".pdf")


async def test_a_student_cannot_upload_against_another_student(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    victim = await user_factory(UserRole.STUDENT)

    document = await _upload(client, await auth_headers(student), victim)
    # student_id is overwritten, not trusted.
    assert document["student_id"] == str(student.id)


async def test_uploads_reject_a_disallowed_extension(client: AsyncClient, user_factory, auth_headers) -> None:
    """`/uploads` serves avatars publicly, and the stored extension decides how
    a browser treats a file, so it cannot come from the client unchecked."""
    student = await user_factory(UserRole.STUDENT)
    response = await client.post(
        f"{DOCUMENTS}/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("payload.html", b"<script>alert(1)</script>", "text/html")},
        headers=await auth_headers(student),
    )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


async def test_uploads_reject_an_empty_file(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    response = await client.post(
        f"{DOCUMENTS}/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("empty.pdf", b"", "application/pdf")},
        headers=await auth_headers(student),
    )
    assert response.status_code == 400


# ── File access ───────────────────────────────────────────────────────────────


async def test_the_owner_can_download_their_document(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(student)
    document = await _upload(client, headers, student)

    response = await client.get(f"{DOCUMENTS}/{document['id']}/download", headers=headers)
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 fake passport scan"


async def test_another_student_cannot_download_the_file(client: AsyncClient, user_factory, auth_headers) -> None:
    """The metadata row and the bytes must enforce the same rule. ED360 serves
    the bytes off a public static mount, where neither applies."""
    owner = await user_factory(UserRole.STUDENT)
    document = await _upload(client, await auth_headers(owner), owner)

    intruder = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(intruder)
    assert (await client.get(f"{DOCUMENTS}/{document['id']}/download", headers=headers)).status_code == 403
    assert (await client.get(f"{DOCUMENTS}/{document['id']}", headers=headers)).status_code == 403


async def test_downloading_requires_authentication(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    document = await _upload(client, await auth_headers(student), student)

    assert (await client.get(f"{DOCUMENTS}/{document['id']}/download")).status_code == 401


async def test_the_file_url_points_at_the_authenticated_route(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    document = await _upload(client, await auth_headers(student), student)

    assert document["file_url"] == f"/api/v1/documents/{document['id']}/download"
    assert "/uploads/" not in document["file_url"]


async def test_a_counsellor_can_download_a_students_document(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    document = await _upload(client, await auth_headers(student), student)

    counsellor = await user_factory(UserRole.COUNSELLOR)
    response = await client.get(
        f"{DOCUMENTS}/{document['id']}/download",
        headers=await auth_headers(counsellor),
    )
    assert response.status_code == 200


async def test_a_traversal_in_stored_file_name_cannot_escape_the_upload_dir(
    client: AsyncClient,
    user_factory,
    auth_headers,
    session,
) -> None:
    """`stored_file_name` reaches the filesystem, and `POST /documents` lets
    staff supply it, so the download path must refuse anything outside
    `upload_dir`."""
    from sqlalchemy import select

    from app.models import Document

    student = await user_factory(UserRole.STUDENT)
    document = await _upload(client, await auth_headers(student), student)

    row = await session.scalar(select(Document).where(Document.id == document["id"]))
    row.stored_file_name = "../../../../etc/passwd"
    await session.commit()

    response = await client.get(
        f"{DOCUMENTS}/{document['id']}/download",
        headers=await auth_headers(student),
    )
    assert response.status_code == 404


async def test_students_cannot_create_document_rows_directly(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    """`POST /documents` takes `stored_file_name` from the caller; a student who
    could use it would be able to point a row they own at another student's
    file and read it through the download route."""
    owner = await user_factory(UserRole.STUDENT)
    stolen = await _upload(client, await auth_headers(owner), owner)

    attacker = await user_factory(UserRole.STUDENT)
    response = await client.post(
        DOCUMENTS,
        json={
            "student_id": str(attacker.id),
            "document_type": "passport",
            "original_file_name": "mine.pdf",
            "stored_file_name": stolen["stored_file_name"],
            "file_url": "/whatever",
        },
        headers=await auth_headers(attacker),
    )
    assert response.status_code == 403


# ── Review workflow ───────────────────────────────────────────────────────────


async def test_verifying_stamps_the_reviewer_and_notifies_the_student(
    client: AsyncClient,
    user_factory,
    auth_headers,
    session,
) -> None:
    from sqlalchemy import select

    from app.models import Notification

    student = await user_factory(UserRole.STUDENT)
    document = await _upload(client, await auth_headers(student), student)

    counsellor = await user_factory(UserRole.COUNSELLOR)
    verified = await client.post(
        f"{DOCUMENTS}/{document['id']}/verify",
        json={"remarks": "Looks good"},
        headers=await auth_headers(counsellor),
    )
    assert verified.status_code == 200
    assert verified.json()["status"] == "approved"
    assert verified.json()["verified_by"] == str(counsellor.id)
    assert verified.json()["verified_at"] is not None

    notifications = (await session.scalars(select(Notification).where(Notification.user_id == student.id))).all()
    assert any(n.title == "Document approved" for n in notifications)


async def test_rejecting_records_the_reason(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    document = await _upload(client, await auth_headers(student), student)

    counsellor = await user_factory(UserRole.COUNSELLOR)
    rejected = await client.post(
        f"{DOCUMENTS}/{document['id']}/reject",
        json={"reason": "Expired passport"},
        headers=await auth_headers(counsellor),
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejection_reason"] == "Expired passport"


async def test_a_student_cannot_approve_their_own_document(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(student)
    document = await _upload(client, headers, student)

    assert (await client.post(f"{DOCUMENTS}/{document['id']}/verify", json={}, headers=headers)).status_code == 403


async def test_patch_cannot_forge_an_approval(client: AsyncClient, user_factory, auth_headers) -> None:
    """ED360's `DocumentUpdate` carries `status`, `verified_by` and
    `verified_at`, so an approval can be recorded against someone who never
    gave it."""
    student = await user_factory(UserRole.STUDENT)
    document = await _upload(client, await auth_headers(student), student)

    admin = await user_factory(UserRole.ADMIN)
    patched = await client.patch(
        f"{DOCUMENTS}/{document['id']}",
        json={"status": "approved", "verified_by": str(student.id), "title": "Renamed"},
        headers=await auth_headers(admin),
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"
    assert patched.json()["status"] == "pending"
    assert patched.json()["verified_by"] is None


# ── Folders ───────────────────────────────────────────────────────────────────


async def test_a_folder_aggregates_a_students_documents(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    student_headers = await auth_headers(student)
    first = await _upload(client, student_headers, student)
    await _upload(client, student_headers, student, document_type="academic_transcript")

    counsellor = await user_factory(UserRole.COUNSELLOR)
    staff_headers = await auth_headers(counsellor)
    await client.post(f"{DOCUMENTS}/{first['id']}/verify", json={}, headers=staff_headers)

    folder = await client.get(f"{DOCUMENTS}/folders/{student.id}", headers=staff_headers)
    assert folder.status_code == 200
    body = folder.json()
    assert body["document_count"] == 2
    assert body["approved_count"] == 1
    assert body["pending_count"] == 1
    assert body["total_size"] > 0

    listing = await client.get(f"{DOCUMENTS}/folders", headers=staff_headers)
    assert listing.json()["total"] == 1


async def test_students_cannot_browse_folders(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(student)
    await _upload(client, headers, student)

    assert (await client.get(f"{DOCUMENTS}/folders", headers=headers)).status_code == 403
    assert (await client.get(f"{DOCUMENTS}/folders/{student.id}", headers=headers)).status_code == 403


async def test_students_only_list_their_own_documents(client: AsyncClient, user_factory, auth_headers) -> None:
    mine = await user_factory(UserRole.STUDENT)
    theirs = await user_factory(UserRole.STUDENT)
    await _upload(client, await auth_headers(mine), mine)
    await _upload(client, await auth_headers(theirs), theirs)

    response = await client.get(
        DOCUMENTS,
        params={"student_id": str(theirs.id)},
        headers=await auth_headers(mine),
    )
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["student_id"] == str(mine.id)


async def test_extraction_reports_that_it_is_not_configured(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(student)
    document = await _upload(client, headers, student)

    response = await client.post(f"{DOCUMENTS}/{document['id']}/extract", headers=headers)
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["fields"] == {}
