"""Who may use the student portal, and what it costs them.

Ignition's portal is not free: a student pays a one-time access fee, priced by
the country they are applying from, before the application workflow opens to
them. Everything that decides that lives here so there is exactly one answer to
"has this student paid?" — the route guard, the student's own screen and the
staff console all ask this module rather than re-deriving it from payment rows.

Research and the whole public platform stay free. So do the parts of the portal
a student needs *in order to* pay: their account, their profile, and this fee
itself. See `require_paid_portal_access` for where the line is drawn.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.deps import get_db_session
from ..models import Payment, PortalAccessFee, StudentProfile
from ..models.academic import Country
from ..models.enums import PaymentMethod, PaymentPurpose, PaymentStatus

#: Methods a student may choose for the access fee. Cash and bank transfer are
#: deliberately absent: those are taken in the office by staff, who record them
#: through the normal payments screen, not through this self-service flow.
SELF_SERVICE_METHODS = (
    PaymentMethod.ESewa,
    PaymentMethod.KHALTI,
    PaymentMethod.CREDIT_CARD,
    PaymentMethod.DEBIT_CARD,
)


@dataclass(frozen=True)
class AccessFee:
    amount: float
    currency: str
    country_name: str | None
    #: True when this is the fallback price rather than one set for the
    #: student's own country — the UI says so rather than implying precision.
    is_default: bool


@dataclass(frozen=True)
class AccessState:
    has_access: bool
    fee: AccessFee | None
    paid_at: datetime | None
    payment_id: uuid.UUID | None


class PortalAccessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _country_for(self, student_id: uuid.UUID) -> Country | None:
        """The student's country, taken from the nationality on their profile.

        `StudentProfile.nationality` is free text, so it is matched
        case-insensitively against the catalogue's country names. No match
        means the fallback price applies — a student is never blocked from
        paying because their nationality was typed unexpectedly.
        """
        nationality = await self.session.scalar(
            select(StudentProfile.nationality).where(StudentProfile.user_id == student_id)
        )
        if not nationality:
            return None

        return await self.session.scalar(
            select(Country).where(Country.name.ilike(nationality.strip()))
        )

    async def fee_for(self, student_id: uuid.UUID) -> AccessFee | None:
        country = await self._country_for(student_id)

        fee = None
        if country is not None:
            fee = await self.session.scalar(
                select(PortalAccessFee).where(
                    PortalAccessFee.country_id == country.id,
                    PortalAccessFee.is_active.is_(True),
                )
            )

        if fee is not None:
            return AccessFee(
                amount=float(fee.amount),
                currency=fee.currency,
                country_name=country.name if country else None,
                is_default=False,
            )

        fallback = await self.session.scalar(
            select(PortalAccessFee).where(
                PortalAccessFee.country_id.is_(None),
                PortalAccessFee.is_active.is_(True),
            )
        )
        if fallback is None:
            # No price list configured at all. Callers treat this as "cannot
            # quote", which is a louder and more honest failure than inventing
            # a number.
            return None

        return AccessFee(
            amount=float(fallback.amount),
            currency=fallback.currency,
            country_name=country.name if country else None,
            is_default=True,
        )

    async def _access_payment(self, student_id: uuid.UUID) -> Payment | None:
        return await self.session.scalar(
            select(Payment)
            .where(
                Payment.student_id == student_id,
                Payment.purpose == PaymentPurpose.PORTAL_ACCESS,
                Payment.status == PaymentStatus.COMPLETED,
            )
            .order_by(Payment.created_at.asc())
        )

    async def has_access(self, student_id: uuid.UUID) -> bool:
        return await self._access_payment(student_id) is not None

    async def state_for(self, student_id: uuid.UUID) -> AccessState:
        payment = await self._access_payment(student_id)
        if payment is not None:
            return AccessState(
                has_access=True,
                fee=AccessFee(
                    amount=float(payment.amount),
                    currency=payment.currency,
                    country_name=None,
                    is_default=False,
                ),
                paid_at=payment.payment_date or payment.created_at,
                payment_id=payment.id,
            )

        return AccessState(
            has_access=False,
            fee=await self.fee_for(student_id),
            paid_at=None,
            payment_id=None,
        )

    async def record_access_payment(
        self,
        student_id: uuid.UUID,
        method: PaymentMethod,
        fee: AccessFee,
        *,
        transaction_reference: str,
        remarks: str,
    ) -> Payment:
        """Writes the completed access payment that opens the portal.

        Deliberately takes the amount from `fee` rather than from the caller:
        the price is Ignition's to set, and a client that could name its own
        amount could buy access for one rupee.
        """
        payment = Payment(
            student_id=student_id,
            purpose=PaymentPurpose.PORTAL_ACCESS,
            amount=fee.amount,
            currency=fee.currency,
            payment_method=method,
            status=PaymentStatus.COMPLETED,
            transaction_reference=transaction_reference,
            payment_date=datetime.now(timezone.utc),
            remarks=remarks,
        )
        self.session.add(payment)
        await self.session.commit()
        await self.session.refresh(payment)
        return payment


async def get_portal_access_service(
    session: AsyncSession = Depends(get_db_session),
) -> PortalAccessService:
    return PortalAccessService(session)
