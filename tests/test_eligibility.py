"""The public eligibility assessment, end to end.

Three properties carry most of the weight here.

**The verdict is the server's.** A student posts answers, not conclusions, and
nothing the browser claims about its own eligibility is stored.

**The public response is narrower than the record.** An anonymous caller may
create a lead; they may not read back the CRM key, the counsellor or the rules.

**It is one feature on top of the existing CRM, not a second one.** A submission
produces a lead that the existing lead endpoints can assign, progress, annotate
and follow up — so those are exercised against it rather than reimplemented.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.enums import UserRole
from app.services.eligibility_rules import ASSESSMENT_VERSION

pytestmark = pytest.mark.asyncio

SUBMIT = "/api/v1/public/eligibility"
STAFF = "/api/v1/eligibility-assessments"
LEADS = "/api/v1/leads"


def _submission(**overrides) -> dict:
    payload = {
        "education": {
            "highest_qualification": "plus_two",
            "subject": "Science",
            "grade": "78%",
            "institution": "Kathmandu Model College",
            "completion_year": 2025,
        },
        "english": {"evidence": "ielts", "overall_score": 6.5, "listening": 6.5, "reading": 6.0, "writing": 6.0, "speaking": 6.5},
        "course": {
            "study_level": "undergraduate",
            "preferred_course": "Computer Science",
            "preferred_location": "London",
            "preferred_universities": ["essex"],
        },
        "finance": {"funding_source": "family", "estimated_funds": 2500000},
        "documents": {
            "academic": "ready",
            "passport": "ready",
            "english": "ready",
            "financial": "in_progress",
            "personal": "ready",
        },
        "contact": {
            "full_name": "Anisha Gurung",
            "email": "anisha@example.com",
            "phone": "+9779800000000",
            "country": "Nepal",
            "preferred_contact_method": "whatsapp",
            "consent": True,
        },
        "source_page": "/resources/eligibility",
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


# ── Submitting ───────────────────────────────────────────────────────────────


async def test_an_anonymous_student_can_submit_and_a_lead_appears(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    response = await client.post(SUBMIT, json=_submission())
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["overall_status"] == "preliminary_likely_eligible"
    assert body["document_readiness"] == 90
    assert "counsellor" in body["summary"]

    admin = await user_factory(UserRole.ADMIN)
    headers = await auth_headers(admin)
    leads = await client.get(LEADS, params={"search": "anisha@example.com"}, headers=headers)
    assert leads.status_code == 200
    assert leads.json()["total"] == 1


async def test_the_public_response_hides_everything_internal(client: AsyncClient) -> None:
    """An anonymous caller gets a reference, a status and a sentence. Not the
    lead id, not the per-dimension indicators, and not the reasoning behind
    them."""
    body = (await client.post(SUBMIT, json=_submission())).json()
    assert set(body) == {"reference", "overall_status", "document_readiness", "summary"}


async def test_a_double_clicked_submit_creates_one_assessment(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    first = await client.post(SUBMIT, json=_submission())
    second = await client.post(SUBMIT, json=_submission())
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["reference"] == second.json()["reference"]

    headers = await auth_headers(await user_factory(UserRole.ADMIN))
    assert (await client.get(STAFF, headers=headers)).json()["total"] == 1


async def test_a_returning_student_does_not_get_a_second_lead(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """`leads.email` is unique where not null — a second lead for the same
    address would not merely be untidy, it would fail to insert."""
    await client.post(SUBMIT, json=_submission())
    again = await client.post(SUBMIT, json=_submission(english={"overall_score": 7.5}))
    assert again.status_code == 200, again.text

    headers = await auth_headers(await user_factory(UserRole.ADMIN))
    assert (await client.get(LEADS, params={"search": "anisha@example.com"}, headers=headers)).json()["total"] == 1
    # Two assessments, though: the second is a new submission, not an edit.
    assert (await client.get(STAFF, headers=headers)).json()["total"] == 2


async def test_submitting_without_consent_is_refused(client: AsyncClient) -> None:
    """A lead with no recorded consent is one nobody may lawfully ring."""
    response = await client.post(SUBMIT, json=_submission(contact={"consent": False}))
    assert response.status_code == 422


async def test_an_unreachable_phone_number_is_refused(client: AsyncClient) -> None:
    response = await client.post(SUBMIT, json=_submission(contact={"phone": "123"}))
    assert response.status_code == 422


async def test_scores_outside_any_real_scale_are_refused(client: AsyncClient) -> None:
    response = await client.post(SUBMIT, json=_submission(english={"overall_score": 900}))
    assert response.status_code == 422


async def test_staff_are_notified_when_an_assessment_arrives(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    headers = await auth_headers(counsellor)

    await client.post(SUBMIT, json=_submission())

    notifications = await client.get("/api/v1/notifications", headers=headers)
    assert notifications.status_code == 200, notifications.text
    titles = [item["title"] for item in notifications.json()["items"]]
    assert "New eligibility assessment received" in titles


# ── Reading, as staff ────────────────────────────────────────────────────────


async def test_a_counsellor_sees_the_whole_submission(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    await client.post(SUBMIT, json=_submission())
    headers = await auth_headers(await user_factory(UserRole.COUNSELLOR))

    listing = (await client.get(STAFF, headers=headers)).json()
    assert listing["total"] == 1
    row = listing["items"][0]
    assert row["contact"]["full_name"] == "Anisha Gurung"
    assert row["english_summary"] == "IELTS 6.5"
    assert row["lead_status"] == "new"

    detail = await client.get(f"{STAFF}/{row['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["education"]["institution"] == "Kathmandu Model College"
    assert body["finance"]["funding_source"] == "family"
    assert body["assessment_version"] == ASSESSMENT_VERSION
    assert body["assessment_notes"]


async def test_marketing_cannot_read_assessments(client: AsyncClient, user_factory, auth_headers) -> None:
    """Marketing works the top of the funnel, but an assessment carries a named
    person's grades, finances and passport readiness."""
    await client.post(SUBMIT, json=_submission())
    headers = await auth_headers(await user_factory(UserRole.MARKETING))
    assert (await client.get(STAFF, headers=headers)).status_code == 403


