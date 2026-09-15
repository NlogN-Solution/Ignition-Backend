"""Why a visitor came to register.

A student browsing the public site finds MSc Computer Science at Coventry,
presses Apply Now, and is sent to the portal to make an account. Until now
everything about *which course* was dropped at that boundary: the public
Apply button was one fixed link to `/registration` for every course on the
site, and onboarding then asked the student to find the same course again from
a dropdown. The system knew and threw it away.

This is the record that stops it being thrown away.

## Why a row and not a token

The obvious alternative is a signed token in the URL carrying the course. It
would work, but it puts the payload in the browser, and then every consumer
has to remember to re-resolve and re-validate it. A row inverts that: the
browser holds an opaque id and *nothing else*, so there is no payload to
tamper with, no signature to verify and no version to migrate. The course is
resolved once, here, at mint time, against `programs` — which is what makes
"never trust course data from the browser" structural rather than a rule
someone has to follow.

It also answers questions a token cannot: which courses send people to
register, how many of those intents are ever claimed, how long the gap is.

## Lifecycle

    mint (public, unauthenticated)
      → carried through /registration or /login as ?intent=<id>
      → claim (authenticated, binds to the user)
      → consumed by onboarding / the apply flow

`claimed_by` is what makes it survive the register-versus-login fork: the
intent does not care which door the student came through, only that somebody
eventually arrived. And it is deliberately *not* deleted on claim — a student
who abandons onboarding halfway and comes back tomorrow should still be
applying for the course they picked.

Nothing here creates an application. It records what the student was looking
at; `/apply/:slug` is still where they decide.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base
from ..db.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from .academic import Intake, Program, University
    from .user import User

#: How long an unclaimed intent stays good for.
#:
#: Long enough to survive a student reading the course page, going away to
#: find their passport number and coming back tomorrow; short enough that a
#: link shared months later does not silently attach someone to a course whose
#: fees and intake have moved on. Expiry is checked on read, not swept — an
#: expired row is evidence about a visit that happened and is worth keeping.
INTENT_TTL = timedelta(days=30)


class ApplyIntent(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "apply_intents"

    #: Resolved from the public course slug at mint time. RESTRICT rather than
    #: CASCADE: an intent whose programme was deleted is a question worth
    #: seeing, not a row to vanish.
    program_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("programs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Denormalised from the programme so the "you're applying for" card is one
    #: read, and so the record still says which institution was involved if the
    #: programme is later re-pointed.
    university_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("universities.id", ondelete="SET NULL"))
    intake_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("intakes.id", ondelete="SET NULL"))

    #: The public path the student pressed Apply on. Product analytics, and it
    #: is also what a "back to where you were" link would need.
    source_path: Mapped[str | None] = mapped_column(String(300))

    claimed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    #: Set when the student has actually started the application this intent
    #: was about, so onboarding stops offering it as "your selected course".
    fulfilled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    program: Mapped[Program] = relationship("Program")
    university: Mapped[University | None] = relationship("University")
    intake: Mapped[Intake | None] = relationship("Intake")
    claimer: Mapped[User | None] = relationship("User")

    __table_args__ = (
        Index("idx_apply_intents_claimed_by", "claimed_by"),
        Index("idx_apply_intents_program_id", "program_id"),
        Index("idx_apply_intents_created_at", "created_at"),
    )

    @property
    def is_expired(self) -> bool:
        return self.claimed_at is None and datetime.now(self.created_at.tzinfo) - self.created_at > INTENT_TTL

    def __repr__(self) -> str:
        return f"<ApplyIntent id={self.id} program_id={self.program_id}>"
