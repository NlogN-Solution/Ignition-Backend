from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models.enums import Gender
from .email import NormalizedEmail


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = Field(default="bearer")


class LoginRequest(BaseModel):
    email: NormalizedEmail
    password: str = Field(min_length=8)


#: Student self-signup password policy. The portal's registration form shows
#: the same four rules as a live checklist (student-frontend
#: src/lib/passwordPolicy.js); this is the copy a client cannot skip. Staff
#: passwords are admin-issued and changed through their own endpoints, which
#: keep the plain length rule.
_PASSWORD_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[A-Z]"), "an uppercase letter"),
    (re.compile(r"[a-z]"), "a lowercase letter"),
    (re.compile(r"\d"), "a number"),
    (re.compile(r"[^A-Za-z0-9\s]"), "a symbol"),
)


def validate_password_policy(password: str) -> str:
    missing = [label for pattern, label in _PASSWORD_RULES if not pattern.search(password)]
    if missing:
        raise ValueError(f"Password must include {', '.join(missing)}.")
    return password


class PublicRegisterRequest(BaseModel):
    """Body of `POST /auth/register`.

    ED360's `RegisterRequest` carries `role` and `status` and passes both
    straight to the `User` constructor, so `{"role": "super_admin"}` in an
    unauthenticated request body creates a super admin. That is the single worst
    bug in the port's source material, and it matters more here because this
    endpoint is public self-signup for the student portal.

    The fix is structural rather than a validation rule: the fields do not exist
    on this model, and `extra="forbid"` rejects a request that sends them
    instead of ignoring it — a client attempting escalation gets a 422, not a
    silently downgraded account. The server always assigns STUDENT/ACTIVE.

    Staff accounts are created only through authenticated `POST /users`, which
    is gated by `can_manage_target`.
    """

    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    password: str = Field(min_length=8)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    #: Long enough to be dialled. The ceiling alone was not enough: a
    #: registration writing `phone="abc"` creates a Lead with that number
    #: (`core/subscribers.link_or_create_lead_for_student`), and `LeadBase`
    #: requires seven characters — so a three-character phone accepted here
    #: produced a lead the staff console could not list. The two ends of that
    #: chain now agree.
    phone: str | None = Field(default=None, min_length=7, max_length=20)
    date_of_birth: date | None = None
    gender: Gender | None = None

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        return validate_password_policy(value)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    """Logout revokes a specific session, so it needs the token being retired.

    Optional: without it the current refresh token cannot be identified (the
    access token does not carry the session), and logout falls back to revoking
    every session for the user.
    """

    refresh_token: str | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
