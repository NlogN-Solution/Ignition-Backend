"""Regression tests for the confirmed findings of the 2026-09-25 security audit.

Moved here from `security-audit/tests/security/` once every finding was fixed,
so the main suite keeps them. Each test asserts the SECURE behaviour; against
the audited commit (1e58579) 16 of them failed, which was the reproduction.
The `test_control_*` cases are positive controls that passed before and must
keep passing. Further regression tests for the remediation live in
`tests/test_security_hardening.py`.

Synthetic users and objects only; everything runs inside the project's
rolled-back `ignition_test` transaction (see tests/conftest.py).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db_session
from app.main import app as fastapi_app
from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

API = "/api/v1"
APPLICATIONS = f"{API}/applications"


# ── Shared setup ──────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin(user_factory):
    return await user_factory(UserRole.ADMIN, email="sec.admin@example.com")


@pytest_asyncio.fixture
async def admin_headers(admin, auth_headers):
    return await auth_headers(admin)


@pytest_asyncio.fixture
async def program_id(client: AsyncClient, admin_headers) -> str:
    country = await client.post(f"{API}/countries", json={"name": "UK", "iso2": "GB"}, headers=admin_headers)
    university = await client.post(
        f"{API}/universities", json={"country_id": country.json()["id"], "name": "UCL"}, headers=admin_headers
    )
    program = await client.post(
        f"{API}/programs", json={"university_id": university.json()["id"], "name": "MSc CS"}, headers=admin_headers
    )
    assert program.status_code == 200, program.text
    return program.json()["id"]


@pytest_asyncio.fixture
async def template_id(client: AsyncClient, admin_headers) -> str:
    created = await client.post(f"{API}/workflow-templates", json={"name": "Std"}, headers=admin_headers)
    tid = created.json()["id"]
    await client.post(f"{API}/workflow-templates/{tid}/stages", json={"key": "docs", "name": "Docs"}, headers=admin_headers)
    return tid


async def _application(client, admin_headers, program_id, student, counsellor=None) -> str:
    body = {"student_id": str(student.id), "program_id": program_id}
    if counsellor is not None:
        body["counsellor_id"] = str(counsellor.id)
    created = await client.post(APPLICATIONS, json=body, headers=admin_headers)
    assert created.status_code == 200, created.text
    return created.json()["id"]


async def _upload(client, headers, student, document_type="passport", **extra):
    return await client.post(
        f"{API}/documents/upload",
        data={"student_id": str(student.id), "document_type": document_type, **extra},
        files={"file": ("scan.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
        headers=headers,
    )


@pytest_asyncio.fixture
async def lenient_client(session):
    """Client that turns unhandled exceptions into the 500 a real client sees."""

    async def _override():
        yield session

    fastapi_app.dependency_overrides[get_db_session] = _override
    transport = ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    fastapi_app.dependency_overrides.clear()


# ── FAPI-SEC-001: SIMULATED_PAYMENTS fails open in production ────────────────


def test_fapi_sec_001_production_refuses_simulated_payments(monkeypatch) -> None:
    """A production config that does not explicitly disable simulated payments
    must not boot — otherwise POST /student/me/access/checkout grants paid
    portal access for free."""
    from app.core.config import Settings

    for key in ("SIMULATED_PAYMENTS",):
        monkeypatch.delenv(key, raising=False)
    try:
        settings = Settings(
            _env_file=None,
            ENVIRONMENT="production",
            JWT_SECRET_KEY="x" * 48,
            CLOUDINARY_CLOUD_NAME="c",
            CLOUDINARY_API_KEY="k",
            CLOUDINARY_API_SECRET="s",
        )
    except ValueError:
        return  # secure: refused to boot
    assert settings.SIMULATED_PAYMENTS is False, "production boots with SIMULATED_PAYMENTS=True"


async def test_fapi_sec_001_checkout_unlocks_gated_documents_without_payment(
    client: AsyncClient, user_factory, auth_headers, admin_headers, access_fee
) -> None:
    student = await user_factory(UserRole.STUDENT, email="sec.pay@example.com")
    headers = await auth_headers(student)
    checkout = await client.post(f"{API}/student/me/access/checkout", json={"payment_method": "esewa"}, headers=headers)
    # Secure behaviour in production: no gateway => refuse.
    assert checkout.status_code != 200, "checkout granted portal access with no payment"


# ── FAPI-SEC-002: checklist item accepts any document_id ─────────────────────


async def test_fapi_sec_002_student_cannot_link_another_students_document(
    client: AsyncClient, user_factory, auth_headers, admin_headers, program_id
) -> None:
    attacker = await user_factory(UserRole.STUDENT, email="sec.attacker@example.com")
    victim = await user_factory(UserRole.STUDENT, email="sec.victim@example.com")
    attacker_headers = await auth_headers(attacker)
    victim_headers = await auth_headers(victim)

    app_id = await _application(client, admin_headers, program_id, attacker)
    item = (
        await client.post(f"{APPLICATIONS}/{app_id}/checklist", json={"document_type": "passport"}, headers=admin_headers)
    ).json()

    victim_doc = (await _upload(client, victim_headers, victim)).json()
    await client.post(f"{API}/documents/{victim_doc['id']}/verify", json={}, headers=admin_headers)

    linked = await client.patch(
        f"{APPLICATIONS}/{app_id}/checklist/{item['id']}",
        json={"document_id": victim_doc["id"]},
        headers=attacker_headers,
    )
    filed = await client.get(f"{API}/student/me/applications/{app_id}/documents", headers=attacker_headers)
    leaked = [d for d in filed.json().get("items", []) if d["id"] == victim_doc["id"]]

    assert linked.status_code in (400, 403, 404), (
        f"foreign document linked (status={linked.status_code}, item status={linked.json().get('status')})"
    )
    assert not leaked, "victim's document metadata now listed on the attacker's application"


async def test_fapi_sec_002_approved_document_of_other_type_does_not_self_verify(
    client: AsyncClient, user_factory, auth_headers, admin_headers, program_id
) -> None:
    """An approved 'photo' linked to a 'passport' request must not land VERIFIED."""
    student = await user_factory(UserRole.STUDENT, email="sec.selfverify@example.com")
    headers = await auth_headers(student)
    app_id = await _application(client, admin_headers, program_id, student)
    item = (
        await client.post(f"{APPLICATIONS}/{app_id}/checklist", json={"document_type": "passport"}, headers=admin_headers)
    ).json()
    photo = (await _upload(client, headers, student, document_type="photo")).json()
    await client.post(f"{API}/documents/{photo['id']}/verify", json={}, headers=admin_headers)

    linked = await client.patch(
        f"{APPLICATIONS}/{app_id}/checklist/{item['id']}", json={"document_id": photo["id"]}, headers=headers
    )
    assert linked.json().get("status") != "verified", "student self-verified a passport request with a photo"


# ── FAPI-SEC-003: workflow step id not bound to the application in the path ──


async def test_fapi_sec_003_student_cannot_read_other_applications_step_activities(
    client: AsyncClient, user_factory, auth_headers, admin_headers, program_id, template_id
) -> None:
    attacker = await user_factory(UserRole.STUDENT, email="sec.wf.attacker@example.com")
    victim = await user_factory(UserRole.STUDENT, email="sec.wf.victim@example.com")
    own_app = await _application(client, admin_headers, program_id, attacker)
    victim_app = await _application(client, admin_headers, program_id, victim)

    workflow = (
        await client.post(f"{APPLICATIONS}/{victim_app}/workflow", json={"template_id": template_id}, headers=admin_headers)
    ).json()
    victim_step = workflow["steps"][0]["id"]
    await client.post(
        f"{APPLICATIONS}/{victim_app}/workflow/steps/{victim_step}/activities",
        json={"comment": "SYNTHETIC-PRIVATE-NOTE"},
        headers=admin_headers,
    )

    response = await client.get(
        f"{APPLICATIONS}/{own_app}/workflow/steps/{victim_step}/activities",
        headers=await auth_headers(attacker),
    )
    assert response.status_code == 404 or response.json() == [], (
        "another application's workflow step activities were returned: "
        + str([a.get("comment") for a in response.json()])
    )


# ── FAPI-SEC-004: student-supplied application_id is not ownership-checked ────


async def test_fapi_sec_004_upload_cannot_attach_to_foreign_application(
    client: AsyncClient, user_factory, auth_headers, admin_headers, program_id
) -> None:
    attacker = await user_factory(UserRole.STUDENT, email="sec.up.attacker@example.com")
    victim = await user_factory(UserRole.STUDENT, email="sec.up.victim@example.com")
    victim_app = await _application(client, admin_headers, program_id, victim)

    uploaded = await _upload(client, await auth_headers(attacker), attacker, application_id=victim_app)
    victim_view = await client.get(
        f"{API}/student/me/applications/{victim_app}/documents", headers=await auth_headers(victim)
    )
    planted = [d for d in victim_view.json()["items"] if d["student_id"] == str(attacker.id)]

    assert uploaded.status_code in (400, 403, 404), f"upload accepted (status={uploaded.status_code})"
    assert not planted, "attacker's file now appears on the victim's application"


async def test_fapi_sec_004_student_thread_cannot_attach_to_foreign_application(
    client: AsyncClient, user_factory, auth_headers, admin_headers, program_id
) -> None:
    attacker = await user_factory(UserRole.STUDENT, email="sec.th.attacker@example.com")
    victim = await user_factory(UserRole.STUDENT, email="sec.th.victim@example.com")
    victim_app = await _application(client, admin_headers, program_id, victim)

    created = await client.post(
        f"{API}/student/me/threads",
        json={"subject": "hi", "body": "synthetic", "application_id": victim_app},
        headers=await auth_headers(attacker),
    )
    assert created.status_code in (400, 403, 404), f"thread filed on foreign application (status={created.status_code})"


# ── FAPI-SEC-005: counsellor own-work scoping bypassed on newer routers ──────


@pytest_asyncio.fixture
async def scoped_setup(client, user_factory, auth_headers, admin_headers, program_id):
    """Counsellor A owns nothing; student S's application belongs to counsellor B."""
    counsellor_a = await user_factory(UserRole.COUNSELLOR, email="sec.c.a@example.com")
    counsellor_b = await user_factory(UserRole.COUNSELLOR, email="sec.c.b@example.com")
    student = await user_factory(UserRole.STUDENT, email="sec.c.student@example.com")
    app_id = await _application(client, admin_headers, program_id, student, counsellor=counsellor_b)
    a_headers = await auth_headers(counsellor_a)
    return {"student": student, "app_id": app_id, "a_headers": a_headers, "admin_headers": admin_headers}


