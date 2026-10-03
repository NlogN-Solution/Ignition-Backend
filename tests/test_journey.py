"""The UK student journey, end to end through the API.

What is worth pinning here is what the UI cannot enforce: that only the open
stage moves, that each side can only take its own actions, that statuses and
stages stay one story (an offer recorded through the existing milestone route
completes the offer stage; a failed suitability interview rejects the
application), and that nobody reaches a journey they could not reach the
application of.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models.enums import UserRole
from app.services.journey_templates import ensure_uk_journey_template

pytestmark = pytest.mark.asyncio

APPLICATIONS = "/api/v1/applications"
DOCUMENTS = "/api/v1/documents"
ACADEMIC = "/api/v1"

PDF = ("file.pdf", b"%PDF-1.4 a document", "application/pdf")


@pytest_asyncio.fixture
async def setup(client: AsyncClient, session, user_factory, auth_headers) -> dict:
    """A UK master's application on the UK journey, a student and their counsellor."""
    admin = await user_factory(UserRole.ADMIN, email="j.admin@example.com")
    admin_headers = await auth_headers(admin)
    counsellor = await user_factory(UserRole.COUNSELLOR, email="j.counsellor@example.com")

    country = (
        await client.post(f"{ACADEMIC}/countries", json={"name": "United Kingdom", "iso2": "GB"}, headers=admin_headers)
    ).json()
    await ensure_uk_journey_template(session)
    university = (
        await client.post(
            f"{ACADEMIC}/universities", json={"country_id": country["id"], "name": "UCL"}, headers=admin_headers
        )
    ).json()

    async def program(name: str, level: str) -> dict:
        response = await client.post(
            f"{ACADEMIC}/programs",
            json={"university_id": university["id"], "name": name, "degree_level": level},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    master = await program("MSc Computer Science", "master")
    bachelor = await program("BSc Computer Science", "bachelor")

    student = await user_factory(UserRole.STUDENT, email="j.student@example.com")

    async def application(program_id: str) -> dict:
        created = await client.post(
            APPLICATIONS,
            json={"student_id": str(student.id), "program_id": program_id, "counsellor_id": str(counsellor.id)},
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        app_id = created.json()["id"]
        started = await client.post(f"{APPLICATIONS}/{app_id}/workflow", json={}, headers=admin_headers)
        assert started.status_code == 200, started.text
        return created.json()

    return {
        "admin": admin_headers,
        "staff": await auth_headers(counsellor),
        "student": student,
        "student_headers": await auth_headers(student),
        "application": await application(master["id"]),
        "new_application": application,
        "bachelor": bachelor,
    }


def _url(setup: dict, suffix: str = "") -> str:
    return f"{APPLICATIONS}/{setup['application']['id']}/journey{suffix}"


async def _journey(client: AsyncClient, setup: dict, headers: dict | None = None) -> dict:
    response = await client.get(_url(setup), headers=headers or setup["student_headers"])
    assert response.status_code == 200, response.text
    return response.json()


def _step(journey: dict, key: str) -> dict:
    return next(step for step in journey["steps"] if step["key"] == key)


async def _upload_and_link(client: AsyncClient, setup: dict, item: dict, *, document_type: str | None = None) -> None:
    uploaded = await client.post(
        f"{DOCUMENTS}/upload",
        data={
            "student_id": str(setup["student"].id),
            "document_type": document_type or item["document_type"],
            "application_id": setup["application"]["id"],
        },
        files={"file": PDF},
        headers=setup["student_headers"],
    )
    assert uploaded.status_code == 200, uploaded.text
    linked = await client.patch(
        f"{APPLICATIONS}/{setup['application']['id']}/checklist/{item['id']}",
        json={"document_id": uploaded.json()["id"]},
        headers=setup["student_headers"],
    )
    assert linked.status_code == 200, linked.text


async def _finish_documents_stage(client: AsyncClient, setup: dict, key: str) -> dict:
    step = _step(await _journey(client, setup), key)
    for item in step["checklist"]:
        await _upload_and_link(client, setup, item)
    response = await client.post(_url(setup, f"/steps/{step['id']}/submit"), headers=setup["student_headers"])
    assert response.status_code == 200, response.text
    return response.json()


async def _record(client: AsyncClient, setup: dict, status: str, date_field: str, **extra) -> None:
    response = await client.post(
        f"{APPLICATIONS}/{setup['application']['id']}/milestone",
        data={"status": status, date_field: "2026-10-01", **extra},
        files={"letter": PDF},
        headers=setup["staff"],
    )
    assert response.status_code == 200, response.text


async def _pass_review(client: AsyncClient, setup: dict, key: str, **payload) -> None:
    step = _step(await _journey(client, setup), key)
    submitted = await client.post(
        _url(setup, f"/steps/{step['id']}/submissions"), json=payload, headers=setup["student_headers"]
    )
    assert submitted.status_code == 200, submitted.text
    submission = _step(submitted.json(), key)["submissions"][-1]
    reviewed = await client.post(
        _url(setup, f"/submissions/{submission['id']}/review"), json={"verdict": "verified"}, headers=setup["staff"]
    )
    assert reviewed.status_code == 200, reviewed.text


def _future(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


async def _book_and_decide(client: AsyncClient, setup: dict, key: str, outcome: str) -> dict:
    step = _step(await _journey(client, setup), key)
    published = await client.post(
        _url(setup, f"/steps/{step['id']}/slots"),
        json={"slots": [{"starts_at": _future(3)}, {"starts_at": _future(4)}]},
        headers=setup["staff"],
    )
    assert published.status_code == 200, published.text
    open_slots = [s for s in _step(published.json(), key)["slots"] if s["status"] == "open"]
    booked = await client.post(_url(setup, f"/slots/{open_slots[0]['id']}/book"), headers=setup["student_headers"])
    assert booked.status_code == 200, booked.text
    decided = await client.post(
        _url(setup, f"/slots/{open_slots[0]['id']}/outcome"), json={"outcome": outcome}, headers=setup["staff"]
    )
    assert decided.status_code == 200, decided.text
    return decided.json()


# ── Shape ─────────────────────────────────────────────────────────────────────


async def test_a_uk_application_gets_the_ten_stage_journey(client: AsyncClient, setup: dict) -> None:
    journey = await _journey(client, setup)

    assert journey["template_name"] == "UK Student Journey"
    assert [step["kind"] for step in journey["steps"]] == [
        "documents", "issued", "review", "review", "booking", "booking", "documents", "documents", "issued", "checklist",
    ]  # fmt: skip
    assert journey["current_step_id"] == journey["steps"][0]["id"]
    assert journey["steps"][0]["waiting_on"] == "student"


async def test_a_postgraduate_is_asked_for_bachelor_documents_and_an_undergraduate_is_not(
    client: AsyncClient, setup: dict
) -> None:
    journey = await _journey(client, setup)
    assert journey["study_level"] == "pg"
    labels = [item["custom_label"] for item in _step(journey, "application")["checklist"]]
    assert len(labels) == 9 and "Bachelor's transcripts" in labels

    ug = await setup["new_application"](setup["bachelor"]["id"])
    ug_journey = (await client.get(f"{APPLICATIONS}/{ug['id']}/journey", headers=setup["student_headers"])).json()
    assert ug_journey["study_level"] == "ug"
    ug_labels = [item["custom_label"] for item in _step(ug_journey, "application")["checklist"]]
    assert len(ug_labels) == 7 and "Bachelor's transcripts" not in ug_labels


async def test_the_gap_answer_adds_and_removes_the_gap_document(client: AsyncClient, setup: dict) -> None:
    added = await client.put(_url(setup, "/study-gap"), json={"has_study_gap": True}, headers=setup["student_headers"])
    assert added.status_code == 200, added.text
    assert added.json()["has_study_gap"] is True
    assert len(_step(added.json(), "application")["checklist"]) == 10

    removed = await client.put(
        _url(setup, "/study-gap"), json={"has_study_gap": False}, headers=setup["student_headers"]
    )
    assert len(_step(removed.json(), "application")["checklist"]) == 9


# ── Documents and issued stages ───────────────────────────────────────────────


async def test_a_documents_stage_cannot_be_submitted_with_documents_missing(client: AsyncClient, setup: dict) -> None:
    step = _step(await _journey(client, setup), "application")
    response = await client.post(_url(setup, f"/steps/{step['id']}/submit"), headers=setup["student_headers"])
    assert response.status_code == 400
    assert "Still needed" in response.json()["detail"]


async def test_submitting_application_documents_moves_the_status_forward(client: AsyncClient, setup: dict) -> None:
    journey = await _finish_documents_stage(client, setup, "application")

    assert _step(journey, "application")["status"] == "completed"
    assert _step(journey, "offer")["status"] == "current"
    assert _step(journey, "offer")["waiting_on"] == "staff"
    application = (await client.get(f"{APPLICATIONS}/{setup['application']['id']}", headers=setup["staff"])).json()
    assert application["status"] == "ready_to_submit"


async def test_recording_the_offer_completes_the_offer_stage(client: AsyncClient, setup: dict) -> None:
    await _finish_documents_stage(client, setup, "application")
    await _record(client, setup, "offer_received", "offer_received_date", offer_type="conditional")

    journey = await _journey(client, setup)
    assert _step(journey, "offer")["status"] == "completed"
    assert _step(journey, "interview_prep")["status"] == "current"


async def test_an_offer_recorded_early_skips_the_unfinished_stages_before_it(client: AsyncClient, setup: dict) -> None:
    await _record(client, setup, "offer_received", "offer_received_date")

    journey = await _journey(client, setup)
    assert _step(journey, "application")["status"] == "skipped"
    assert _step(journey, "offer")["status"] == "completed"
    assert _step(journey, "interview_prep")["status"] == "current"


# ── Review rounds ─────────────────────────────────────────────────────────────


async def test_a_review_round_can_be_sent_back_and_resubmitted(client: AsyncClient, setup: dict) -> None:
    await _record(client, setup, "offer_received", "offer_received_date")
    step = _step(await _journey(client, setup), "interview_prep")

    first = await client.post(
        _url(setup, f"/steps/{step['id']}/submissions"),
        json={"body_text": "I chose this course because…"},
        headers=setup["student_headers"],
    )
    assert first.status_code == 200, first.text
    assert _step(first.json(), "interview_prep")["waiting_on"] == "staff"

    again = await client.post(
        _url(setup, f"/steps/{step['id']}/submissions"), json={"body_text": "Again"}, headers=setup["student_headers"]
    )
    assert again.status_code == 409, "a round still under review cannot be replaced"

    round_one = _step(first.json(), "interview_prep")["submissions"][0]
    no_feedback = await client.post(
        _url(setup, f"/submissions/{round_one['id']}/review"),
        json={"verdict": "changes_requested"},
        headers=setup["staff"],
    )
    assert no_feedback.status_code == 422, "sending a round back needs feedback"

    sent_back = await client.post(
        _url(setup, f"/submissions/{round_one['id']}/review"),
        json={"verdict": "changes_requested", "feedback": "Say more about your career plan."},
        headers=setup["staff"],
    )
    assert sent_back.status_code == 200, sent_back.text
    assert _step(sent_back.json(), "interview_prep")["status"] == "current"

    second = await client.post(
        _url(setup, f"/steps/{step['id']}/submissions"),
        json={"body_text": "Better answers"},
        headers=setup["student_headers"],
    )
    rounds = _step(second.json(), "interview_prep")["submissions"]
    assert [r["round"] for r in rounds] == [1, 2]
    assert rounds[0]["feedback"] == "Say more about your career plan."

    verified = await client.post(
        _url(setup, f"/submissions/{rounds[1]['id']}/review"), json={"verdict": "verified"}, headers=setup["staff"]
    )
    assert _step(verified.json(), "interview_prep")["status"] == "completed"
    assert _step(verified.json(), "interview_recording")["status"] == "current"


async def test_the_recording_stage_takes_a_link_but_the_preparation_stage_does_not(
    client: AsyncClient, setup: dict
) -> None:
    await _record(client, setup, "offer_received", "offer_received_date")
    prep = _step(await _journey(client, setup), "interview_prep")
    refused = await client.post(
        _url(setup, f"/steps/{prep['id']}/submissions"),
        json={"external_url": "https://youtu.be/abc"},
        headers=setup["student_headers"],
    )
    assert refused.status_code == 400

    await _pass_review(client, setup, "interview_prep", body_text="answers")
    recording = _step(await _journey(client, setup), "interview_recording")
    plain_http = await client.post(
        _url(setup, f"/steps/{recording['id']}/submissions"),
        json={"external_url": "http://youtu.be/abc"},
        headers=setup["student_headers"],
    )
    assert plain_http.status_code == 422
    accepted = await client.post(
        _url(setup, f"/steps/{recording['id']}/submissions"),
        json={"external_url": "https://youtu.be/abc"},
        headers=setup["student_headers"],
    )
    assert accepted.status_code == 200, accepted.text


async def test_a_student_cannot_review_and_staff_cannot_submit(client: AsyncClient, setup: dict) -> None:
    await _record(client, setup, "offer_received", "offer_received_date")
    step = _step(await _journey(client, setup), "interview_prep")

    by_staff = await client.post(
        _url(setup, f"/steps/{step['id']}/submissions"), json={"body_text": "x"}, headers=setup["staff"]
    )
    assert by_staff.status_code == 403

    submitted = await client.post(
        _url(setup, f"/steps/{step['id']}/submissions"), json={"body_text": "x"}, headers=setup["student_headers"]
    )
    submission = _step(submitted.json(), "interview_prep")["submissions"][0]
    self_review = await client.post(
        _url(setup, f"/submissions/{submission['id']}/review"),
        json={"verdict": "verified"},
        headers=setup["student_headers"],
    )
    assert self_review.status_code == 403


async def test_a_stage_that_is_not_open_refuses_actions(client: AsyncClient, setup: dict) -> None:
    step = _step(await _journey(client, setup), "interview_prep")
    response = await client.post(
        _url(setup, f"/steps/{step['id']}/submissions"), json={"body_text": "early"}, headers=setup["student_headers"]
    )
    assert response.status_code == 409


# ── Booking ───────────────────────────────────────────────────────────────────


async def _reach_mock(client: AsyncClient, setup: dict) -> None:
    await _record(client, setup, "offer_received", "offer_received_date")
    await _pass_review(client, setup, "interview_prep", body_text="answers")
    await _pass_review(client, setup, "interview_recording", external_url="https://youtu.be/abc")


async def test_booking_a_slot_releases_the_others_and_makes_an_appointment(client: AsyncClient, setup: dict) -> None:
    await _reach_mock(client, setup)
    step = _step(await _journey(client, setup), "mock_interview")
    assert step["waiting_on"] == "staff", "nothing to book until slots are offered"

    published = await client.post(
        _url(setup, f"/steps/{step['id']}/slots"),
        json={
            "slots": [{"starts_at": _future(2), "meeting_link": "https://meet.example/x"}, {"starts_at": _future(5)}]
        },
        headers=setup["staff"],
    )
    slots = _step(published.json(), "mock_interview")["slots"]
    booked = await client.post(_url(setup, f"/slots/{slots[0]['id']}/book"), headers=setup["student_headers"])
    assert booked.status_code == 200, booked.text

    student_view = _step(booked.json(), "mock_interview")
    assert [s["status"] for s in student_view["slots"]] == ["booked"], "the released slot is hidden from the student"
    assert student_view["slots"][0]["appointment_id"] is not None
    assert student_view["waiting_on"] == "staff"

    twice = await client.post(_url(setup, f"/slots/{slots[1]['id']}/book"), headers=setup["student_headers"])
    assert twice.status_code == 409


async def test_a_reschedule_opens_a_new_attempt(client: AsyncClient, setup: dict) -> None:
    await _reach_mock(client, setup)
    after = await _book_and_decide(client, setup, "mock_interview", "reschedule")
    assert _step(after, "mock_interview")["status"] == "current"

    passed = await _book_and_decide(client, setup, "mock_interview", "passed")
    step = _step(passed, "mock_interview")
    assert step["status"] == "completed"
    assert {s["attempt"] for s in step["slots"]} == {1, 2}
    assert _step(passed, "suitability_interview")["status"] == "current"


async def test_failing_the_suitability_interview_ends_the_journey(client: AsyncClient, setup: dict) -> None:
    await _reach_mock(client, setup)
    await _book_and_decide(client, setup, "mock_interview", "passed")

    step = _step(await _journey(client, setup), "suitability_interview")
    published = await client.post(
        _url(setup, f"/steps/{step['id']}/slots"), json={"slots": [{"starts_at": _future(3)}]}, headers=setup["staff"]
    )
    slot = _step(published.json(), "suitability_interview")["slots"][0]
    await client.post(_url(setup, f"/slots/{slot['id']}/book"), headers=setup["student_headers"])
    no_reschedule = await client.post(
        _url(setup, f"/slots/{slot['id']}/outcome"), json={"outcome": "reschedule"}, headers=setup["staff"]
    )
    assert no_reschedule.status_code == 400

    failed = await client.post(
        _url(setup, f"/slots/{slot['id']}/outcome"),
        json={"outcome": "failed", "note": "No intake available"},
        headers=setup["staff"],
    )
    journey = failed.json()
    assert journey["status"] == "cancelled"
    assert _step(journey, "suitability_interview")["status"] == "failed"
    assert _step(journey, "cas_documents")["status"] == "cancelled"
    application = (await client.get(f"{APPLICATIONS}/{setup['application']['id']}", headers=setup["staff"])).json()
    assert application["status"] == "rejected"


# ── CAS and visa ──────────────────────────────────────────────────────────────


async def test_cas_then_visa_checklist_finishes_the_journey(client: AsyncClient, setup: dict) -> None:
    await _reach_mock(client, setup)
    await _book_and_decide(client, setup, "mock_interview", "passed")
    await _book_and_decide(client, setup, "suitability_interview", "passed")
    await _finish_documents_stage(client, setup, "cas_documents")
    await _finish_documents_stage(client, setup, "cas_shield")
    await _record(client, setup, "cas_received", "cas_received_date", cas_number="E4G123456")

    journey = await _journey(client, setup)
    visa = _step(journey, "visa")
    assert visa["status"] == "current"

    for task in visa["config"]["tasks"]:
        response = await client.put(
            _url(setup, f"/steps/{visa['id']}/tasks/{task['key']}"),
            json={"done": True},
            headers=setup["student_headers"],
        )
        assert response.status_code == 200, response.text

    journey = response.json()
    assert journey["status"] == "completed"
    assert journey["progress_percent"] == 100
    application = (await client.get(f"{APPLICATIONS}/{setup['application']['id']}", headers=setup["staff"])).json()
    assert application["status"] == "visa_processing"


# ── Scoping and switching ─────────────────────────────────────────────────────


async def test_nobody_else_reaches_the_journey(client: AsyncClient, setup: dict, user_factory, auth_headers) -> None:
    other_student = await user_factory(UserRole.STUDENT, email="j.other@example.com")
    other_counsellor = await user_factory(UserRole.COUNSELLOR, email="j.other.c@example.com")

    assert (await client.get(_url(setup), headers=await auth_headers(other_student))).status_code == 403
    assert (await client.get(_url(setup), headers=await auth_headers(other_counsellor))).status_code == 404


async def test_a_step_of_another_application_is_not_found(client: AsyncClient, setup: dict) -> None:
    other = await setup["new_application"](setup["bachelor"]["id"])
    foreign = (await client.get(f"{APPLICATIONS}/{other['id']}/journey", headers=setup["student_headers"])).json()
    foreign_step = foreign["steps"][0]["id"]
    response = await client.post(_url(setup, f"/steps/{foreign_step}/submit"), headers=setup["student_headers"])
    assert response.status_code == 404


async def test_switching_an_application_with_an_offer_lands_on_interview_preparation(
    client: AsyncClient, setup: dict
) -> None:
    standard = await client.post(
        "/api/v1/workflow-templates", json={"name": "Standard", "is_default": True}, headers=setup["admin"]
    )
    template_id = standard.json()["id"]
    await client.post(
        f"/api/v1/workflow-templates/{template_id}/stages", json={"key": "all", "name": "All"}, headers=setup["admin"]
    )
    moved_to_standard = await client.post(
        _url(setup, "/switch-template"), json={"template_id": template_id}, headers=setup["staff"]
    )
    assert moved_to_standard.status_code == 200, moved_to_standard.text
    await _record(client, setup, "offer_received", "offer_received_date")

    back = await client.post(_url(setup, "/switch-template"), json={}, headers=setup["staff"])
    assert back.status_code == 200, back.text
    journey = back.json()
    assert journey["template_name"] == "UK Student Journey"
    assert _step(journey, "offer")["status"] == "completed"
    assert _step(journey, "interview_prep")["status"] == "current"
