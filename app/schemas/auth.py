from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ..models.enums import Gender


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = Field(default="bearer")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


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

    email: EmailStr
    password: str = Field(min_length=8)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=20)
    date_of_birth: date | None = None
    gender: Gender | None = None


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
