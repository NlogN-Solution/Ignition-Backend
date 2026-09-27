"""Student-facing fixes from the 2026-09 feedback round.

* Self-registration enforces the password policy the sign-up form shows.
* Profile completion reaches 100% only when every field and section is filled,
  and says what is missing.
* A self-registered student's lead activity carries the account's own
  creation time, to the second.
* "Computer Science" from onboarding is read as the Computing subject.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from httpx import AsyncClient

from app.models.enums import CourseSubject, UserRole
from app.services.recommendation_service import SUBJECT_ALIASES

pytestmark = pytest.mark.asyncio

API = "/api/v1"
REGISTER = f"{API}/auth/register"


def _registration(**overrides) -> dict:
    body = {
        "email": "policy.student@example.com",
        "password": "Str0ng-enough!",
        "first_name": "Asha",
        "last_name": "Gurung",
        "phone": "9812345678",
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize(
    ("password", "missing"),
    [
        ("alllowercase1!", "an uppercase letter"),
        ("ALLUPPERCASE1!", "a lowercase letter"),
        ("NoNumbersHere!", "a number"),
        ("NoSymbols123", "a symbol"),
    ],
)
async def test_registration_rejects_a_password_missing_a_rule(client: AsyncClient, password, missing) -> None:
    response = await client.post(REGISTER, json=_registration(password=password))
    assert response.status_code == 422, response.text
    assert missing in response.text


async def test_registration_rejects_a_short_password(client: AsyncClient) -> None:
    response = await client.post(REGISTER, json=_registration(password="Ab1!"))
    assert response.status_code == 422


async def test_registration_accepts_a_password_meeting_every_rule(client: AsyncClient) -> None:
    response = await client.post(REGISTER, json=_registration())
    assert response.status_code == 200, response.text


async def test_signup_activity_is_stamped_with_the_account_creation_time(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    registered = await client.post(REGISTER, json=_registration(email="stamp.student@example.com"))
    assert registered.status_code == 200, registered.text
    student_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    me = (await client.get(f"{API}/auth/me", headers=student_headers)).json()

    admin = await user_factory(UserRole.ADMIN, email="stamp.admin@example.com")
    admin_headers = await auth_headers(admin)
    lead = (await client.get(f"{API}/leads", params={"search": "stamp.student@example.com"}, headers=admin_headers)).json()[
        "items"
    ][0]
    activities = (await client.get(f"{API}/leads/{lead['id']}/activities", headers=admin_headers)).json()
    signup = next(a for a in activities if a["title"] == "Signed up on the portal")

    assert datetime.fromisoformat(signup["created_at"]) == datetime.fromisoformat(me["created_at"])


async def test_profile_completion_is_100_only_when_everything_is_filled(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    student = await user_factory(UserRole.STUDENT, email="complete.student@example.com", phone="9800000001")
    headers = await auth_headers(student)

    partial = await client.patch(
        f"{API}/student/me/profile",
        json={"education_level": "bachelor", "nationality": "Nepali", "passport_number": "PA123"},
        headers=headers,
    )
    assert partial.status_code == 200, partial.text
    body = partial.json()
    assert body["profile_completion"] < 100
    assert "Date of birth" in body["profile_missing"]
    assert "At least one education entry" in body["profile_missing"]
    assert "At least one work experience entry" in body["profile_missing"]
    assert "At least one test score" in body["profile_missing"]

    account = await client.patch(
        f"{API}/users/me", json={"date_of_birth": "2001-04-05", "gender": "female"}, headers=headers
    )
    assert account.status_code == 200, account.text

    full = await client.patch(
        f"{API}/student/me/profile",
        json={
            "citizenship_number": "12-34",
            "birth_place": "Pokhara",
            "father_name": "Ram",
            "mother_name": "Sita",
            "emergency_contact_name": "Ram",
            "emergency_contact_phone": "9800000002",
            "current_address": "Kathmandu",
            "permanent_address": "Pokhara",
            "university_name": "Tribhuvan University",
            "graduation_year": 2023,
            "gpa": 3.6,
            "preferred_country": "United Kingdom",
            "preferred_program": "Computer Science",
            "preferred_intake": "September 2026",
            "budget": 20000,
            "test_scores": {"language": {"IELTS": {"selected": True, "score": "7.0", "date": "2026-01-10"}}},
        },
        headers=headers,
    )
    assert full.status_code == 200, full.text
    # Every field is filled but the two repeatable sections are still empty.
    assert full.json()["profile_missing"] == ["At least one education entry", "At least one work experience entry"]
    assert full.json()["profile_completion"] < 100

    education = await client.post(
        f"{API}/users/{student.id}/education", json={"institution_name": "Tribhuvan University"}, headers=headers
    )
    assert education.status_code in (200, 201), education.text
    experience = await client.post(
        f"{API}/users/{student.id}/experience",
        json={"company_name": "Ignition", "job_title": "Intern"},
        headers=headers,
    )
    assert experience.status_code in (200, 201), experience.text

    done = (await client.get(f"{API}/student/me/profile", headers=headers)).json()
    assert done["profile_missing"] == []
    assert done["profile_completion"] == 100


async def test_an_unselected_test_does_not_count_as_a_score(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT, email="unticked.student@example.com")
    headers = await auth_headers(student)
    body = (
        await client.patch(
            f"{API}/student/me/profile",
            json={
                "education_level": "bachelor",
                "test_scores": {"language": {"IELTS": {"selected": False, "score": "7.0"}}},
            },
            headers=headers,
        )
    ).json()
    assert "At least one test score" in body["profile_missing"]


def test_computer_science_is_read_as_the_computing_subject() -> None:
    assert SUBJECT_ALIASES["computer science"] is CourseSubject.COMPUTING


async def test_lead_assignment_activity_names_the_counsellor(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN, email="assign.admin@example.com")
    counsellor = await user_factory(UserRole.COUNSELLOR, email="assign.counsellor@example.com")
    headers = await auth_headers(admin)
    lead = (
        await client.post(
            f"{API}/leads",
            json={"first_name": "Mina", "last_name": "Rai", "phone": "9800000009", "email": "mina@example.com"},
            headers=headers,
        )
    ).json()
    assigned = await client.post(
        f"{API}/leads/{lead['id']}/assign", json={"assigned_to": str(counsellor.id)}, headers=headers
    )
    assert assigned.status_code == 200, assigned.text

    activities = (await client.get(f"{API}/leads/{lead['id']}/activities", headers=headers)).json()
    entry = next(a for a in activities if a["title"] == "Lead assigned")
    assert entry["description"] == f"Lead assigned to {counsellor.first_name} {counsellor.last_name}."
    assert str(counsellor.id) not in entry["description"]


async def test_an_appointment_request_notifies_the_student_and_the_desk(
    client: AsyncClient, user_factory, auth_headers
) -> None:
    student = await user_factory(UserRole.STUDENT, email="appt.student@example.com")
    frontdesk = await user_factory(UserRole.FRONTDESK, email="appt.frontdesk@example.com")
    student_headers = await auth_headers(student)

    response = await client.post(
        f"{API}/student/me/appointments/request",
        json={"title": "Course advice", "preferred_date": "2027-04-01", "appointment_type": "consultation"},
        headers=student_headers,
    )
    assert response.status_code == 200, response.text

    mine = (await client.get(f"{API}/student/me/notifications", headers=student_headers)).json()["items"]
    assert any(n["title"] == "Appointment requested" for n in mine)

    desk = (await client.get(f"{API}/notifications", headers=await auth_headers(frontdesk))).json()["items"]
    request = next(n for n in desk if n["title"] == "New appointment request")
    assert "Course advice" in request["message"]
    assert student.first_name in request["message"]


async def test_staff_can_mark_every_notification_read(client: AsyncClient, user_factory, auth_headers, session) -> None:
    from app.models import Notification
    from app.models.enums import NotificationType

    admin = await user_factory(UserRole.ADMIN, email="bell.admin@example.com")
    other = await user_factory(UserRole.ADMIN, email="bell.other@example.com")
    for owner in (admin, admin, other):
        session.add(Notification(user_id=owner.id, type=NotificationType.LEAD, title="t", message="m"))
    await session.commit()
    headers = await auth_headers(admin)

    response = await client.post(f"{API}/notifications/read-all", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 2

    unread = (await client.get(f"{API}/notifications", params={"is_read": False}, headers=headers)).json()
    assert unread["total"] == 0
    # Someone else's notifications are untouched.
    others = (
        await client.get(f"{API}/notifications", params={"is_read": False}, headers=await auth_headers(other))
    ).json()
    assert others["total"] == 1


async def test_article_body_is_sanitised_and_served_publicly(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN, email="writer.admin@example.com")
    headers = await auth_headers(admin)
    body = (
        "<h2>Costs</h2><p onclick='steal()'>Plan <strong>early</strong>.</p>"
        "<script>alert(1)</script><img src='javascript:alert(1)'>"
        "<figure class='image'><img src='https://cdn.example.com/a.png' alt='A'></figure>"
        "<p>" + "word " * 440 + "</p>"
    )
    created = await client.post(
        f"{API}/content-pages",
        json={
            "key": "post.plan-early",
            "kind": "post",
            "slug": "plan-early",
            "title": "Plan early",
            "excerpt": "Why.",
            "body_html": body,
            "cover_image_url": "https://cdn.example.com/cover.png",
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    page = created.json()
    assert "<script" not in page["body_html"]
    assert "onclick" not in page["body_html"]
    assert "javascript:" not in page["body_html"]
    assert '<img src="https://cdn.example.com/a.png" alt="A">' in page["body_html"]
    assert page["reading_minutes"] == 2

    published = await client.post(
        f"{API}/content-pages/{page['id']}/publish", json={"is_published": True}, headers=headers
    )
    assert published.status_code == 200, published.text

    public = (await client.get(f"{API}/public/content/post.plan-early")).json()
    assert public["body_html"] == page["body_html"]
    assert public["cover_image_url"] == "https://cdn.example.com/cover.png"
    index = (await client.get(f"{API}/public/content", params={"kind": "post"})).json()
    assert index["items"][0]["cover_image_url"] == "https://cdn.example.com/cover.png"
    assert "body_html" not in index["items"][0]


async def test_two_articles_cannot_share_a_slug(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN, email="slug.admin@example.com")
    headers = await auth_headers(admin)
    first = await client.post(
        f"{API}/content-pages",
        json={"key": "post.one", "kind": "post", "slug": "same", "title": "One"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    clash = await client.post(
        f"{API}/content-pages",
        json={"key": "post.two", "kind": "post", "slug": "same", "title": "Two"},
        headers=headers,
    )
    assert clash.status_code == 409
    # A guide may reuse it: guides and articles live under different paths.
    guide = await client.post(
        f"{API}/content-pages",
        json={"key": "guide.same", "kind": "guide", "slug": "same", "title": "Guide"},
        headers=headers,
    )
    assert guide.status_code == 200, guide.text
    second = await client.post(
        f"{API}/content-pages",
        json={"key": "post.two", "kind": "post", "slug": "other", "title": "Two"},
        headers=headers,
    )
    renamed = await client.patch(f"{API}/content-pages/{second.json()['id']}", json={"slug": "same"}, headers=headers)
    assert renamed.status_code == 409
