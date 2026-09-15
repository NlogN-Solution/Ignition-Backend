"""A milestone, for the screen that celebrates it.

`ApplicationMilestoneRead`, not `MilestoneRead`: that name is taken by
`schemas/progress.py`, which describes a rung on the student's *journey*
ladder. Different question, and `routes/student.py` imports both.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ApplicationMilestoneRead(BaseModel):
    id: UUID
    application_id: UUID
    kind: str
    occurred_at: datetime
    seen_at: datetime | None
    #: Denormalised for the overlay, which has to say "an offer from Coventry
    #: for MSc Computer Science" without the client making three more calls
    #: while a modal is already on screen.
    program_name: str | None = None
    university_name: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def of(cls, milestone: Any) -> ApplicationMilestoneRead:
        application = milestone.application
        program = application.program if application else None
        university = program.university if program else None
        return cls(
            id=milestone.id,
            application_id=milestone.application_id,
            kind=milestone.kind.value if hasattr(milestone.kind, "value") else str(milestone.kind),
            occurred_at=milestone.occurred_at,
            seen_at=milestone.seen_at,
            program_name=program.name if program else None,
            university_name=university.name if university else None,
        )
