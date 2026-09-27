"""Regression tests for the 2026-09 security remediation, beyond the audit's own.

`tests/test_security_audit_findings.py` holds the auditor's reproductions; this
module pins the rest of what the remediation changed — the parts of each fix
the audit's single test per finding did not reach, and the issues found while
fixing (secrets in the settings repr, catalogue drafts via the academic routes,
lead conversion onto staff accounts, spoofable client IPs).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.core.config import get_settings
from app.main import app as fastapi_app
from app.models.enums import UserRole
from tests.conftest import DEFAULT_PASSWORD

pytestmark = pytest.mark.asyncio

API = "/api/v1"
APPLICATIONS = f"{API}/applications"
LOGIN = f"{API}/auth/login"
REFRESH = f"{API}/auth/refresh"
ME = f"{API}/auth/me"


# ── Shared setup ──────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin(user_factory):
    return await user_factory(UserRole.ADMIN, email="hard.admin@example.com")


@pytest_asyncio.fixture
async def admin_headers(admin, auth_headers):
    return await auth_headers(admin)


@pytest_asyncio.fixture
async def catalogue(client: AsyncClient, admin_headers) -> dict:
    country = (await client.post(f"{API}/countries", json={"name": "UK", "iso2": "GB"}, headers=admin_headers)).json()
    university = (
        await client.post(f"{API}/universities", json={"country_id": country["id"], "name": "UCL"}, headers=admin_headers)
    ).json()
    program = (
        await client.post(f"{API}/programs", json={"university_id": university["id"], "name": "MSc CS"}, headers=admin_headers)
    ).json()
    other = (
        await client.post(f"{API}/programs", json={"university_id": university["id"], "name": "MSc AI"}, headers=admin_headers)
    ).json()
    return {"country": country, "university": university, "program": program, "other_program": other}


async def _application(client, admin_headers, program_id, student, counsellor=None) -> dict:
    body = {"student_id": str(student.id), "program_id": program_id}
    if counsellor is not None:
        body["counsellor_id"] = str(counsellor.id)
    created = await client.post(APPLICATIONS, json=body, headers=admin_headers)
    assert created.status_code == 200, created.text
    return created.json()


def _client_from(ip: str, session: AsyncSession) -> AsyncClient:
    async def _override():
        yield session

    fastapi_app.dependency_overrides[get_db_session] = _override
    return AsyncClient(transport=ASGITransport(app=fastapi_app, client=(ip, 1)), base_url="http://test")


# ── FAPI-SEC-001: configuration ───────────────────────────────────────────────


def test_simulated_payments_are_off_by_default() -> None:
    from app.core.config import Settings

    assert Settings(_env_file=None).SIMULATED_PAYMENTS is False


def test_production_refuses_to_boot_with_simulated_payments() -> None:
    from app.core.config import Settings

    with pytest.raises(ValueError, match="SIMULATED_PAYMENTS"):
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            SIMULATED_PAYMENTS=True,
            JWT_SECRET_KEY="x" * 48,
            CLOUDINARY_CLOUD_NAME="c",
            CLOUDINARY_API_KEY="k",
            CLOUDINARY_API_SECRET="s",
        )


def test_the_settings_repr_carries_no_secrets() -> None:
    """A traceback or debug log printing the settings object used to print
    DATABASE_URL with its password, and every other credential with it."""
    from app.core.config import Settings

    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://owner:hunter2-db@db.example.com/prod",
        JWT_SECRET_KEY="jwt-secret-value-for-the-test-xxxxxxx",
        CLOUDINARY_API_SECRET="cloud-secret-value",
        CLOUDINARY_API_KEY="cloud-key-value",
        LANDING_REVALIDATE_SECRET="landing-secret-value",
        REDIS_URL="redis://:redis-pass@redis.example.com:6379/0",
    )
    text = repr(settings) + str(settings)
    for secret in ("hunter2-db", "jwt-secret-value", "cloud-secret-value", "cloud-key-value", "landing-secret", "redis-pass"):
        assert secret not in text, secret


# ── FAPI-SEC-002 / -004: checklist and uploads, staff side ────────────────────


async def test_staff_cannot_link_one_students_document_to_anothers_checklist(
    client: AsyncClient, user_factory, auth_headers, admin_headers, catalogue
) -> None:
    alice = await user_factory(UserRole.STUDENT, email="hard.alice@example.com")
    bob = await user_factory(UserRole.STUDENT, email="hard.bob@example.com")
    app = await _application(client, admin_headers, catalogue["program"]["id"], alice)
    item = (
        await client.post(f"{APPLICATIONS}/{app['id']}/checklist", json={"document_type": "passport"}, headers=admin_headers)
    ).json()
    bobs = (
        await client.post(
            f"{API}/documents/upload",
            data={"student_id": str(bob.id), "document_type": "passport"},
            files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
            headers=admin_headers,
        )
    ).json()
    linked = await client.patch(
        f"{APPLICATIONS}/{app['id']}/checklist/{item['id']}", json={"document_id": bobs["id"]}, headers=admin_headers
    )
    assert linked.status_code == 404


async def test_a_matching_approved_document_still_verifies_the_item(
    client: AsyncClient, user_factory, auth_headers, admin_headers, catalogue
) -> None:
    """The fix must not break the legitimate path: an approved passport linked
    to the passport request is verified."""
    student = await user_factory(UserRole.STUDENT, email="hard.match@example.com")
    headers = await auth_headers(student)
    app = await _application(client, admin_headers, catalogue["program"]["id"], student)
    item = (
        await client.post(f"{APPLICATIONS}/{app['id']}/checklist", json={"document_type": "passport"}, headers=admin_headers)
    ).json()
    doc = (
        await client.post(
            f"{API}/documents/upload",
            data={"student_id": str(student.id), "document_type": "passport"},
            files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
            headers=headers,
        )
    ).json()
    await client.post(f"{API}/documents/{doc['id']}/verify", json={}, headers=admin_headers)
    linked = await client.patch(
        f"{APPLICATIONS}/{app['id']}/checklist/{item['id']}", json={"document_id": doc["id"]}, headers=headers
    )
    assert linked.status_code == 200
    assert linked.json()["status"] == "verified"


async def test_staff_upload_must_name_a_student(client: AsyncClient, user_factory, admin_headers) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR, email="hard.notastudent@example.com")
    response = await client.post(
        f"{API}/documents/upload",
        data={"student_id": str(counsellor.id), "document_type": "passport"},
        files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
        headers=admin_headers,
    )
    assert response.status_code == 404


async def test_staff_application_intake_must_belong_to_the_program(
    client: AsyncClient, user_factory, admin_headers, catalogue
) -> None:
    student = await user_factory(UserRole.STUDENT, email="hard.intake@example.com")
    intake = await client.post(
        f"{API}/intakes", json={"program_id": catalogue["other_program"]["id"], "name": "Sep 2027"}, headers=admin_headers
    )
    assert intake.status_code == 200, intake.text
    response = await client.post(
        APPLICATIONS,
        json={"student_id": str(student.id), "program_id": catalogue["program"]["id"], "intake_id": intake.json()["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 400


async def test_staff_application_student_must_be_a_student(
    client: AsyncClient, user_factory, admin_headers, catalogue
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR, email="hard.appstaff@example.com")
    response = await client.post(
        APPLICATIONS,
        json={"student_id": str(counsellor.id), "program_id": catalogue["program"]["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 404


# ── FAPI-SEC-003: step binding on write ───────────────────────────────────────


async def test_a_step_cannot_be_updated_through_another_application(
    client: AsyncClient, user_factory, admin_headers, catalogue
) -> None:
    template = (await client.post(f"{API}/workflow-templates", json={"name": "Std"}, headers=admin_headers)).json()
    await client.post(
        f"{API}/workflow-templates/{template['id']}/stages", json={"key": "docs", "name": "Docs"}, headers=admin_headers
    )
    one = await _application(client, admin_headers, catalogue["program"]["id"], await user_factory(UserRole.STUDENT))
    two = await _application(client, admin_headers, catalogue["program"]["id"], await user_factory(UserRole.STUDENT))
    workflow = (
        await client.post(f"{APPLICATIONS}/{two['id']}/workflow", json={"template_id": template["id"]}, headers=admin_headers)
    ).json()
    step_id = workflow["steps"][0]["id"]

    wrong = await client.patch(
        f"{APPLICATIONS}/{one['id']}/workflow/steps/{step_id}", json={"notes": "x"}, headers=admin_headers
    )
    assert wrong.status_code == 404
    comment = await client.post(
        f"{APPLICATIONS}/{one['id']}/workflow/steps/{step_id}/activities", json={"comment": "x"}, headers=admin_headers
    )
    assert comment.status_code == 404


# ── FAPI-SEC-005: /communication scoping ──────────────────────────────────────


@pytest_asyncio.fixture
async def two_counsellors(client, user_factory, auth_headers, admin_headers, catalogue):
    a = await user_factory(UserRole.COUNSELLOR, email="hard.ca@example.com")
    b = await user_factory(UserRole.COUNSELLOR, email="hard.cb@example.com")
    b_student = await user_factory(UserRole.STUDENT, email="hard.bstudent@example.com")
    free_student = await user_factory(UserRole.STUDENT, email="hard.free@example.com")
    b_app = await _application(client, admin_headers, catalogue["program"]["id"], b_student, counsellor=b)
    return {
        "a_headers": await auth_headers(a),
        "b_headers": await auth_headers(b),
        "b_student": b_student,
        "free_student": free_student,
        "b_app": b_app,
    }


async def test_a_counsellor_cannot_open_or_reply_to_a_colleagues_thread(
    client: AsyncClient, auth_headers, two_counsellors
) -> None:
    thread = (
        await client.post(
            f"{API}/student/me/threads",
            json={"subject": "help", "body": "private"},
            headers=await auth_headers(two_counsellors["b_student"]),
        )
    ).json()
    a = two_counsellors["a_headers"]
    assert (await client.get(f"{API}/communication/threads/{thread['id']}", headers=a)).status_code == 404
    reply = await client.post(f"{API}/communication/threads/{thread['id']}/messages", data={"body": "hi"}, headers=a)
    assert reply.status_code == 404
    listed = await client.get(f"{API}/communication/applications/{two_counsellors['b_app']['id']}/threads", headers=a)
    assert listed.json() == []

    # ...while the owning counsellor sees it.
    b = two_counsellors["b_headers"]
    assert (await client.get(f"{API}/communication/threads/{thread['id']}", headers=b)).status_code == 200


async def test_a_counsellor_cannot_start_a_thread_with_a_colleagues_student(client: AsyncClient, two_counsellors) -> None:
    response = await client.post(
        f"{API}/communication/threads",
        json={"subject": "x", "body": "x", "student_id": str(two_counsellors["b_student"].id)},
        headers=two_counsellors["a_headers"],
    )
    assert response.status_code == 404


async def test_unclaimed_students_stay_visible_to_every_counsellor(
    client: AsyncClient, auth_headers, two_counsellors
) -> None:
    """The unclaimed half of the rule is what stops a new student's first
    message going unseen."""
    await client.post(
        f"{API}/student/me/threads",
        json={"subject": "first contact", "body": "hello"},
        headers=await auth_headers(two_counsellors["free_student"]),
    )
    inbox = (await client.get(f"{API}/communication/threads", headers=two_counsellors["a_headers"])).json()
    assert [t["subject"] for t in inbox["items"]] == ["first contact"]


async def test_thread_ids_must_agree(client: AsyncClient, admin_headers, two_counsellors) -> None:
    """An application of student B cannot be attached to a thread about another student."""
    response = await client.post(
        f"{API}/communication/threads",
        json={
            "subject": "x",
            "body": "x",
            "student_id": str(two_counsellors["free_student"].id),
            "application_id": two_counsellors["b_app"]["id"],
        },
        headers=admin_headers,
    )
    assert response.status_code == 400


async def test_workflow_step_list_is_scoped_for_counsellors(client: AsyncClient, admin_headers, two_counsellors) -> None:
    template = (await client.post(f"{API}/workflow-templates", json={"name": "Std"}, headers=admin_headers)).json()
    await client.post(
        f"{API}/workflow-templates/{template['id']}/stages", json={"key": "docs", "name": "Docs"}, headers=admin_headers
    )
    await client.post(
        f"{APPLICATIONS}/{two_counsellors['b_app']['id']}/workflow",
        json={"template_id": template["id"]},
        headers=admin_headers,
    )
    assert (await client.get(f"{API}/workflow-steps", headers=two_counsellors["a_headers"])).json()["total"] == 0
    assert (await client.get(f"{API}/workflow-steps", headers=two_counsellors["b_headers"])).json()["total"] == 1


# ── FAPI-SEC-014: people ──────────────────────────────────────────────────────


async def test_a_counsellor_cannot_read_a_colleagues_students_pii(client: AsyncClient, two_counsellors) -> None:
    a = two_counsellors["a_headers"]
    student_id = two_counsellors["b_student"].id
    assert (await client.get(f"{API}/users/{student_id}/student-profile", headers=a)).status_code == 404
    assert (await client.get(f"{API}/users/{student_id}", headers=a)).status_code == 404
    assert (await client.get(f"{API}/documents/folders/{student_id}", headers=a)).status_code == 404
    listed = (await client.get(f"{API}/users", headers=a)).json()
    assert str(student_id) not in {u["id"] for u in listed["items"]}
    # The unclaimed student is still in reach.
    assert str(two_counsellors["free_student"].id) in {u["id"] for u in listed["items"]}


async def test_a_counsellor_cannot_open_a_colleagues_students_document(
    client: AsyncClient, admin_headers, two_counsellors
) -> None:
    doc = (
        await client.post(
            f"{API}/documents/upload",
            data={"student_id": str(two_counsellors["b_student"].id), "document_type": "passport"},
            files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")},
            headers=admin_headers,
        )
    ).json()
    a = two_counsellors["a_headers"]
    assert (await client.get(f"{API}/documents/{doc['id']}", headers=a)).status_code == 404
    assert (await client.get(f"{API}/documents/{doc['id']}/link", headers=a)).status_code == 404
    listed = (await client.get(f"{API}/documents", headers=a)).json()
    assert doc["id"] not in {d["id"] for d in listed["items"]}
    b = two_counsellors["b_headers"]
    assert (await client.get(f"{API}/documents/{doc['id']}", headers=b)).status_code == 200


# ── FAPI-SEC-006 / -015: leave ────────────────────────────────────────────────


@pytest_asyncio.fixture
async def leave_setup(client, user_factory, auth_headers, admin_headers):
    staff = await user_factory(UserRole.STAFF, email="hard.leave.staff@example.com")
    colleague = await user_factory(UserRole.STAFF, email="hard.leave.colleague@example.com")
    manager = await user_factory(UserRole.MANAGER, email="hard.leave.manager@example.com")
    leave_type = (await client.post(f"{API}/leave-types", json={"name": "Sick"}, headers=admin_headers)).json()
    return {
        "staff": await auth_headers(staff),
        "colleague": await auth_headers(colleague),
        "manager": await auth_headers(manager),
        "leave_type": leave_type,
    }


async def _leave(client, headers, leave_type_id, with_file=True):
    return await client.post(
        f"{API}/leave-requests",
        data={"leave_type_id": leave_type_id, "start_date": "2026-10-05", "end_date": "2026-10-06"},
        files={"file": ("note.pdf", b"%PDF-1.4 medical", "application/pdf")} if with_file else None,
        headers=headers,
    )


async def test_leave_attachment_is_owner_or_manager_only(client: AsyncClient, leave_setup) -> None:
    created = (await _leave(client, leave_setup["staff"], leave_setup["leave_type"]["id"])).json()
    assert created["has_attachment"] is True
    link = f"{API}/leave-requests/{created['id']}/attachment"
    assert (await client.get(link, headers=leave_setup["staff"])).status_code == 200
    assert (await client.get(link, headers=leave_setup["manager"])).status_code == 200
    assert (await client.get(link, headers=leave_setup["colleague"])).status_code == 403
    assert (await client.get(f"{link}/link", headers=leave_setup["colleague"])).status_code == 403


async def test_a_manager_cannot_approve_their_own_leave(client: AsyncClient, leave_setup) -> None:
    own = (await _leave(client, leave_setup["manager"], leave_setup["leave_type"]["id"], with_file=False)).json()
    response = await client.post(f"{API}/leave-requests/{own['id']}/approve", json={}, headers=leave_setup["manager"])
    assert response.status_code == 403


async def test_a_decided_leave_request_cannot_be_decided_again(client: AsyncClient, leave_setup) -> None:
    request = (await _leave(client, leave_setup["staff"], leave_setup["leave_type"]["id"], with_file=False)).json()
    url = f"{API}/leave-requests/{request['id']}"
    assert (await client.post(f"{url}/approve", json={}, headers=leave_setup["manager"])).status_code == 200
    assert (await client.post(f"{url}/reject", json={"reason": "late"}, headers=leave_setup["manager"])).status_code == 409


# ── FAPI-SEC-007: every list route is bounded ─────────────────────────────────


def test_every_paginated_route_bounds_page_and_limit() -> None:
    """The build-time guard the remediation plan asked for: a new list route
    declaring a bare `page: int` or `limit: int` fails here."""
    from tests.test_endpoint_authorization import _iter_api_routes

    unbounded = []
    for route in _iter_api_routes(fastapi_app.routes):
        for param in route.dependant.query_params:
            if param.name not in ("page", "limit"):
                continue
            metadata = getattr(param.field_info, "metadata", [])
            has_upper = any(getattr(m, "le", None) is not None or getattr(m, "lt", None) is not None for m in metadata)
            has_lower = any(getattr(m, "ge", None) is not None or getattr(m, "gt", None) is not None for m in metadata)
            if not (has_upper and has_lower):
                unbounded.append(f"{sorted(route.methods)} {route.path} ({param.name})")
    assert not unbounded, "unbounded pagination: " + ", ".join(unbounded)


async def test_the_consoles_largest_page_size_still_works(client: AsyncClient, admin_headers) -> None:
    """The dashboard asks for limit=200; the ceiling must not break it."""
    assert (await client.get(f"{API}/leads?limit=200", headers=admin_headers)).status_code == 200
    assert (await client.get(f"{API}/leads?limit=201", headers=admin_headers)).status_code == 422


# ── FAPI-SEC-008 (academic): unpublished catalogue ───────────────────────────


async def test_students_only_see_published_programs(
    client: AsyncClient, user_factory, auth_headers, catalogue
) -> None:
    student = await user_factory(UserRole.STUDENT, email="hard.catalog@example.com")
    headers = await auth_headers(student)
    listed = (await client.get(f"{API}/programs", headers=headers)).json()
    assert listed["total"] == 0  # nothing in the fixture catalogue is published
    assert (await client.get(f"{API}/programs/{catalogue['program']['id']}", headers=headers)).status_code == 404
    assert (await client.get(f"{API}/universities", headers=headers)).json()["total"] == 0


async def test_students_cannot_read_workflow_templates(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT, email="hard.templates@example.com")
    assert (await client.get(f"{API}/workflow-templates", headers=await auth_headers(student))).status_code == 403


# ── FAPI-SEC-009: e-mail ──────────────────────────────────────────────────────


async def test_login_is_case_insensitive(client: AsyncClient, user_factory) -> None:
    user = await user_factory(UserRole.STUDENT, email="hard.case@example.com")
    response = await client.post(LOGIN, json={"email": "Hard.Case@Example.com", "password": DEFAULT_PASSWORD})
    assert response.status_code == 200, user.email


async def test_changing_email_needs_the_current_password(client: AsyncClient, user_factory, auth_headers) -> None:
    user = await user_factory(UserRole.STUDENT, email="hard.move@example.com")
    headers = await auth_headers(user)
    refused = await client.patch(f"{API}/users/me", json={"email": "elsewhere@example.com"}, headers=headers)
    assert refused.status_code == 400
    # Re-sending the unchanged address (what the console's profile form does)
    # needs no password.
    same = await client.patch(f"{API}/users/me", json={"email": "HARD.MOVE@example.com", "first_name": "N"}, headers=headers)
    assert same.status_code == 200
    moved = await client.patch(
        f"{API}/users/me",
        json={"email": "Elsewhere@Example.com", "current_password": DEFAULT_PASSWORD},
        headers=headers,
    )
    assert moved.status_code == 200
    assert moved.json()["email"] == "elsewhere@example.com"


async def test_password_confirmation_cannot_be_brute_forced(client: AsyncClient, user_factory, auth_headers) -> None:
    """A stolen token must not become the password by guessing at the
    confirm-your-password prompts."""
    settings = get_settings()
    user = await user_factory(UserRole.STUDENT, email="hard.oracle@example.com")
    headers = await auth_headers(user)
    for attempt in range(settings.MAX_FAILED_LOGIN_ATTEMPTS):
        await client.patch(
            f"{API}/users/me", json={"email": "x@example.com", "current_password": f"guess-{attempt}"}, headers=headers
        )
    right = await client.patch(
        f"{API}/users/me", json={"email": "x@example.com", "current_password": DEFAULT_PASSWORD}, headers=headers
    )
    assert right.status_code == 400


# ── FAPI-SEC-010: client address ──────────────────────────────────────────────


def _request(peer: str, headers: dict[str, str]):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 1)})


def test_client_ip_ignores_forwarded_headers_without_trusted_hops(monkeypatch) -> None:
    from app.core.client_ip import client_ip

    monkeypatch.setattr(get_settings(), "TRUSTED_PROXY_HOPS", 0)
    assert client_ip(_request("10.0.0.5", {"X-Forwarded-For": "1.2.3.4"})) == "10.0.0.5"


def test_client_ip_takes_the_entry_the_proxy_appended(monkeypatch) -> None:
    """With one trusted proxy the client is the LAST entry; a value the client
    put at the front of the header must never be believed."""
    from app.core.client_ip import client_ip

    monkeypatch.setattr(get_settings(), "TRUSTED_PROXY_HOPS", 1)
    spoofed = _request("10.0.0.5", {"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})
    assert client_ip(spoofed) == "203.0.113.7"


def test_client_ip_falls_back_on_garbage(monkeypatch) -> None:
    from app.core.client_ip import client_ip

    monkeypatch.setattr(get_settings(), "TRUSTED_PROXY_HOPS", 1)
    assert client_ip(_request("10.0.0.5", {"X-Forwarded-For": "not-an-ip"})) == "10.0.0.5"
    assert client_ip(_request("10.0.0.5", {"X-Forwarded-For": "203.0.113.7:4431"})) == "203.0.113.7"


# ── FAPI-SEC-012: body size ───────────────────────────────────────────────────


async def test_an_oversized_declared_body_is_refused_before_reading(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT, email="hard.big@example.com")
    headers = {**(await auth_headers(student)), "Content-Length": str(10 * 1024 * 1024 * 1024)}
    response = await client.post(f"{API}/documents/upload", content=b"", headers=headers)
    assert response.status_code == 413


async def test_an_oversized_streamed_body_is_cut_off(
    client: AsyncClient, user_factory, auth_headers, monkeypatch
) -> None:
    """No Content-Length (chunked): counted as it arrives."""
    from app.core import middleware

    student = await user_factory(UserRole.STUDENT, email="hard.stream@example.com")
    headers = await auth_headers(student)
    limit = get_settings().MAX_REQUEST_BODY_MB * 1024 * 1024

    async def body():
        # A well-formed file part that simply never ends — so the multipart
        # parser keeps reading and only the streaming cap can stop it.
        yield (
            b"--hardening\r\n"
            b'Content-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
            b"Content-Type: application/pdf\r\n\r\n"
        )
        chunk = b"x" * (1024 * 1024)
        for _ in range(limit // len(chunk) + 2):
            yield chunk

    headers["Content-Type"] = "multipart/form-data; boundary=hardening"
    response = await client.post(f"{API}/documents/upload", content=body(), headers=headers)
    assert response.status_code == 413, response.text
    assert middleware is not None


async def test_a_single_file_over_the_per_file_limit_is_refused(
    client: AsyncClient, user_factory, auth_headers, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "MAX_UPLOAD_SIZE_MB", 1)
    student = await user_factory(UserRole.STUDENT, email="hard.onefile@example.com")
    response = await client.post(
        f"{API}/documents/upload",
        data={"student_id": str(student.id), "document_type": "passport"},
        files={"file": ("p.pdf", b"x" * (1024 * 1024 + 1), "application/pdf")},
        headers=await auth_headers(student),
    )
    assert response.status_code == 400


# ── FAPI-SEC-013: expiring file links ─────────────────────────────────────────


def test_private_links_carry_an_expiring_token_when_configured(monkeypatch) -> None:
    import cloudinary

    from app.core import uploads

    settings = get_settings()
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "CLOUDINARY_AUTH_TOKEN_KEY", "00112233445566778899aabbccddeeff")
    cloudinary.config(cloud_name="demo", api_key="k", api_secret="s")
    url = uploads.build_download_url("abc.pdf", folder=uploads.DOCUMENT_FOLDER, download_name="a.pdf")
    assert url is not None and "__cld_token__=exp=" in url


# ── FAPI-SEC-016: sessions ────────────────────────────────────────────────────


async def test_logout_ends_the_access_token_immediately(client: AsyncClient, user_factory) -> None:
    user = await user_factory(UserRole.STUDENT, email="hard.logout@example.com")
    tokens = (await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    assert (await client.get(ME, headers=headers)).status_code == 200
    assert (await client.post(f"{API}/auth/logout", headers=headers)).status_code == 200
    assert (await client.get(ME, headers=headers)).status_code == 401


async def test_reusing_a_rotated_refresh_token_revokes_every_session(
    client: AsyncClient, user_factory, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "REFRESH_REUSE_GRACE_SECONDS", 0)
    user = await user_factory(UserRole.STUDENT, email="hard.reuse@example.com")
    stolen = (await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})).json()
    rotated = (await client.post(REFRESH, json={"refresh_token": stolen["refresh_token"]})).json()

    # The old token comes back: treat it as theft.
    assert (await client.post(REFRESH, json={"refresh_token": stolen["refresh_token"]})).status_code == 401
    assert (await client.post(REFRESH, json={"refresh_token": rotated["refresh_token"]})).status_code == 401
    assert (
        await client.get(ME, headers={"Authorization": f"Bearer {rotated['access_token']}"})
    ).status_code == 401


async def test_a_racing_refresh_inside_the_grace_window_is_harmless(client: AsyncClient, user_factory) -> None:
    user = await user_factory(UserRole.STUDENT, email="hard.race@example.com")
    first = (await client.post(LOGIN, json={"email": user.email, "password": DEFAULT_PASSWORD})).json()
    winner = (await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})).json()
    assert (await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})).status_code == 401
    # Two tabs racing must not log the user out everywhere.
    assert (await client.post(REFRESH, json={"refresh_token": winner["refresh_token"]})).status_code == 200


async def test_a_token_without_a_session_id_is_refused(client: AsyncClient, user_factory) -> None:
    from datetime import UTC, datetime, timedelta

    import jwt

    user = await user_factory(UserRole.ADMIN, email="hard.nosid@example.com")
    settings = get_settings()
    token = jwt.encode(
        {"sub": str(user.id), "type": "access", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    assert (await client.get(ME, headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_logout_ignores_someone_elses_refresh_token(client: AsyncClient, user_factory, auth_headers) -> None:
    victim = await user_factory(UserRole.STUDENT, email="hard.victim@example.com")
    victim_tokens = (await client.post(LOGIN, json={"email": victim.email, "password": DEFAULT_PASSWORD})).json()
    attacker = await user_factory(UserRole.STUDENT, email="hard.attacker@example.com")
    await client.post(
        f"{API}/auth/logout", json={"refresh_token": victim_tokens["refresh_token"]}, headers=await auth_headers(attacker)
    )
    assert (await client.post(REFRESH, json={"refresh_token": victim_tokens["refresh_token"]})).status_code == 200


# ── FAPI-SEC-018 and lead conversion ──────────────────────────────────────────


async def test_an_anonymous_submission_does_not_rewrite_an_existing_lead(
    client: AsyncClient, session: AsyncSession, admin_headers
) -> None:
    from app.models import Lead
    from tests.test_eligibility import SUBMIT, _submission

    lead = (
        await client.post(
            f"{API}/leads", json={"first_name": "Real", "last_name": "Person", "phone": "+9779800000000"}, headers=admin_headers
        )
    ).json()
    response = await client.post(SUBMIT, json=_submission(contact={"email": "impostor@example.com"}))
    assert response.status_code == 200, response.text
    row = await session.scalar(select(Lead).where(Lead.id == lead["id"]))
    await session.refresh(row)
    assert row.email is None


async def test_a_lead_cannot_be_converted_onto_a_staff_account(
    client: AsyncClient, user_factory, admin_headers
) -> None:
    staff = await user_factory(UserRole.COUNSELLOR, email="hard.staffmail@example.com")
    lead = (
        await client.post(
            f"{API}/leads",
            json={"first_name": "X", "last_name": "Y", "phone": "9811111111", "email": staff.email},
            headers=admin_headers,
        )
    ).json()
    converted = await client.post(f"{API}/leads/{lead['id']}/convert", json={}, headers=admin_headers)
    assert converted.status_code == 400
    explicit = await client.post(
        f"{API}/leads/{lead['id']}/convert", json={"converted_user_id": str(staff.id)}, headers=admin_headers
    )
    assert explicit.status_code == 400


# ── Transport hygiene ─────────────────────────────────────────────────────────


async def test_security_headers_and_request_id_hygiene(client: AsyncClient) -> None:
    response = await client.get(f"{API}/health", headers={"X-Request-ID": "x" * 5000})
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert len(response.headers["X-Request-ID"]) <= 128

    kept = await client.get(f"{API}/health", headers={"X-Request-ID": "trace-abc_123"})
    assert kept.headers["X-Request-ID"] == "trace-abc_123"


async def test_owner_can_check_client_ip_resolution(client: AsyncClient, user_factory, auth_headers) -> None:
    owner = await user_factory(UserRole.SUPER_ADMIN, email="hard.owner@example.com")
    admin = await user_factory(UserRole.ADMIN, email="hard.notowner@example.com")
    assert (await client.get(f"{API}/health/client-ip", headers=await auth_headers(owner))).status_code == 200
    assert (await client.get(f"{API}/health/client-ip", headers=await auth_headers(admin))).status_code == 403


# ── Temporary passwords (found during remediation) ────────────────────────────


async def test_staff_with_a_temporary_password_can_only_change_it(client: AsyncClient, user_factory) -> None:
    """`must_change_password` was enforced only by the admin console's router,
    so the published seed password (scripts/SEED_NEON.md) or an admin-issued
    temporary one gave full API access to a direct caller."""
    staff = await user_factory(UserRole.ADMIN, email="hard.temp@example.com", must_change_password=True)
    tokens = (await client.post(LOGIN, json={"email": staff.email, "password": DEFAULT_PASSWORD})).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert (await client.get(f"{API}/users", headers=headers)).status_code == 403
    assert (await client.get(ME, headers=headers)).status_code == 200
    changed = await client.post(
        f"{API}/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "a-brand-new-password"},
        headers=headers,
    )
    assert changed.status_code == 200
    assert (await client.get(f"{API}/users", headers=headers)).status_code == 200
