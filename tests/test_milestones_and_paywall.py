"""Offer, CAS, and the one-time unlock that gates them.

TESTS G–K from the brief. The properties worth protecting here are the ones a
UI cannot enforce:

* recording a milestone is **all-or-nothing** — a status claiming an offer with
  no date and no letter behind it is the bug the whole phase exists to fix;
* the paywall is enforced **where the bytes are**, so `curl` cannot walk past
  a hidden button;
* the fee is **once** — a second offer must not ask for a second payment.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

APPLICATIONS = "/api/v1/applications"
DOCUMENTS = "/api/v1/documents"
STUDENT = "/api/v1/student"
ACADEMIC = "/api/v1"

PDF = ("offer.pdf", b"%PDF-1.4 offer letter", "application/pdf")


@pytest_asyncio.fixture
async def setup(client: AsyncClient, user_factory, auth_headers, access_fee) -> dict:
    """A student with one application, and an admin to work it."""
    admin = await user_factory(UserRole.ADMIN, email="ms.admin@example.com")
    staff = await auth_headers(admin)

    country = (await client.post(f"{ACADEMIC}/countries", json={"name": "United Kingdom", "iso2": "GB"}, headers=staff)).json()
    university = (
        await client.post(
            f"{ACADEMIC}/universities",
            json={"country_id": country["id"], "name": "University of Coventry"},
            headers=staff,
        )
    ).json()
    program = (
        await client.post(
            f"{ACADEMIC}/programs",
            json={"university_id": university["id"], "name": "MSc Computer Science"},
            headers=staff,
        )
    ).json()

    student = await user_factory(UserRole.STUDENT, email="ms.student@example.com")
    application = (
        await client.post(
            APPLICATIONS,
            json={"student_id": str(student.id), "program_id": program["id"]},
            headers=staff,
        )
    ).json()

    return {
        "staff": staff,
        "student": student,
        "student_headers": await auth_headers(student),
        "application": application,
        "program": program,
        "university": university,
    }


async def _record_offer(client: AsyncClient, setup: dict, *, with_letter: bool = True, date: str = "2026-03-02"):
    files = {"letter": PDF} if with_letter else None
    return await client.post(
        f"{APPLICATIONS}/{setup['application']['id']}/milestone",
        data={
            "status": "offer_received",
            "offer_received_date": date,
            "offer_type": "conditional",
            "remarks": "Emailed by admissions.",
        },
        files=files,
        headers=setup["staff"],
    )


async def _unlock(client: AsyncClient, setup: dict):
    return await client.post(
        f"{STUDENT}/me/access/checkout",
        json={"payment_method": "esewa"},
        headers=setup["student_headers"],
    )


# ── TEST G — recording an offer is one act ───────────────────────────────────


async def test_the_status_dropdown_alone_cannot_claim_an_offer(client: AsyncClient, setup: dict) -> None:
    """The plain status route refuses milestone statuses.

    This is the bug in its original form: a dropdown and a remarks box produced
    `offer_received` with no date and no letter, and the student's portal showed
    a green badge with nothing to open.
    """
    response = await client.post(
        f"{APPLICATIONS}/{setup['application']['id']}/status",
        json={"status": "offer_received", "remarks": "got it"},
        headers=setup["staff"],
    )
    assert response.status_code == 400
    assert "milestone" in response.json()["detail"].lower()


async def test_an_offer_with_no_date_is_refused(client: AsyncClient, setup: dict) -> None:
    response = await client.post(
        f"{APPLICATIONS}/{setup['application']['id']}/milestone",
        data={"status": "offer_received"},
        files={"letter": PDF},
        headers=setup["staff"],
    )
    assert response.status_code == 400


async def test_an_offer_with_no_letter_is_refused_and_nothing_is_written(
    client: AsyncClient, setup: dict
) -> None:
    """Atomicity, from the outside: a refused milestone must leave the
    application exactly where it was."""
    refused = await _record_offer(client, setup, with_letter=False)
    assert refused.status_code == 400

    application = (
        await client.get(f"{APPLICATIONS}/{setup['application']['id']}", headers=setup["staff"])
    ).json()
    assert application["status"] == "draft", "a refused milestone must not move the status"
    assert application["offer_received_date"] is None, "nor write the date"


async def test_recording_an_offer_writes_status_date_letter_and_milestone(
    client: AsyncClient, setup: dict
) -> None:
    """TEST G. One request, and everything lands together."""
    response = await _record_offer(client, setup)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "offer_received"
    assert body["offer_received_date"] == "2026-03-02"
    assert body["offer_type"] == "conditional"

    # The letter is on the application, so the student's portal can find it.
    filed = await client.get(
        f"{STUDENT}/me/applications/{setup['application']['id']}/documents",
        headers=setup["student_headers"],
    )
    assert [d["document_type"] for d in filed.json()["items"]] == ["offer_letter"]

    # The student is notified, and the celebration is waiting.
    notifications = (await client.get(f"{STUDENT}/me/notifications", headers=setup["student_headers"])).json()
    assert any("Offer received" in n["title"] for n in notifications["items"])

    unseen = (await client.get(f"{STUDENT}/me/milestones/unseen", headers=setup["student_headers"])).json()
    assert [m["kind"] for m in unseen] == ["offer_received"]
    assert unseen[0]["university_name"] == "University of Coventry"


async def test_the_celebration_fires_once(client: AsyncClient, setup: dict) -> None:
    """TEST G, second half. Confetti on every dashboard load is not a
    celebration."""
    await _record_offer(client, setup)
    headers = setup["student_headers"]

    unseen = (await client.get(f"{STUDENT}/me/milestones/unseen", headers=headers)).json()
    acknowledged = await client.post(f"{STUDENT}/me/milestones/{unseen[0]['id']}/seen", headers=headers)
    assert acknowledged.status_code == 200

    assert (await client.get(f"{STUDENT}/me/milestones/unseen", headers=headers)).json() == []

    # The notification survives it — a student who dismissed the overlay must
    # still be able to find the offer.
    notifications = (await client.get(f"{STUDENT}/me/notifications", headers=headers)).json()
    assert any("Offer received" in n["title"] for n in notifications["items"])


async def test_correcting_the_date_does_not_re_fire_the_celebration(
    client: AsyncClient, setup: dict
) -> None:
    await _record_offer(client, setup)
    headers = setup["student_headers"]
    unseen = (await client.get(f"{STUDENT}/me/milestones/unseen", headers=headers)).json()
    await client.post(f"{STUDENT}/me/milestones/{unseen[0]['id']}/seen", headers=headers)

    fixed = await _record_offer(client, setup, date="2026-03-09")
    assert fixed.status_code == 200
    assert fixed.json()["offer_received_date"] == "2026-03-09"
    assert (await client.get(f"{STUDENT}/me/milestones/unseen", headers=headers)).json() == [], (
        "a typo correction is not new news"
    )


async def test_a_student_cannot_dismiss_someone_elses_milestone(
    client: AsyncClient, setup: dict, user_factory, auth_headers
) -> None:
    await _record_offer(client, setup)
    unseen = (await client.get(f"{STUDENT}/me/milestones/unseen", headers=setup["student_headers"])).json()

    stranger = await user_factory(UserRole.STUDENT, email="ms.stranger@example.com")
    response = await client.post(
        f"{STUDENT}/me/milestones/{unseen[0]['id']}/seen", headers=await auth_headers(stranger)
    )
    assert response.status_code == 404


# ── TESTS H / I / J — the paywall ────────────────────────────────────────────


async def test_a_locked_student_cannot_reach_the_offer_letter_through_the_api(
    client: AsyncClient, setup: dict
) -> None:
    """TEST H. Hiding the button is not a gate.

    The download route is a URL with a bearer token. This is the request a
    student could otherwise make with curl.
    """
    await _record_offer(client, setup)
    headers = setup["student_headers"]

    documents = (await client.get(f"{STUDENT}/me/documents", headers=headers)).json()["items"]
    offer = next(d for d in documents if d["document_type"] == "offer_letter")

    # They are *told* it exists — that is the point of the paywall.
    assert offer["title"]

    for path in (f"{DOCUMENTS}/{offer['id']}/link", f"{DOCUMENTS}/{offer['id']}/download"):
        response = await client.get(path, headers=headers)
        assert response.status_code == 402, f"{path} answered {response.status_code}"


async def test_their_own_documents_are_never_gated(client: AsyncClient, setup: dict) -> None:
    """Charging somebody to read a file they supplied would be indefensible."""
    headers = setup["student_headers"]
    uploaded = await client.post(
        f"{DOCUMENTS}/upload",
        data={"student_id": str(setup["student"].id), "document_type": "passport"},
        files={"file": ("passport.pdf", b"%PDF-1.4 scan", "application/pdf")},
        headers=headers,
    )
    assert (await client.get(f"{DOCUMENTS}/{uploaded.json()['id']}/link", headers=headers)).status_code == 200


async def test_staff_are_never_gated(client: AsyncClient, setup: dict) -> None:
    await _record_offer(client, setup)
    documents = (await client.get(f"{DOCUMENTS}?document_type=offer_letter", headers=setup["staff"])).json()["items"]
    response = await client.get(f"{DOCUMENTS}/{documents[0]['id']}/link", headers=setup["staff"])
    assert response.status_code == 200


async def test_paying_unlocks_the_letter_server_side(client: AsyncClient, setup: dict) -> None:
    """TEST I. The entitlement is a row, not a flag in the browser."""
    await _record_offer(client, setup)
    headers = setup["student_headers"]
    documents = (await client.get(f"{STUDENT}/me/documents", headers=headers)).json()["items"]
    offer = next(d for d in documents if d["document_type"] == "offer_letter")

    assert (await client.get(f"{DOCUMENTS}/{offer['id']}/link", headers=headers)).status_code == 402

    paid = await _unlock(client, setup)
    assert paid.status_code == 200, paid.text
    assert paid.json()["has_access"] is True

    assert (await client.get(f"{DOCUMENTS}/{offer['id']}/link", headers=headers)).status_code == 200
    # And it is still unlocked on a fresh request, because nothing about it
    # lives in the client.
    assert (await client.get(f"{DOCUMENTS}/{offer['id']}/download", headers=headers)).status_code in (200, 307)


async def test_the_quoted_fee_is_the_configured_one(client: AsyncClient, setup: dict) -> None:
    """The student is quoted from `portal_access_fees`, not from a constant in
    the client — which is also why a caller cannot name their own amount."""
    state = (await client.get(f"{STUDENT}/me/access", headers=setup["student_headers"])).json()
    assert state["has_access"] is False
    assert state["fee"]["currency"] == "NPR"
    assert state["fee"]["amount"] == 5000


async def test_a_second_offer_needs_no_second_payment(
    client: AsyncClient, setup: dict, user_factory
) -> None:
    """TEST J. The fee is for the platform, once — not per offer."""
    await _record_offer(client, setup)
    await _unlock(client, setup)
    headers = setup["student_headers"]

    # A second application, a second offer.
    second_program = (
        await client.post(
            f"{ACADEMIC}/programs",
            json={"university_id": setup["university"]["id"], "name": "MSc Data Science"},
            headers=setup["staff"],
        )
    ).json()
    second = (
        await client.post(
            APPLICATIONS,
            json={"student_id": str(setup["student"].id), "program_id": second_program["id"]},
            headers=setup["staff"],
        )
    ).json()
    recorded = await client.post(
        f"{APPLICATIONS}/{second['id']}/milestone",
        data={"status": "offer_received", "offer_received_date": "2026-04-01"},
        files={"letter": ("offer2.pdf", b"%PDF-1.4 second offer", "application/pdf")},
        headers=setup["staff"],
    )
    assert recorded.status_code == 200, recorded.text

    # No second checkout, and the new letter opens straight away.
    assert (await _unlock(client, setup)).status_code == 409, "already paid"
    filed = (
        await client.get(f"{STUDENT}/me/applications/{second['id']}/documents", headers=headers)
    ).json()["items"]
    offer = next(d for d in filed if d["document_type"] == "offer_letter")
    assert (await client.get(f"{DOCUMENTS}/{offer['id']}/link", headers=headers)).status_code == 200

    # And it is a second celebration, because it is genuinely a second offer.
    unseen = (await client.get(f"{STUDENT}/me/milestones/unseen", headers=headers)).json()
    assert len(unseen) == 2


# ── TEST K — CAS ─────────────────────────────────────────────────────────────


async def test_cas_needs_its_date_and_letter_and_notifies(client: AsyncClient, setup: dict) -> None:
    """TEST K. Same machinery, a different row in the requirements table."""
    application_id = setup["application"]["id"]

    refused = await client.post(
        f"{APPLICATIONS}/{application_id}/milestone",
        data={"status": "cas_received"},
        headers=setup["staff"],
    )
    assert refused.status_code == 400

    recorded = await client.post(
        f"{APPLICATIONS}/{application_id}/milestone",
        data={
            "status": "cas_received",
            "cas_received_date": "2026-05-20",
            "cas_number": "E4G8H7J2K1",
        },
        files={"letter": ("cas.pdf", b"%PDF-1.4 cas statement", "application/pdf")},
        headers=setup["staff"],
    )
    assert recorded.status_code == 200, recorded.text
    body = recorded.json()
    assert body["status"] == "cas_received"
    assert body["cas_received_date"] == "2026-05-20"
    assert body["cas_number"] == "E4G8H7J2K1"

    headers = setup["student_headers"]
    notifications = (await client.get(f"{STUDENT}/me/notifications", headers=headers)).json()
    assert any("CAS" in n["title"] for n in notifications["items"])

    # Gated on the same one-time fee, not a new one.
    filed = (
        await client.get(f"{STUDENT}/me/applications/{application_id}/documents", headers=headers)
    ).json()["items"]
    cas = next(d for d in filed if d["document_type"] == "cas_letter")
    assert (await client.get(f"{DOCUMENTS}/{cas['id']}/link", headers=headers)).status_code == 402

    await _unlock(client, setup)
    assert (await client.get(f"{DOCUMENTS}/{cas['id']}/link", headers=headers)).status_code == 200


# ── TEST F — staff hear about a new application ──────────────────────────────


async def test_submitting_an_application_notifies_staff(
    client: AsyncClient, setup: dict, user_factory, auth_headers
) -> None:
    """TEST F. Nothing used to tell them at all."""
    submitted = await client.post(
        f"{STUDENT}/me/applications/{setup['application']['id']}/submit",
        headers=setup["student_headers"],
    )
    assert submitted.status_code == 200, submitted.text

    notifications = (await client.get("/api/v1/notifications", headers=setup["staff"])).json()
    matching = [n for n in notifications["items"] if n["title"] == "New application submitted"]
    assert matching, "staff must be told an application arrived"
    assert "MSc Computer Science" in matching[0]["message"]
    # Typed, so the client can route to it without parsing the sentence.
    assert matching[0]["related_type"] == "application"
    assert matching[0]["action_url"] == f"/applications/{setup['application']['id']}"


async def test_pressing_submit_twice_does_not_notify_twice(client: AsyncClient, setup: dict) -> None:
    for _ in range(2):
        await client.post(
            f"{STUDENT}/me/applications/{setup['application']['id']}/submit",
            headers=setup["student_headers"],
        )
    notifications = (await client.get("/api/v1/notifications", headers=setup["staff"])).json()
    assert len([n for n in notifications["items"] if n["title"] == "New application submitted"]) == 1
