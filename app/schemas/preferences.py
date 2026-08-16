from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DashboardSettingsRead(BaseModel):
    preferred_currency: str | None = None
    email_notifications_enabled: bool
    push_notifications_enabled: bool
    show_points_widget: bool

    model_config = ConfigDict(from_attributes=True)


class DashboardSettingsUpdate(BaseModel):
    preferred_currency: str | None = None
    email_notifications_enabled: bool | None = None
    push_notifications_enabled: bool | None = None
    show_points_widget: bool | None = None
