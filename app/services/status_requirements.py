"""What each status change needs before it is allowed to happen.

## Why a table and not a chain of `if`s

Most statuses need nothing but a note. Two need a date and a document, because
they record something that happened *outside* the system: a university issued
an offer, a university issued a CAS. Those are the two that were broken —
`offer_received` could be set from a dropdown with no date and no letter, and
`cas_received` had nowhere to put either, so the student's portal showed a
status with nothing behind it.

The obvious fix is `if status == OFFER_RECEIVED: require(...)` in the route. It
works for two and rots at five: the requirements end up duplicated between the
API that enforces them and the dialog that collects them, and the two drift.

So the requirements are data. One table, read by the validator *and* served to
the client so the dialog renders itself from the same source. Adding a
milestone is a row here, not a branch in three files.

## What it does not do

It does not encode which transitions are legal. `documents_pending` →
`enrolled` is nonsense, but this module is deliberately not the place that
says so: a counsellor correcting a mis-set status needs to move an application
backwards, and a transition graph that forbids it turns a typo into a support
ticket. What is enforced here is *completeness* — if you say an offer arrived,
say when and show the letter.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.enums import ApplicationStatus, DocumentType
from ..models.milestone import MilestoneKind


@dataclass(frozen=True)
class StatusRequirement:
    """What a status needs, and what recording it produces."""

    #: Form field → the application column it writes. Both required.
    required_date_field: str | None = None
    #: The document type that must be attached, if any.
    required_document: DocumentType | None = None
    #: Optional extra columns the dialog offers.
    optional_fields: tuple[str, ...] = ()
    #: The milestone to record, which is what drives the student's celebration
    #: and the durable notification.
    milestone: MilestoneKind | None = None
    #: Shown at the top of the dialog.
    prompt: str = ""
    document_label: str = ""


#: Keyed on the status being moved *to*.
#:
#: Only statuses with real external evidence appear. Everything absent from
#: this map takes the plain status-and-note path, which is the correct default
#: — most transitions are Ignition recording its own progress, and asking for a
#: document to prove one would be ceremony.
STATUS_REQUIREMENTS: dict[ApplicationStatus, StatusRequirement] = {
    ApplicationStatus.OFFER_RECEIVED: StatusRequirement(
        required_date_field="offer_received_date",
        required_document=DocumentType.OFFER_LETTER,
        optional_fields=("offer_type", "tuition_fee", "scholarship_amount"),
        milestone=MilestoneKind.OFFER_RECEIVED,
        prompt="The university has made an offer. Record the date it was issued and upload the letter.",
        document_label="Offer letter",
    ),
    ApplicationStatus.CAS_RECEIVED: StatusRequirement(
        required_date_field="cas_received_date",
        required_document=DocumentType.CAS_LETTER,
        optional_fields=("cas_number",),
        milestone=MilestoneKind.CAS_RECEIVED,
        prompt="The university has issued the CAS. The student cannot apply for a visa without it.",
        document_label="CAS statement",
    ),
    ApplicationStatus.VISA_APPROVED: StatusRequirement(
        required_date_field="visa_decision_date",
        # No required document: the decision letter is often a portal
        # screenshot or an email, and refusing to record an approved visa for
        # want of a file would mean the status was wrong instead.
        milestone=MilestoneKind.VISA_APPROVED,
        prompt="Record the decision date. Upload the decision letter if you have it.",
        document_label="Visa decision",
    ),
}

#: Columns a milestone form may write, as an allowlist.
#:
#: The route applies whatever the requirement names, so this is the backstop
#: that stops a crafted request writing `status` or `student_id` through the
#: milestone path — which would be a way around the audit trail that
#: `change_application_status` exists to keep.
WRITABLE_MILESTONE_FIELDS = frozenset(
    {
        "offer_received_date",
        "cas_received_date",
        "visa_decision_date",
        "offer_type",
        "cas_number",
        "tuition_fee",
        "scholarship_amount",
    }
)


def requirement_for(status: ApplicationStatus) -> StatusRequirement | None:
    return STATUS_REQUIREMENTS.get(status)


def requirements_payload() -> list[dict]:
    """The config, for the client that renders the dialog.

    Served rather than duplicated in TypeScript so there is one answer to "what
    does recording an offer need". A field added here appears in the dialog
    without a frontend change.
    """
    return [
        {
            "status": status.value,
            "prompt": requirement.prompt,
            "required_date_field": requirement.required_date_field,
            "required_document": (
                requirement.required_document.value if requirement.required_document else None
            ),
            "document_label": requirement.document_label,
            "optional_fields": list(requirement.optional_fields),
            "milestone": requirement.milestone.value if requirement.milestone else None,
        }
        for status, requirement in STATUS_REQUIREMENTS.items()
    ]
