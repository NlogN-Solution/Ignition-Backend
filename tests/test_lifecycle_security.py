"""Cross-student isolation, across every surface this rebuild added.

PART 25 of the brief. One student, one stranger, and every new endpoint that
returns something belonging to a person. The interesting property is not that
each check exists but that none of them was forgotten — so this walks the whole
surface rather than sampling it.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

STUDENT = "/api/v1/student"
COMMS = "/api/v1/communication"
DOCUMENTS = "/api/v1/documents"
APPLICATIONS = "/api/v1/applications"
ACADEMIC = "/api/v1"


@pytest_asyncio.fixture
async def two_students(client: AsyncClient, user_factory, auth_headers, access_fee) -> dict:
    admin = await user_factory(UserRole.ADMIN, email="sec.admin@example.com")
    staff = await auth_headers(admin)

    country = (await client.post(f"{ACADEMIC}/countries", json={"name": "UK", "iso2": "GB"}, headers=staff)).json()
    university = (
        await client.post(
            f"{ACADEMIC}/universities",
            json={"country_id": country["id"], "name": "Coventry"},
            headers=staff,
        )
    ).json()
    program = (
        await client.post(
            f"{ACADEMIC}/programs",
            json={"university_id": university["id"], "name": "MSc CS"},
            headers=staff,
        )
    ).json()

    owner = await user_factory(UserRole.STUDENT, email="sec.owner@example.com")
    stranger = await user_factory(UserRole.STUDENT, email="sec.stranger@example.com")
    application = (
        await client.post(
            APPLICATIONS,
            json={"student_id": str(owner.id), "program_id": program["id"]},
            headers=staff,
        )
    ).json()

    # An offer, so there is a gated document to try to reach.
    await client.post(
        f"{APPLICATIONS}/{application['id']}/milestone",
        data={"status": "offer_received", "offer_received_date": "2026-03-02"},
        files={"letter": ("offer.pdf", b"%PDF-1.4 offer", "application/pdf")},
        headers=staff,
    )

    return {
        "staff": staff,
        "owner": owner,
        "owner_headers": await auth_headers(owner),
        "stranger_headers": await auth_headers(stranger),
        "application": application,
    }


async def test_a_stranger_sees_none_of_the_owners_application(
    client: AsyncClient, two_students: dict
) -> None:
    application_id = two_students["application"]["id"]
    stranger = two_students["stranger_headers"]

    for path in (
        f"{STUDENT}/me/applications/{application_id}",
        f"{STUDENT}/me/applications/{application_id}/documents",
        f"{STUDENT}/me/applications/{application_id}/timeline",
        f"{STUDENT}/me/applications/{application_id}/checklist",
    ):
        response = await client.get(path, headers=stranger)
        assert response.status_code == 404, f"{path} answered {response.status_code}"


async def test_a_stranger_cannot_reach_the_owners_offer_letter(
    client: AsyncClient, two_students: dict
) -> None:
    """Two boundaries stacked: ownership *and* the paywall.

    Ownership is the one that must answer first — a stranger being told to pay
    would confirm the document exists.
    """
    owner_documents = (
        await client.get(f"{STUDENT}/me/documents", headers=two_students["owner_headers"])
    ).json()["items"]
    offer = next(d for d in owner_documents if d["document_type"] == "offer_letter")

    stranger = two_students["stranger_headers"]
    for path in (
        f"{DOCUMENTS}/{offer['id']}",
        f"{DOCUMENTS}/{offer['id']}/link",
        f"{DOCUMENTS}/{offer['id']}/download",
    ):
        response = await client.get(path, headers=stranger)
        assert response.status_code == 403, f"{path} answered {response.status_code}"


async def test_a_stranger_gets_no_threads_attachments_or_milestones(
    client: AsyncClient, two_students: dict
) -> None:
    staff = two_students["staff"]
    owner = two_students["owner"]
    stranger = two_students["stranger_headers"]

    thread = (
        await client.post(
            f"{COMMS}/threads",
            json={"subject": "Private", "body": "Only for you.", "student_id": str(owner.id)},
            headers=staff,
        )
    ).json()

    assert (await client.get(f"{COMMS}/threads/{thread['id']}", headers=stranger)).status_code == 404
    assert (await client.get(f"{STUDENT}/me/threads", headers=stranger)).json() == []
    assert (await client.get(f"{STUDENT}/me/milestones/unseen", headers=stranger)).json() == []


async def test_a_student_cannot_use_the_staff_communication_surface(
    client: AsyncClient, two_students: dict
) -> None:
    """The lead and student thread listings are staff-only. A student calling
    them with their *own* id must still be refused — otherwise the internal
    notes filter is one query parameter away from being bypassed."""
    owner = two_students["owner"]
    headers = two_students["owner_headers"]

    for path in (
        f"{COMMS}/threads",
        f"{COMMS}/students/{owner.id}/threads",
        f"{COMMS}/leads/{owner.id}/threads",
    ):
        response = await client.get(path, headers=headers)
        assert response.status_code == 403, f"{path} answered {response.status_code}"


async def test_a_student_cannot_record_a_milestone_on_their_own_application(
    client: AsyncClient, two_students: dict
) -> None:
    """Granting yourself an offer would be the most attractive bug in the
    product."""
    response = await client.post(
        f"{APPLICATIONS}/{two_students['application']['id']}/milestone",
        data={"status": "offer_received", "offer_received_date": "2026-01-01"},
        files={"letter": ("fake.pdf", b"%PDF-1.4 self-granted", "application/pdf")},
        headers=two_students["owner_headers"],
    )
    assert response.status_code == 403


async def test_the_milestone_route_cannot_write_arbitrary_columns(
    client: AsyncClient, two_students: dict
) -> None:
    """The form is a fixed set of `Form(...)` parameters and the service checks
    an allowlist on top. A field that is neither is ignored by FastAPI rather
    than reaching the model — asserted so a future refactor to `**kwargs`
    cannot quietly open it."""
    response = await client.post(
        f"{APPLICATIONS}/{two_students['application']['id']}/milestone",
        data={
            "status": "cas_received",
            "cas_received_date": "2026-05-01",
            "student_id": "00000000-0000-0000-0000-000000000000",
        },
        files={"letter": ("cas.pdf", b"%PDF-1.4 cas", "application/pdf")},
        headers=two_students["staff"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["student_id"] == str(two_students["owner"].id), (
        "the milestone path must not be able to reassign an application"
    )
