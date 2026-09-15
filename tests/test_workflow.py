"""Workflow templates, application workflows, and the checklist.

Steps 6–10 were ported by copy-and-transform and were covered only by the two
architectural guards. These exercise the behaviour that actually matters: a
template instantiating into ordered steps, completing one advancing the next,
and a student seeing their own workflow but no one else's.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import UserRole

pytestmark = pytest.mark.asyncio

TEMPLATES = "/api/v1/workflow-templates"
ACADEMIC = "/api/v1"
APPLICATIONS = "/api/v1/applications"


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, user_factory, auth_headers) -> dict[str, str]:
    admin = await user_factory(UserRole.ADMIN, email="wf.admin@example.com")
    return await auth_headers(admin)


@pytest_asyncio.fixture
async def template(client: AsyncClient, admin_headers) -> dict:
    """A template with three ordered stages."""
    created = await client.post(TEMPLATES, json={"name": "Standard UK"}, headers=admin_headers)
    assert created.status_code == 200, created.text
    template_id = created.json()["id"]

    for key, name in (("docs", "Documents"), ("offer", "Offer"), ("visa", "Visa")):
        stage = await client.post(
            f"{TEMPLATES}/{template_id}/stages",
            json={"key": key, "name": name},
            headers=admin_headers,
        )
        assert stage.status_code == 200, stage.text

    return (await client.get(f"{TEMPLATES}/{template_id}", headers=admin_headers)).json()


@pytest_asyncio.fixture
async def application(client: AsyncClient, admin_headers, user_factory, auth_headers) -> dict:
    country = await client.post(f"{ACADEMIC}/countries", json={"name": "UK", "iso2": "GB"}, headers=admin_headers)
    university = await client.post(
        f"{ACADEMIC}/universities",
        json={"country_id": country.json()["id"], "name": "UCL"},
        headers=admin_headers,
    )
    program = await client.post(
        f"{ACADEMIC}/programs",
        json={"university_id": university.json()["id"], "name": "MSc CS"},
        headers=admin_headers,
    )
    student = await user_factory(UserRole.STUDENT, email="wf.student@example.com")
    created = await client.post(
        APPLICATIONS,
        json={"student_id": str(student.id), "program_id": program.json()["id"]},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    return {"application": created.json(), "student": student}


# ── Templates ─────────────────────────────────────────────────────────────────


async def test_a_template_slug_is_derived_and_stages_are_ordered(template: dict) -> None:
    assert template["slug"] == "standard-uk"
    assert [stage["key"] for stage in template["stages"]] == ["docs", "offer", "visa"]
    # Orders are 0-based.
    assert [stage["order"] for stage in template["stages"]] == [0, 1, 2]


async def test_stages_can_be_reordered(client: AsyncClient, admin_headers, template: dict) -> None:
    stage_ids = [stage["id"] for stage in template["stages"]]
    reversed_ids = list(reversed(stage_ids))

    response = await client.post(
        f"{TEMPLATES}/{template['id']}/stages/reorder",
        json={"stage_ids": reversed_ids},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    refreshed = await client.get(f"{TEMPLATES}/{template['id']}", headers=admin_headers)
    assert [stage["id"] for stage in refreshed.json()["stages"]] == reversed_ids
    assert [stage["order"] for stage in refreshed.json()["stages"]] == [0, 1, 2]


async def test_duplicating_a_template_copies_its_stages(
    client: AsyncClient,
    admin_headers,
    template: dict,
) -> None:
    response = await client.post(f"{TEMPLATES}/{template['id']}/duplicate", headers=admin_headers)
    assert response.status_code == 200, response.text
    copy = response.json()

    assert copy["id"] != template["id"]
    assert copy["slug"] != template["slug"], "a duplicate must not collide on the unique slug"
    assert [stage["key"] for stage in copy["stages"]] == ["docs", "offer", "visa"]


async def test_only_admins_manage_templates(client: AsyncClient, user_factory, auth_headers) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    response = await client.post(TEMPLATES, json={"name": "Sneaky"}, headers=await auth_headers(counsellor))
    assert response.status_code == 403


# ── Instantiation and advancement ─────────────────────────────────────────────


async def test_instantiating_creates_one_step_per_stage(
    client: AsyncClient,
    admin_headers,
    template: dict,
    application: dict,
) -> None:
    application_id = application["application"]["id"]
    response = await client.post(
        f"{APPLICATIONS}/{application_id}/workflow",
        json={"template_id": template["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    workflow = response.json()

    assert len(workflow["steps"]) == 3
    assert [step["order"] for step in workflow["steps"]] == [0, 1, 2]
    # The first step opens; the rest wait their turn.
    assert workflow["steps"][0]["status"] == "current"
    assert {step["status"] for step in workflow["steps"][1:]} == {"pending"}


async def test_completing_a_step_advances_the_next(
    client: AsyncClient,
    admin_headers,
    template: dict,
    application: dict,
) -> None:
    application_id = application["application"]["id"]
    workflow = (
        await client.post(
            f"{APPLICATIONS}/{application_id}/workflow",
            json={"template_id": template["id"]},
            headers=admin_headers,
        )
    ).json()
    first, second = workflow["steps"][0], workflow["steps"][1]

    completed = await client.patch(
        f"{APPLICATIONS}/{application_id}/workflow/steps/{first['id']}",
        json={"status": "completed"},
        headers=admin_headers,
    )
    assert completed.status_code == 200, completed.text

    refreshed = (await client.get(f"{APPLICATIONS}/{application_id}/workflow", headers=admin_headers)).json()
    by_id = {step["id"]: step for step in refreshed["steps"]}
    assert by_id[first["id"]]["status"] == "completed"
    assert by_id[second["id"]]["status"] == "current", "completing a step must open the next one"


async def test_completing_every_step_completes_the_workflow(
    client: AsyncClient,
    admin_headers,
    template: dict,
    application: dict,
) -> None:
    application_id = application["application"]["id"]
    workflow = (
        await client.post(
            f"{APPLICATIONS}/{application_id}/workflow",
            json={"template_id": template["id"]},
            headers=admin_headers,
        )
    ).json()

    for step in workflow["steps"]:
        await client.patch(
            f"{APPLICATIONS}/{application_id}/workflow/steps/{step['id']}",
            json={"status": "completed"},
            headers=admin_headers,
        )

    refreshed = (await client.get(f"{APPLICATIONS}/{application_id}/workflow", headers=admin_headers)).json()
    assert refreshed["status"] == "completed"
    assert refreshed["completed_at"] is not None


async def test_step_changes_are_recorded_as_activities(
    client: AsyncClient,
    admin_headers,
    template: dict,
    application: dict,
) -> None:
    application_id = application["application"]["id"]
    workflow = (
        await client.post(
            f"{APPLICATIONS}/{application_id}/workflow",
            json={"template_id": template["id"]},
            headers=admin_headers,
        )
    ).json()
    step_id = workflow["steps"][0]["id"]

    await client.patch(
        f"{APPLICATIONS}/{application_id}/workflow/steps/{step_id}",
        json={"status": "completed"},
        headers=admin_headers,
    )
    await client.post(
        f"{APPLICATIONS}/{application_id}/workflow/steps/{step_id}/activities",
        json={"comment": "Chased the university"},
        headers=admin_headers,
    )

    activities = await client.get(
        f"{APPLICATIONS}/{application_id}/workflow/steps/{step_id}/activities",
        headers=admin_headers,
    )
    assert activities.status_code == 200
    kinds = {a["activity_type"] for a in activities.json()}
    assert "comment" in kinds
    assert len(activities.json()) >= 2


# ── Ownership ─────────────────────────────────────────────────────────────────


async def test_a_student_sees_their_own_workflow_but_not_a_strangers(
    client: AsyncClient,
    admin_headers,
    template: dict,
    application: dict,
    user_factory,
    auth_headers,
) -> None:
    application_id = application["application"]["id"]
    await client.post(
        f"{APPLICATIONS}/{application_id}/workflow",
        json={"template_id": template["id"]},
        headers=admin_headers,
    )

    owner_headers = await auth_headers(application["student"])
    assert (await client.get(f"{APPLICATIONS}/{application_id}/workflow", headers=owner_headers)).status_code == 200

    intruder = await user_factory(UserRole.STUDENT, email="wf.intruder@example.com")
    intruder_headers = await auth_headers(intruder)
    assert (await client.get(f"{APPLICATIONS}/{application_id}/workflow", headers=intruder_headers)).status_code == 403


async def test_a_student_cannot_advance_their_own_workflow(
    client: AsyncClient,
    admin_headers,
    template: dict,
    application: dict,
    auth_headers,
) -> None:
    application_id = application["application"]["id"]
    workflow = (
        await client.post(
            f"{APPLICATIONS}/{application_id}/workflow",
            json={"template_id": template["id"]},
            headers=admin_headers,
        )
    ).json()

    response = await client.patch(
        f"{APPLICATIONS}/{application_id}/workflow/steps/{workflow['steps'][0]['id']}",
        json={"status": "completed"},
        headers=await auth_headers(application["student"]),
    )
    assert response.status_code == 403


# ── Checklist ─────────────────────────────────────────────────────────────────


async def test_checklist_items_belong_to_their_application(
    client: AsyncClient,
    admin_headers,
    application: dict,
    auth_headers,
) -> None:
    application_id = application["application"]["id"]
    created = await client.post(
        f"{APPLICATIONS}/{application_id}/checklist",
        json={"custom_label": "Upload passport"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text

    listed = await client.get(f"{APPLICATIONS}/{application_id}/checklist", headers=admin_headers)
    assert [item["custom_label"] for item in listed.json()] == ["Upload passport"]

    # The owning student can see it; a stranger cannot.
    owner_headers = await auth_headers(application["student"])
    assert (await client.get(f"{APPLICATIONS}/{application_id}/checklist", headers=owner_headers)).status_code == 200


# ── The document request round trip ───────────────────────────────────────────
#
# A requested document is two rows: the `ApplicationChecklistItem` that asked
# for it and the `Document` that answers it. The bug these cover is the two
# drifting — the student's Documents screen reads the `Document`, the staff
# console reads the item, and each side used to write only its own row, so a
# verified document went on saying "Pending" to the student who uploaded it.


async def _request_and_fulfil(client: AsyncClient, admin_headers, application: dict, auth_headers) -> tuple[str, str, str]:
    """Staff request a passport; the student uploads one and links it."""
    application_id = application["application"]["id"]
    student = application["student"]
    student_headers = await auth_headers(student)

    item = (
        await client.post(
            f"{APPLICATIONS}/{application_id}/checklist",
            json={"document_type": "passport"},
            headers=admin_headers,
        )
    ).json()

    uploaded = (
        await client.post(
            "/api/v1/documents/upload",
            data={"student_id": str(student.id), "document_type": "passport"},
            files={"file": ("passport.pdf", b"%PDF-1.4 scan", "application/pdf")},
            headers=student_headers,
        )
    ).json()

    linked = await client.patch(
        f"{APPLICATIONS}/{application_id}/checklist/{item['id']}",
        json={"document_id": uploaded["id"]},
        headers=student_headers,
    )
    assert linked.status_code == 200, linked.text
    return application_id, item["id"], uploaded["id"]


async def test_linking_a_document_submits_the_item_and_files_it_on_the_application(
    client: AsyncClient, admin_headers, application: dict, auth_headers
) -> None:
    application_id, item_id, document_id = await _request_and_fulfil(
        client, admin_headers, application, auth_headers
    )

    items = (await client.get(f"{APPLICATIONS}/{application_id}/checklist", headers=admin_headers)).json()
    assert items[0]["status"] == "submitted"
    assert items[0]["document_id"] == document_id

    # And the file is now the application's, not just the student's vault.
    student_headers = await auth_headers(application["student"])
    filed = await client.get(
        f"/api/v1/student/me/applications/{application_id}/documents", headers=student_headers
    )
    assert filed.status_code == 200, filed.text
    assert [d["id"] for d in filed.json()["items"]] == [document_id]


async def test_verifying_the_checklist_item_approves_the_underlying_document(
    client: AsyncClient, admin_headers, application: dict, auth_headers
) -> None:
    """The bug: staff pressed Verify here, the student kept seeing "Pending"."""
    application_id, item_id, document_id = await _request_and_fulfil(
        client, admin_headers, application, auth_headers
    )

    verified = await client.patch(
        f"{APPLICATIONS}/{application_id}/checklist/{item_id}",
        json={"status": "verified"},
        headers=admin_headers,
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "verified"

    student_headers = await auth_headers(application["student"])
    document = (await client.get(f"/api/v1/documents/{document_id}", headers=student_headers)).json()
    assert document["status"] == "approved"
    assert document["verified_at"] is not None


async def test_rejecting_the_checklist_item_rejects_the_underlying_document(
    client: AsyncClient, admin_headers, application: dict, auth_headers
) -> None:
    application_id, item_id, document_id = await _request_and_fulfil(
        client, admin_headers, application, auth_headers
    )

    await client.patch(
        f"{APPLICATIONS}/{application_id}/checklist/{item_id}",
        json={"status": "rejected", "notes": "The photo page is cut off."},
        headers=admin_headers,
    )

    student_headers = await auth_headers(application["student"])
    document = (await client.get(f"/api/v1/documents/{document_id}", headers=student_headers)).json()
    assert document["status"] == "rejected"
    assert document["rejection_reason"] == "The photo page is cut off."


async def test_verifying_the_document_verifies_the_checklist_item(
    client: AsyncClient, admin_headers, application: dict, auth_headers
) -> None:
    """The other direction, which the event subscribers already handled — kept
    here so the pair is covered in one place."""
    application_id, item_id, document_id = await _request_and_fulfil(
        client, admin_headers, application, auth_headers
    )

    await client.post(f"/api/v1/documents/{document_id}/verify", json={}, headers=admin_headers)

    items = (await client.get(f"{APPLICATIONS}/{application_id}/checklist", headers=admin_headers)).json()
    assert items[0]["status"] == "verified"


async def test_re_uploading_against_a_rejected_item_puts_it_back_under_review(
    client: AsyncClient, admin_headers, application: dict, auth_headers
) -> None:
    application_id, item_id, _ = await _request_and_fulfil(client, admin_headers, application, auth_headers)
    student = application["student"]
    student_headers = await auth_headers(student)

    await client.patch(
        f"{APPLICATIONS}/{application_id}/checklist/{item_id}",
        json={"status": "rejected", "notes": "Unreadable."},
        headers=admin_headers,
    )

    replacement = (
        await client.post(
            "/api/v1/documents/upload",
            data={"student_id": str(student.id), "document_type": "passport"},
            files={"file": ("passport-2.pdf", b"%PDF-1.4 better scan", "application/pdf")},
            headers=student_headers,
        )
    ).json()
    relinked = await client.patch(
        f"{APPLICATIONS}/{application_id}/checklist/{item_id}",
        json={"document_id": replacement["id"]},
        headers=student_headers,
    )

    assert relinked.status_code == 200, relinked.text
    assert relinked.json()["status"] == "submitted"
    assert relinked.json()["document_id"] == replacement["id"]


async def test_staff_uploading_the_requested_file_lands_the_item_verified(
    client: AsyncClient, admin_headers, application: dict, auth_headers
) -> None:
    """A document that arrives already decided does not go back in the queue.

    Staff uploading on a student's behalf files the row approved — they have the
    document in front of them, and there is nobody left to verify it against. An
    item linked to such a document must say `verified`, not `submitted`, or the
    checklist and the file it points at disagree the moment they are joined.
    """
    application_id = application["application"]["id"]
    student = application["student"]

    item = (
        await client.post(
            f"{APPLICATIONS}/{application_id}/checklist",
            json={"document_type": "offer_letter"},
            headers=admin_headers,
        )
    ).json()

    uploaded = (
        await client.post(
            "/api/v1/documents/upload",
            data={"student_id": str(student.id), "document_type": "offer_letter"},
            files={"file": ("offer.pdf", b"%PDF-1.4 offer", "application/pdf")},
            headers=admin_headers,
        )
    ).json()
    assert uploaded["status"] == "approved"

    linked = await client.patch(
        f"{APPLICATIONS}/{application_id}/checklist/{item['id']}",
        json={"document_id": uploaded["id"]},
        headers=admin_headers,
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["status"] == "verified"


async def test_a_students_own_upload_still_lands_the_item_submitted(
    client: AsyncClient, admin_headers, application: dict, auth_headers
) -> None:
    """The other half: a student's upload genuinely is waiting on someone."""
    _, item_id, _ = await _request_and_fulfil(client, admin_headers, application, auth_headers)
    items = (
        await client.get(
            f"{APPLICATIONS}/{application['application']['id']}/checklist", headers=admin_headers
        )
    ).json()
    assert next(i for i in items if i["id"] == item_id)["status"] == "submitted"
