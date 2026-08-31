"""The eligibility ruleset itself.

Separate from `test_eligibility.py` because these are pure functions over a
dict — no database, no HTTP, no event loop. They are also the tests that will
still matter when the rules are revised: each one names a decision that was
made deliberately, so a future edit that breaks it has to argue with the
reason rather than only with the assertion.
"""

from __future__ import annotations

from app.services.eligibility_rules import ASSESSMENT_VERSION, assess


def _submission(**overrides) -> dict:
    payload = {
        "education": {"highest_qualification": "plus_two", "grade": "78%"},
        "english": {"evidence": "ielts", "overall_score": 6.5},
        "course": {"study_level": "undergraduate"},
        "finance": {"funding_source": "family", "estimated_funds": 2500000},
        "documents": {
            "academic": "ready",
            "passport": "ready",
            "english": "ready",
            "financial": "in_progress",
            "personal": "ready",
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


def test_a_student_who_has_not_sat_a_test_is_early_not_ineligible() -> None:
    """The single most important rule in the file. A public form must never
    tell a student they do not qualify because they have not started."""
    verdict = assess(_submission(english={"evidence": "not_taken", "overall_score": None}))
    assert verdict["english_status"].value == "needs_review"
    assert verdict["overall_status"].value != "more_information_required"


def test_finance_alone_never_holds_an_assessment_back() -> None:
    """This ruleset sets no funding threshold on purpose, so finance can never
    return `likely_meets`. If it counted toward the overall verdict, every
    submission ever made would land in the same queue."""
    verdict = assess(_submission())
    assert verdict["financial_status"].value == "needs_review"
    assert verdict["overall_status"].value == "preliminary_likely_eligible"


def test_document_readiness_is_out_of_the_whole_checklist() -> None:
    """Skipping four of five questions is not 100% ready."""
    verdict = assess(_submission(documents={"academic": "ready", "passport": None, "english": None, "financial": None, "personal": None}))
    assert verdict["document_readiness"] == 20


def test_a_diploma_holder_asking_about_a_masters_is_reviewed_not_refused() -> None:
    """Unusual is not impossible — it is a conversation about a top-up."""
    verdict = assess(_submission(education={"highest_qualification": "diploma"}, course={"study_level": "postgraduate"}))
    assert verdict["academic_status"].value == "needs_review"


def test_every_assessment_records_the_ruleset_that_produced_it() -> None:
    assert assess(_submission())["assessment_version"] == ASSESSMENT_VERSION