async def test_fapi_sec_005_control_application_is_hidden_from_other_counsellor(client, scoped_setup) -> None:
    """Positive control: the intended rule IS enforced on /applications/{id}."""
    r = await client.get(f"{APPLICATIONS}/{scoped_setup['app_id']}", headers=scoped_setup["a_headers"])
    assert r.status_code == 404


async def test_fapi_sec_005_checklist_of_other_counsellors_application(client, scoped_setup) -> None:
    r = await client.get(f"{APPLICATIONS}/{scoped_setup['app_id']}/checklist", headers=scoped_setup["a_headers"])
    assert r.status_code == 404, f"checklist of another counsellor's application readable (status={r.status_code})"


async def test_fapi_sec_005_other_counsellors_student_correspondence(client, scoped_setup, auth_headers) -> None:
    student = scoped_setup["student"]
    await client.post(
        f"{API}/student/me/threads",
        json={"subject": "private", "body": "SYNTHETIC-PRIVATE-MESSAGE"},
        headers=await auth_headers(student),
    )
    await client.post(
        f"{API}/communication/threads",
        json={"subject": "note", "body": "SYNTHETIC-INTERNAL-NOTE", "student_id": str(student.id), "visibility": "internal"},
        headers=scoped_setup["admin_headers"],
    )
    by_student = await client.get(f"{API}/communication/students/{student.id}/threads", headers=scoped_setup["a_headers"])
    inbox = await client.get(f"{API}/communication/threads", headers=scoped_setup["a_headers"])

    visible = by_student.json() if by_student.status_code == 200 else []
    inbox_items = inbox.json().get("items", []) if inbox.status_code == 200 else []
    assert not visible and not inbox_items, (
        f"counsellor A reads {len(visible)} thread(s) of counsellor B's student "
        f"(incl. internal: {any(t.get('visibility') == 'internal' for t in visible)}); inbox shows {len(inbox_items)}"
    )


