from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ..models.enums import Gender, UserRole, UserStatus


class UserBase(BaseModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    role: UserRole
    status: UserStatus
    phone: str | None = None
    date_of_birth: date | None = None
    gender: Gender | None = None


class UserCreate(BaseModel):
    """Staff-created account. `role` is honoured here — unlike public register —
    but only after `can_manage_target` clears the caller for that role.

    Omitting `password` mints a temporary one and returns it once, which is the
    lead-conversion path: a student record exists before portal access does.
    """

    email: EmailStr
    password: str | None = Field(default=None, min_length=8)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    role: UserRole
    status: UserStatus = UserStatus.ACTIVE
    phone: str | None = Field(default=None, max_length=20)


class UserUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    bio: str | None = None
    date_of_birth: date | None = None
    gender: Gender | None = None
    status: UserStatus | None = None
    role: UserRole | None = None


class UserSelfUpdate(BaseModel):
    """What a user may change about themselves.

    `role` and `status` are absent by design, and `extra="forbid"` makes sending
    them a 422 rather than a no-op — self-service must never be a privilege
    escalation path.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    bio: str | None = None
    date_of_birth: date | None = None
    gender: Gender | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str | None = Field(default=None, min_length=8)


class ResetPasswordResponse(BaseModel):
    user_id: UUID
    generated_password: str


class UserRead(UserBase):
    """ED360's version exposes `organization_id` and `is_platform_admin`; both
    are gone with the tenancy strip (R1/R3).

    `email` is re-declared as a plain `str`, overriding `UserBase.EmailStr`.
    Validation belongs on the way *in* — `UserCreate` and `PublicRegisterRequest`
    still use `EmailStr`, so nothing unparseable gets stored through the API.
    Validating on the way *out* means a single row the validator dislikes (a
    legacy import, a migrated address, a seeded `.test` domain) raises inside
    the list comprehension and returns 500 for the whole page rather than for
    that one row. One bad record should not hide the other two hundred.
    """

    email: str
    id: UUID
    avatar_url: str | None = None
    bio: str | None = None
    must_change_password: bool = False
    has_portal_access: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class UserList(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    limit: int


class StaffDirectoryEntry(BaseModel):
    """Name + role only — no email, phone, or profile fields.

    Lets any staff role pick a colleague as an appointment attendee without
    exposing the full staff-management surface that `GET /users` reserves for
    admin/super_admin.
    """

    id: UUID
    first_name: str
    last_name: str
    role: UserRole

    model_config = ConfigDict(from_attributes=True)


class StaffDirectoryList(BaseModel):
    items: list[StaffDirectoryEntry]
