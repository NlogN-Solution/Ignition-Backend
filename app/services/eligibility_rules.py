"""The preliminary eligibility ruleset.

Kept apart from the service that stores submissions, for one reason: **these
rules will change and stored assessments must not change with them.** Every
submission records the `version` that produced it, so a rule reworked next
spring leaves last autumn's assessments saying what they said at the time.
That is also why the human-readable reasons are stored alongside the verdict
rather than recomputed on read.

Three things this deliberately will not do.

**It will not say "not eligible".** Nothing here has seen a transcript, a bank
statement or a university's answer. The strongest verdict is
`LIKELY_MEETS` — meaning "worth a counsellor's time" — and the weakest is
`INSUFFICIENT_INFORMATION`, which is a statement about the form, not the
student. A student who has not sat IELTS yet is early, not rejected.

**It will not be clever.** Grades arrive as free text ("First class", "3.4",
"78%", "distinction") from applicants educated under half a dozen systems.
Parsing that into a comparable number and then gating on it would produce
confident nonsense. Where a value cannot be read honestly, the rule says
`NEEDS_REVIEW` and a person looks.

**It will not run in the browser.** The frontend shows encouragement; this
decides. Anything a student could edit in devtools is not an assessment.
"""

from __future__ import annotations

from typing import Any

from ..models.enums import (
    DocumentReadiness,
    EligibilityIndicator,
    EligibilityOverall,
    EnglishEvidence,
)

#: Bump when a rule below changes in a way that would alter a verdict.
ASSESSMENT_VERSION = "v1"

#: Minimum overall scores that typically open a UK undergraduate or taught
#: postgraduate course. They are the *common* floor, not any university's
#: actual requirement, and they are only ever used to sort a submission into a
#: queue — never shown to a student as a threshold they passed or failed.
_ENGLISH_FLOOR = {
    EnglishEvidence.IELTS: 6.0,
    EnglishEvidence.PTE: 59.0,
    EnglishEvidence.TOEFL: 78.0,
}

#: Qualifications that plausibly precede each study level. Absence from this
#: map is not a rejection — it routes to review.
_LEVEL_ENTRY = {
    "foundation": {"plus_two", "a_levels", "diploma", "other"},
    "undergraduate": {"plus_two", "a_levels", "diploma", "other"},
    "postgraduate": {"bachelors", "masters"},
}

_DOCUMENT_KEYS = ("academic", "passport", "english", "financial", "personal")

_READINESS_WEIGHT = {
    DocumentReadiness.READY: 1.0,
    DocumentReadiness.IN_PROGRESS: 0.5,
    DocumentReadiness.NOT_AVAILABLE: 0.0,
    DocumentReadiness.NOT_SURE: 0.0,
}


def _text(section: dict[str, Any], key: str) -> str:
    value = section.get(key)
    return value.strip() if isinstance(value, str) else ""


def _number(section: dict[str, Any], key: str) -> float | None:
    value = section.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def assess_academic(education: dict[str, Any], course: dict[str, Any]) -> tuple[EligibilityIndicator, list[str]]:
    qualification = _text(education, "highest_qualification")
    level = _text(course, "study_level")

    if not qualification:
        return EligibilityIndicator.INSUFFICIENT_INFORMATION, ["No highest qualification given."]

    if not _text(education, "grade"):
        return (
            EligibilityIndicator.NEEDS_REVIEW,
            ["Qualification recorded, but no grade or GPA — a counsellor will confirm the result."],
        )

    expected = _LEVEL_ENTRY.get(level)
    if expected is None:
        return (
            EligibilityIndicator.NEEDS_REVIEW,
            [f"Academic background recorded against a study level ('{level or 'unspecified'}') with no standard entry route."],
        )

    if qualification not in expected:
        # Not a rejection. Applying to a master's on a diploma is unusual and
        # sometimes right — with a top-up first, or with work experience.
        return (
            EligibilityIndicator.NEEDS_REVIEW,
            [
                f"Holds {qualification.replace('_', ' ')} and is asking about {level} study, "
                "which is not the usual route — worth a conversation about foundation or top-up options."
            ],
        )

    return (
        EligibilityIndicator.LIKELY_MEETS,
        ["Academic background is the usual route into the chosen study level. Grades still need verifying against the specific course."],
    )