async def test_anonymous_callers_cannot_read_assessments(client: AsyncClient) -> None:
    await client.post(SUBMIT, json=_submission())
    assert (await client.get(STAFF)).status_code == 401


async def test_staff_can_search_and_filter(client: AsyncClient, user_factory, auth_headers) -> None:
    await client.post(SUBMIT, json=_submission())
    await client.post(
        SUBMIT,
        json=_submission(
            contact={"full_name": "Bikash Shrestha", "email": "bikash@example.com", "phone": "+9779811111111"},
            course={"preferred_course": "Nursing", "study_level": "postgraduate"},
            education={"highest_qualification": "bachelors"},
        ),
    )
    headers = await auth_headers(await user_factory(UserRole.ADMIN))

    assert (await client.get(STAFF, params={"search": "nursing"}, headers=headers)).json()["total"] == 1
    assert (await client.get(STAFF, params={"study_level": "postgraduate"}, headers=headers)).json()["total"] == 1
    assert (await client.get(STAFF, params={"unassigned": True}, headers=headers)).json()["total"] == 2


async def test_the_existing_lead_workflow_drives_the_assessment(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    """The point of attaching to `Lead`: assigning, progressing and annotating
    an assessment are the CRM's existing endpoints, and the assessment list
    reflects them without a second status system."""
    await client.post(SUBMIT, json=_submission())
    admin = await user_factory(UserRole.ADMIN)
    counsellor = await user_factory(UserRole.COUNSELLOR)
    headers = await auth_headers(admin)

    row = (await client.get(STAFF, headers=headers)).json()["items"][0]
    lead_id = row["lead_id"]

    assigned = await client.post(
        f"{LEADS}/{lead_id}/assign", json={"assigned_to": str(counsellor.id)}, headers=headers
    )
    assert assigned.status_code == 200, assigned.text

    progressed = await client.post(f"{LEADS}/{lead_id}/status", json={"status": "contacted"}, headers=headers)
    assert progressed.status_code == 200, progressed.text

    updated = (await client.get(STAFF, headers=headers)).json()["items"][0]
    assert updated["lead_status"] == "contacted"
    assert updated["assigned_to"] == str(counsellor.id)
    assert updated["assigned_to_name"]

    # And the submission is on the lead's own timeline, so a counsellor who
    # opens the lead sees it without knowing this feature exists.
    activities = (await client.get(f"{LEADS}/{lead_id}/activities", headers=headers)).json()
    assert any(entry["title"] == "Eligibility assessment submitted" for entry in activities)


async def test_the_queue_summary_counts_what_the_strip_shows(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    await client.post(SUBMIT, json=_submission())
    headers = await auth_headers(await user_factory(UserRole.ADMIN))

    stats = (await client.get(f"{STAFF}/stats", headers=headers)).json()
    assert stats["total"] == 1
    assert stats["likely_eligible"] == 1
    assert stats["new"] == 1
    assert stats["unassigned"] == 1