# ── FAPI-SEC-006: leave attachments uploaded as public assets ─────────────────


async def test_fapi_sec_006_leave_attachment_is_private(client, user_factory, auth_headers, admin_headers) -> None:
    staff = await user_factory(UserRole.STAFF, email="sec.leave@example.com")
    leave_type = await client.post(f"{API}/leave-types", json={"name": "Sick"}, headers=admin_headers)
    assert leave_type.status_code == 200, leave_type.text
    created = await client.post(
        f"{API}/leave-requests",
        data={"leave_type_id": leave_type.json()["id"], "start_date": "2026-10-01", "end_date": "2026-10-02"},
        files={"file": ("medical-note.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
        headers=await auth_headers(staff),
    )
    assert created.status_code == 200, created.text
    # store_upload returns a URL only for private=False uploads (a public
    # Cloudinary `upload` asset in production).
    assert created.json()["attachment_url"] is None, (
        f"leave attachment stored as a public asset: {created.json()['attachment_url']}"
    )


# ── FAPI-SEC-007: unbounded / unvalidated pagination ─────────────────────────


async def test_fapi_sec_007_limit_is_clamped(client, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT, email="sec.page@example.com")
    r = await client.get(f"{API}/student/me/documents?limit=1000000", headers=await auth_headers(student))
    assert r.status_code == 422, f"limit=1000000 accepted (status={r.status_code})"


async def test_fapi_sec_007_page_zero_is_a_client_error(lenient_client, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT, email="sec.page0@example.com")
    login = await lenient_client.post(
        f"{API}/auth/login", json={"email": student.email, "password": "correct-horse-battery"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = await lenient_client.get(f"{API}/student/me/documents?page=0", headers=headers)
    assert r.status_code == 422, f"page=0 -> {r.status_code} (negative OFFSET reaches Postgres)"


# ── FAPI-SEC-008: unpublished CMS/catalogue drafts readable by any account ────


async def test_fapi_sec_008_student_cannot_read_unpublished_content(client, user_factory, auth_headers, admin_headers) -> None:
    draft = await client.post(
        f"{API}/content-pages",
        json={"key": "sec-draft", "kind": "page", "title": "SYNTHETIC UNPUBLISHED DRAFT", "is_published": False},
        headers=admin_headers,
    )
    assert draft.status_code == 200, draft.text
    student = await user_factory(UserRole.STUDENT, email="sec.cms@example.com")
    r = await client.get(f"{API}/content-pages/{draft.json()['id']}", headers=await auth_headers(student))
    assert r.status_code in (403, 404), f"self-registered student read an unpublished draft (status={r.status_code})"


# ── FAPI-SEC-009: account e-mail handling ─────────────────────────────────────


async def test_fapi_sec_009_self_email_change_to_taken_address_is_a_409(lenient_client, user_factory) -> None:
    await user_factory(UserRole.STUDENT, email="sec.taken@example.com")
    me = await user_factory(UserRole.STUDENT, email="sec.me@example.com")
    login = await lenient_client.post(f"{API}/auth/login", json={"email": me.email, "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = await lenient_client.patch(f"{API}/users/me", json={"email": "sec.taken@example.com"}, headers=headers)
    assert r.status_code in (400, 409, 422), f"duplicate e-mail -> {r.status_code}"


async def test_fapi_sec_009_email_is_case_insensitive_unique(client) -> None:
    body = {"password": "Correct-horse-battery-1", "first_name": "A", "last_name": "B"}
    first = await client.post(f"{API}/auth/register", json={**body, "email": "Sec.Case@example.com"})
    second = await client.post(f"{API}/auth/register", json={**body, "email": "sec.case@example.com"})
    assert first.status_code == 200
    assert second.status_code == 409, "two accounts registered for the same mailbox differing only in case"


# ── FAPI-SEC-011: lockout distinguishes real accounts ────────────────────────


async def test_fapi_sec_011_locked_account_is_indistinguishable_from_unknown(client, user_factory) -> None:
    victim = await user_factory(UserRole.COUNSELLOR, email="sec.lock@example.com")
    for _ in range(5):
        await client.post(f"{API}/auth/login", json={"email": victim.email, "password": "wrong-password-1"})
    locked = await client.post(f"{API}/auth/login", json={"email": victim.email, "password": "wrong-password-1"})
    unknown = await client.post(f"{API}/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert (locked.status_code, locked.json()) == (unknown.status_code, unknown.json()), (
        f"locked={locked.status_code} {locked.json()} vs unknown={unknown.status_code} {unknown.json()}"
    )


# ── Positive controls (expected to PASS) ─────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        '<a href="javascript:alert(1)">x</a>',
        '<a href=" JaVaScRiPt:alert(1)">x</a>',
        '<a href="\x01javascript:alert(1)">x</a>',
        '<a href="java&#x09;script:alert(1)">x</a>',
        '<img src=x onerror=alert(1)>',
        '<svg><script>alert(1)</script></svg>',
        '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        '<p onclick="alert(1)">x</p>',
    ],
)
def test_control_message_sanitiser_blocks_script(payload: str) -> None:
    from app.core.sanitize import sanitize_message_html

    cleaned = (sanitize_message_html(payload) or "").lower()
    assert "javascript" not in cleaned and "onerror" not in cleaned and "<script" not in cleaned
    assert "onclick" not in cleaned and "data:" not in cleaned


async def test_control_public_register_cannot_set_role(client) -> None:
    r = await client.post(
        f"{API}/auth/register",
        json={"email": "sec.role@example.com", "password": "Correct-horse-battery-1", "first_name": "A", "last_name": "B", "role": "super_admin"},
    )
    assert r.status_code == 422


async def test_control_refresh_token_is_not_a_bearer_token(client, user_factory) -> None:
    user = await user_factory(UserRole.STUDENT, email="sec.refresh@example.com")
    tokens = (await client.post(f"{API}/auth/login", json={"email": user.email, "password": "correct-horse-battery"})).json()
    r = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert r.status_code == 401


async def test_control_refresh_token_is_single_use(client, user_factory) -> None:
    user = await user_factory(UserRole.STUDENT, email="sec.rotate@example.com")
    tokens = (await client.post(f"{API}/auth/login", json={"email": user.email, "password": "correct-horse-battery"})).json()
    assert (await client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).status_code == 200
    assert (await client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).status_code == 401


async def test_control_alg_none_token_rejected(client, user_factory) -> None:
    import base64
    import json

    user = await user_factory(UserRole.ADMIN, email="sec.none@example.com")

    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': str(user.id), 'type': 'access', 'exp': 4102444800})}."
    r = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


async def test_control_student_cannot_read_other_students_document(client, user_factory, auth_headers) -> None:
    owner = await user_factory(UserRole.STUDENT, email="sec.docowner@example.com")
    other = await user_factory(UserRole.STUDENT, email="sec.docother@example.com")
    doc = (await _upload(client, await auth_headers(owner), owner)).json()
    other_headers = await auth_headers(other)
    assert (await client.get(f"{API}/documents/{doc['id']}", headers=other_headers)).status_code == 403
    assert (await client.get(f"{API}/documents/{doc['id']}/download", headers=other_headers)).status_code == 403
    assert (await client.get(f"{API}/student/me/documents/{doc['id']}", headers=other_headers)).status_code == 404