def assess_english(english: dict[str, Any]) -> tuple[EligibilityIndicator, list[str]]:
    evidence = _text(english, "evidence")

    if not evidence:
        return EligibilityIndicator.INSUFFICIENT_INFORMATION, ["No English evidence given."]

    if evidence == EnglishEvidence.NOT_TAKEN.value:
        return (
            EligibilityIndicator.NEEDS_REVIEW,
            ["Has not sat an English test yet — needs advice on which test and score their course requires."],
        )

    if evidence == EnglishEvidence.MOI.value:
        return (
            EligibilityIndicator.NEEDS_REVIEW,
            ["Relying on a medium-of-instruction letter. Some universities accept one and many do not, so this needs checking per university."],
        )

    if evidence == EnglishEvidence.OTHER_TEST.value:
        return (
            EligibilityIndicator.NEEDS_REVIEW,
            ["Holds a test outside the usual three — a counsellor should confirm it is accepted."],
        )

    overall = _number(english, "overall_score")
    if overall is None:
        return EligibilityIndicator.INSUFFICIENT_INFORMATION, ["Test named but no overall score given."]

    floor = _ENGLISH_FLOOR.get(EnglishEvidence(evidence))
    if floor is None:
        return EligibilityIndicator.NEEDS_REVIEW, ["Test score recorded."]

    label = evidence.upper()
    if overall >= floor:
        # A pass on the overall can still fail on a band, and universities
        # differ on which band they care about — so this stays hedged.
        return (
            EligibilityIndicator.LIKELY_MEETS,
            [f"{label} {overall:g} is at or above the score that commonly opens a UK course. Individual band requirements still vary by university."],
        )

    return (
        EligibilityIndicator.NEEDS_REVIEW,
        [f"{label} {overall:g} is below the score most courses ask for. A retake or a pre-sessional English course may be the route."],
    )


def assess_financial(finance: dict[str, Any]) -> tuple[EligibilityIndicator, list[str]]:
    source = _text(finance, "funding_source")
    if not source:
        return EligibilityIndicator.INSUFFICIENT_INFORMATION, ["No funding plan given."]

    funds = _number(finance, "estimated_funds")
    if funds is None:
        return (
            EligibilityIndicator.NEEDS_REVIEW,
            [f"Funding plan recorded ({source.replace('_', ' ')}), with no figure attached. The amount and its evidence need discussing."],
        )

    # Deliberately no threshold. UKVI's maintenance requirement depends on
    # course length, city and what has already been paid, and a student who
    # sees a number here would read it as the bar to clear.
    return (
        EligibilityIndicator.NEEDS_REVIEW,
        [
            f"Funding plan recorded ({source.replace('_', ' ')}) with an estimate of {funds:,.0f}. "
            "Whether that satisfies the maintenance requirement depends on the course, the city and the deposit — confirm against the offer."
        ],
    )


def assess_documents(documents: dict[str, Any]) -> tuple[EligibilityIndicator, int, list[str]]:
    statuses = []
    for key in _DOCUMENT_KEYS:
        raw = documents.get(key)
        if isinstance(raw, str):
            try:
                statuses.append(DocumentReadiness(raw))
            except ValueError:
                continue

    if not statuses:
        return EligibilityIndicator.INSUFFICIENT_INFORMATION, 0, ["No document checklist completed."]

    score = sum(_READINESS_WEIGHT[status] for status in statuses)
    # Out of the full checklist, not out of what they answered: skipping four
    # of five questions is not 100% ready.
    readiness = round(score / len(_DOCUMENT_KEYS) * 100)

    if readiness >= 80:
        return (
            EligibilityIndicator.LIKELY_MEETS,
            readiness,
            [f"{readiness}% of the document checklist is ready."],
        )
    if readiness >= 40:
        return (
            EligibilityIndicator.NEEDS_REVIEW,
            readiness,
            [f"{readiness}% of the document checklist is ready — some documents still to gather."],
        )
    return (
        EligibilityIndicator.NEEDS_REVIEW,
        readiness,
        [f"{readiness}% of the document checklist is ready. Most documents are still outstanding."],
    )


def assess(payload: dict[str, Any]) -> dict[str, Any]:
    """Score one submission. Pure — no database, no clock, no I/O."""
    education = payload.get("education") or {}
    english = payload.get("english") or {}
    course = payload.get("course") or {}
    finance = payload.get("finance") or {}
    documents = payload.get("documents") or {}

    academic_status, academic_notes = assess_academic(education, course)
    english_status, english_notes = assess_english(english)
    financial_status, financial_notes = assess_financial(finance)
    document_status, readiness, document_notes = assess_documents(documents)

    indicators = (academic_status, english_status, financial_status, document_status)

    # The overall verdict routes the lead; it does not judge the student.
    #
    # Academic and English are what decide whether there is a realistic course
    # to talk about at all, so they alone can produce the top verdict. Finance
    # never can: this ruleset deliberately sets no funding threshold, so its
    # best answer is "recorded, needs review", and letting that hold the whole
    # assessment down would put every single submission in the same queue.
    if EligibilityIndicator.INSUFFICIENT_INFORMATION in indicators:
        overall = EligibilityOverall.MORE_INFORMATION_REQUIRED
    elif academic_status == EligibilityIndicator.LIKELY_MEETS and english_status == EligibilityIndicator.LIKELY_MEETS:
        overall = EligibilityOverall.PRELIMINARY_LIKELY_ELIGIBLE
    else:
        overall = EligibilityOverall.NEEDS_COUNSELLOR_REVIEW

    return {
        "academic_status": academic_status,
        "english_status": english_status,
        "financial_status": financial_status,
        "document_status": document_status,
        "overall_status": overall,
        "document_readiness": readiness,
        "assessment_notes": [*academic_notes, *english_notes, *financial_notes, *document_notes],
        "assessment_version": ASSESSMENT_VERSION,
    }
