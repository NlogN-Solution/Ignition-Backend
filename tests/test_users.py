"""User management endpoints.

The focus is the authorisation boundary rather than CRUD mechanics: who may
create, see, and modify whom. `can_manage_target` is the only thing standing
between an `admin` account and a privilege escalation now that ED360's
`is_platform_admin` bypass is gone.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.enums import UserRole, UserStatus

pytestmark = pytest.mark.asyncio

USERS = "/api/v1/users"
STAFF_DIRECTORY = f"{USERS}/staff-directory"


def _new_user(**overrides) -> dict:
    payload = {
        "email": "new.hire@example.com",
        "password": "a-strong-password",
        "first_name": "New",
        "last_name": "Hire",
        "role": UserRole.COUNSELLOR.value,
    }
    payload.update(overrides)
    return payload


# ── Staff-only access ─────────────────────────────────────────────────────────


async def test_students_cannot_list_users(client: AsyncClient, user_factory, auth_headers) -> None:
    """Students are real users of this API — a valid token must not be enough
    to reach a staff endpoint."""
    student = await user_factory(UserRole.STUDENT)
    response = await client.get(USERS, headers=await auth_headers(student))
    assert response.status_code == 403


async def test_admin_can_list_users(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    await user_factory(UserRole.STUDENT)

    response = await client.get(USERS, headers=await auth_headers(admin))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 2
    assert {item["email"] for item in body["items"]} >= {admin.email}


async def test_counsellors_only_see_students(client: AsyncClient, user_factory, auth_headers) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    await user_factory(UserRole.STUDENT)
    await user_factory(UserRole.FINANCE)

    response = await client.get(USERS, headers=await auth_headers(counsellor))
    assert response.status_code == 200
    roles = {item["role"] for item in response.json()["items"]}
    assert roles == {UserRole.STUDENT.value}


async def test_a_counsellor_cannot_widen_the_filter_via_query_string(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    await user_factory(UserRole.ADMIN)

    response = await client.get(
        USERS,
        params={"role": UserRole.ADMIN.value, "deleted": "true"},
        headers=await auth_headers(counsellor),
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


# ── Creation and the escalation boundary ──────────────────────────────────────


async def test_admin_creates_a_staff_account(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    response = await client.post(USERS, json=_new_user(), headers=await auth_headers(admin))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["role"] == UserRole.COUNSELLOR.value
    assert body["email"] == "new.hire@example.com"


async def test_an_admin_cannot_create_a_super_admin(client: AsyncClient, user_factory, auth_headers) -> None:
    """`can_manage_target` requires a strictly higher rank than the role being
    assigned, so admin (3) cannot mint super_admin (4)."""
    admin = await user_factory(UserRole.ADMIN)
    response = await client.post(
        USERS,
        json=_new_user(role=UserRole.SUPER_ADMIN.value),
        headers=await auth_headers(admin),
    )
    assert response.status_code == 403


async def test_a_super_admin_can_create_an_admin(client: AsyncClient, user_factory, auth_headers) -> None:
    owner = await user_factory(UserRole.SUPER_ADMIN)
    response = await client.post(
        USERS,
        json=_new_user(role=UserRole.ADMIN.value),
        headers=await auth_headers(owner),
    )
    assert response.status_code == 200, response.text


async def test_counsellors_cannot_create_users(client: AsyncClient, user_factory, auth_headers) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    response = await client.post(USERS, json=_new_user(), headers=await auth_headers(counsellor))
    assert response.status_code == 403


async def test_create_rejects_a_duplicate_email(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    existing = await user_factory(UserRole.STUDENT)
    response = await client.post(
        USERS,
        json=_new_user(email=existing.email),
        headers=await auth_headers(admin),
    )
    assert response.status_code == 400


# ── Updates ───────────────────────────────────────────────────────────────────


async def test_an_admin_cannot_promote_anyone_to_super_admin(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    admin = await user_factory(UserRole.ADMIN)
    target = await user_factory(UserRole.COUNSELLOR)

    response = await client.patch(
        f"{USERS}/{target.id}",
        json={"role": UserRole.SUPER_ADMIN.value},
        headers=await auth_headers(admin),
    )
    assert response.status_code == 403


async def test_an_admin_cannot_modify_a_super_admin(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    owner = await user_factory(UserRole.SUPER_ADMIN)

    response = await client.patch(
        f"{USERS}/{owner.id}",
        json={"first_name": "Renamed"},
        headers=await auth_headers(admin),
    )
    assert response.status_code == 403


async def test_a_counsellor_cannot_change_a_students_role_or_status(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    student = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(counsellor)

    assert (
        await client.patch(f"{USERS}/{student.id}", json={"role": UserRole.ADMIN.value}, headers=headers)
    ).status_code == 403
    assert (
        await client.patch(
            f"{USERS}/{student.id}",
            json={"status": UserStatus.SUSPENDED.value},
            headers=headers,
        )
    ).status_code == 403

    # ...but ordinary profile edits are exactly what the role is for.
    allowed = await client.patch(f"{USERS}/{student.id}", json={"first_name": "Ada"}, headers=headers)
    assert allowed.status_code == 200
    assert allowed.json()["first_name"] == "Ada"


async def test_self_update_cannot_change_role(client: AsyncClient, user_factory, auth_headers) -> None:
    """`UserSelfUpdate` forbids extra fields, so this is a 422 rather than a
    silently ignored field."""
    student = await user_factory(UserRole.STUDENT)
    response = await client.patch(
        f"{USERS}/me",
        json={"first_name": "Ada", "role": UserRole.SUPER_ADMIN.value},
        headers=await auth_headers(student),
    )
    assert response.status_code == 422


async def test_self_update_changes_own_profile(client: AsyncClient, user_factory, auth_headers) -> None:
    student = await user_factory(UserRole.STUDENT)
    response = await client.patch(
        f"{USERS}/me",
        json={"first_name": "Ada", "bio": "Prospective applicant"},
        headers=await auth_headers(student),
    )
    assert response.status_code == 200
    assert response.json()["first_name"] == "Ada"


# ── Deactivation ──────────────────────────────────────────────────────────────


async def test_admin_deactivates_and_restores_a_user(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    target = await user_factory(UserRole.STUDENT)
    headers = await auth_headers(admin)

    assert (await client.delete(f"{USERS}/{target.id}", headers=headers)).status_code == 200
    # Soft-deleted users drop out of the default listing and lookups.
    assert (await client.get(f"{USERS}/{target.id}", headers=headers)).status_code == 404

    assert (await client.post(f"{USERS}/{target.id}/restore", headers=headers)).status_code == 200
    assert (await client.get(f"{USERS}/{target.id}", headers=headers)).status_code == 200


async def test_an_admin_cannot_deactivate_themselves(client: AsyncClient, user_factory, auth_headers) -> None:
    admin = await user_factory(UserRole.ADMIN)
    response = await client.delete(f"{USERS}/{admin.id}", headers=await auth_headers(admin))
    assert response.status_code == 400


# ── Password reset ────────────────────────────────────────────────────────────


async def test_reset_password_revokes_existing_sessions(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    """An admin resets a password precisely when an account may be compromised;
    the old refresh token must not survive it."""
    admin = await user_factory(UserRole.ADMIN)
    target = await user_factory(UserRole.STUDENT)

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": target.email, "password": "correct-horse-battery"},
    )
    stolen_refresh_token = login.json()["refresh_token"]

    reset = await client.post(
        f"{USERS}/{target.id}/reset-password",
        json={"new_password": None},
        headers=await auth_headers(admin),
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["generated_password"]

    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen_refresh_token})
    assert replay.status_code == 401


async def test_enable_portal_only_works_for_students_without_a_password(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    headers = await auth_headers(counsellor)

    no_portal = await user_factory(UserRole.STUDENT, password=None)
    enabled = await client.post(f"{USERS}/{no_portal.id}/enable-portal", headers=headers)
    assert enabled.status_code == 200
    assert enabled.json()["generated_password"]

    # Not a back door into resetting an account someone already uses.
    already_has_login = await user_factory(UserRole.STUDENT)
    assert (await client.post(f"{USERS}/{already_has_login.id}/enable-portal", headers=headers)).status_code == 400

    staff = await user_factory(UserRole.FINANCE, password=None)
    assert (await client.post(f"{USERS}/{staff.id}/enable-portal", headers=headers)).status_code == 403


# ── Staff directory ───────────────────────────────────────────────────────────


async def test_staff_directory_excludes_students_and_contact_details(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    counsellor = await user_factory(UserRole.COUNSELLOR)
    await user_factory(UserRole.STUDENT)

    response = await client.get(STAFF_DIRECTORY, headers=await auth_headers(counsellor))
    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    assert all(item["role"] != UserRole.STUDENT.value for item in items)
    assert all("email" not in item and "phone" not in item for item in items)


async def test_a_student_may_resolve_one_staff_id_but_not_browse(
    client: AsyncClient,
    user_factory,
    auth_headers,
) -> None:
    student = await user_factory(UserRole.STUDENT)
    counsellor = await user_factory(UserRole.COUNSELLOR)
    headers = await auth_headers(student)

    assert (await client.get(STAFF_DIRECTORY, headers=headers)).status_code == 403

    single = await client.get(STAFF_DIRECTORY, params={"user_id": str(counsellor.id)}, headers=headers)
    assert single.status_code == 200
    assert [item["id"] for item in single.json()["items"]] == [str(counsellor.id)]
