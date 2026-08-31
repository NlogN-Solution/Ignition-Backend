from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from ..models.enums import PaymentMethod


class AccessFeeRead(BaseModel):
    amount: float
    currency: str
    #: The country the price was matched to, when one was matched.
    country_name: str | None
    #: True when this is the fallback price rather than one set for the
    #: student's own country.
    is_default: bool


class PortalAccessRead(BaseModel):
    has_access: bool
    fee: AccessFeeRead | None
    paid_at: datetime | None
    payment_id: UUID | None
    #: Methods offered by the self-service flow. Cash and bank transfer are
    #: taken in the office and recorded by staff instead.
    methods: list[PaymentMethod]
    #: True while the checkout is simulated rather than talking to a real
    #: gateway. The portal renders an unmissable notice when this is set, so a
    #: student is never led to believe money has moved.
    simulated: bool


class PortalAccessCheckout(BaseModel):
    """Only the method. The amount is Ignition's to set, never the client's."""

    payment_method: PaymentMethod
