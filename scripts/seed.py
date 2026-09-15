"""Seed a development database with a usable starting state.

Idempotent: every insert is keyed on a natural unique column and skipped when
the row already exists, so running it twice is a no-op rather than a pile of
duplicates or an IntegrityError.

    python -m scripts.seed              # seed
    python -m scripts.seed --reset      # delete seeded rows first

Refuses to touch a production database. The catalog here is deliberately small
— Phase 4 imports the real one from the student frontend's data files.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.models import (  # noqa: E402
    AttendancePolicy,
    ChecklistTemplateItem,
    CostOfLivingCategory,
    Country,
    CountryCostOfLiving,
    CurrencyRate,
    Department,
    InterviewFeedbackBand,
    InterviewQuestion,
    InterviewType,
    LeaveType,
    PointsRule,
    PortalAccessFee,
    Program,
    ProgressMilestone,
    University,
    User,
    WorkflowStage,
    WorkflowTemplate,
)
from app.models.enums import DegreeLevel, UserRole, UserStatus  # noqa: E402

#: Development logins. `example.com` is IANA's reserved documentation domain and
#: — unlike `.test` — passes `EmailStr`, so these accounts can actually sign in;
#: seeding an address the login endpoint rejects creates rows nobody can use.
#: The password is printed on completion, and staff carry
#: `must_change_password` so a seeded credential cannot quietly become real.
STAFF = [
    ("owner@ignition.example.com", UserRole.SUPER_ADMIN, "Ignition", "Owner"),
    ("admin@ignition.example.com", UserRole.ADMIN, "Asha", "Adhikari"),
    ("manager@ignition.example.com", UserRole.MANAGER, "Manish", "Karki"),
    ("counsellor@ignition.example.com", UserRole.COUNSELLOR, "Chandra", "Bhatta"),
    ("admissions@ignition.example.com", UserRole.ADMISSIONS, "Anjali", "Shrestha"),
    ("finance@ignition.example.com", UserRole.FINANCE, "Prakash", "Thapa"),
    ("frontdesk@ignition.example.com", UserRole.FRONTDESK, "Nisha", "Gurung"),
]
STUDENTS = [
    ("student@ignition.example.com", "Sita", "Rai"),
    ("student2@ignition.example.com", "Bikash", "Lama"),
]
DEV_PASSWORD = "ignition-dev-password"

COUNTRIES = [
    {"name": "Australia", "iso2": "AU", "iso3": "AUS", "phone_code": "+61", "currency_code": "AUD"},
    {"name": "United Kingdom", "iso2": "GB", "iso3": "GBR", "phone_code": "+44", "currency_code": "GBP"},
    {"name": "Canada", "iso2": "CA", "iso3": "CAN", "phone_code": "+1", "currency_code": "CAD"},
    {"name": "United States", "iso2": "US", "iso3": "USA", "phone_code": "+1", "currency_code": "USD"},
    {"name": "Nepal", "iso2": "NP", "iso3": "NPL", "phone_code": "+977", "currency_code": "NPR"},
]

UNIVERSITIES = [
    ("AU", "University of Melbourne", "Melbourne", True),
    ("AU", "Monash University", "Melbourne", True),
    ("GB", "University College London", "London", True),
    ("GB", "University of Manchester", "Manchester", False),
    ("CA", "University of Toronto", "Toronto", True),
    ("US", "Arizona State University", "Tempe", False),
]

PROGRAMS = [
    ("University of Melbourne", "Master of Information Technology", DegreeLevel.MASTER, 24, 45000.0),
    ("University of Melbourne", "Bachelor of Commerce", DegreeLevel.BACHELOR, 36, 42000.0),
    ("Monash University", "Master of Data Science", DegreeLevel.MASTER, 24, 44000.0),
    ("University College London", "MSc Computer Science", DegreeLevel.MASTER, 12, 38000.0),
    ("University of Toronto", "Master of Engineering", DegreeLevel.MASTER, 20, 52000.0),
]

DEPARTMENTS = ["Admissions", "Counselling", "Finance", "Marketing", "Operations"]

LEAVE_TYPES = [
    ("Annual Leave", 12, True),
    ("Sick Leave", 10, True),
    ("Unpaid Leave", 0, False),
]

#: The journey the student portal renders. Weighted so later, harder steps count
#: for more than filling in a profile.
MILESTONES = [
    ("profile", "Profile complete", 10),
    ("documents", "Core documents uploaded", 15),
    ("shortlist", "Universities shortlisted", 10),
    ("apply", "Application submitted", 20),
    ("interview", "Interview completed", 10),
    ("offer", "Offer received", 15),
    ("visa", "Visa approved", 15),
    ("departure", "Ready to depart", 5),
]

#: Points are awarded by event subscribers only — never by a client call.
POINTS_RULES = [
    ("profile.complete", "Profile completed", 25, "profile", True),
    ("document.upload", "Document approved", 15, "documents", False),
    ("task.complete", "Checklist task completed", 20, "tasks", False),
    ("application.submit", "Application submitted", 50, "applications", False),
    ("interview.complete", "Mock interview completed", 15, "interviews", False),
]

#: The default journey checklist, from the portal's `tasksChecklist.json`. Each
#: rung depends on the one before it, and the due offsets are days from the day
#: the student joined — the template is shared, so an absolute date would be
#: wrong for every cohort but the first.
CHECKLIST_TEMPLATE = [
    # key, title, description, stage, depends_on, due_after_days
    (
        "passport",
        "Secure your passport",
        "Apply for or renew a passport valid for at least six months beyond your intake.",
        "Passport",
        None,
        30,
    ),
    (
        "ielts",
        "Sit the IELTS exam",
        "Book and complete IELTS with a band score of 7.0 or higher.",
        "IELTS",
        "passport",
        75,
    ),
    (
        "sop",
        "Write your Statement of Purpose",
        "Draft, review with your counsellor and finalise your SOP.",
        "SOP",
        "ielts",
        105,
    ),
    (
        "lor",
        "Collect Letters of Recommendation",
        "Request two academic references and upload the signed letters.",
        "LOR",
        "sop",
        120,
    ),
    (
        "apply",
        "Submit your applications",
        "Complete and submit applications to every shortlisted university.",
        "Apply",
        "lor",
        150,
    ),
    (
        "interview",
        "Pass the admission interview",
        "Practise with the AI interview module, then attend the university interview.",
        "Interview",
        "apply",
        180,
    ),
    (
        "offer",
        "Accept your offer",
        "Review offer letters, compare conditions and confirm your place.",
        "Offer",
        "interview",
        210,
    ),
    (
        "visa",
        "Apply for your student visa",
        "Assemble financial documents and lodge the student visa application.",
        "Visa",
        "offer",
        240,
    ),
    (
        "departure",
        "Prepare for departure",
        "Book flights, arrange accommodation and complete pre-departure briefing.",
        "Departure",
        "visa",
        270,
    ),
]

#: Mock interview catalog, from the portal's `interviewTypes.json` /
#: `interviews.json`. Each type's question `max_score` values sum to 100, so a
#: session's total score is directly comparable to `passing_score` and to the
#: feedback bands' `min_score` regardless of which type was taken.
INTERVIEW_TYPES = [
    # key, name, description, duration_minutes, passing_score
    #
    # The UK route, in the order a student meets it. This replaced a US-shaped
    # set — an "Academic Interview", a "Visa Interview" described as *consular*
    # questions, and a "Merit Panel Interview". The UK has no consular visa
    # interview: what it has is a UKVI credibility interview, usually by video,
    # and it is the one that refuses people. A merit panel is not part of the
    # ordinary journey at all.
    (
        "pre_cas",
        "Pre-CAS Interview",
        "The university's own check before it issues your CAS. Course knowledge, funding and intent.",
        20,
        70,
    ),
    (
        "credibility",
        "UKVI Credibility Interview",
        "The Home Office interview for a Student visa. Short, recorded, and decided on whether you sound like a genuine student.",
        15,
        75,
    ),
    (
        "academic",
        "Academic / Programme Interview",
        "Departmental questions for competitive courses — your subject, your work, and why this programme.",
        25,
        70,
    ),
]

INTERVIEW_QUESTIONS = {
    "pre_cas": [
        (
            "Why have you chosen this university and this course over the others you applied to?",
            "Name the modules and what they lead to. 'It was the offer I got' is the answer that fails this.",
            25,
        ),
        (
            "How will you pay your tuition and living costs, and where is that money now?",
            "Name the sponsor, their relationship to you, and the account the funds have been held in.",
            25,
        ),
        (
            "What do you know about the modules in your first year?",
            "Two or three by name, and what each one covers. Read the course page before this interview.",
            25,
        ),
        (
            "What do you plan to do after you graduate?",
            "A specific role and where — the Graduate Route or a plan back home both answer this well.",
            25,
        ),
    ],
    "credibility": [
        (
            "Why do you want to study in the UK rather than in your own country?",
            "Give a reason about the course and the sector, not about immigration.",
            25,
        ),
        (
            "Tell me about your course. How long is it, what will you study, and how much does it cost?",
            "Know the duration, the tuition figure and two or three modules. Vagueness here is what refusals cite.",
            25,
        ),
        (
            "Where will you live, and how much will it cost you each month?",
            "A city, a rough rent, and how it fits the maintenance funds you have shown.",
            25,
        ),
        (
            "What will you do when your visa ends?",
            "Answer plainly. A clear plan reads as genuine; a vague one reads as an intention to stay.",
            25,
        ),
    ],
    "academic": [
        (
            "Why this programme, and how does it follow on from what you have already studied?",
            "Name two modules and link them to a project or a paper you have actually worked on.",
            25,
        ),
        (
            "Talk me through a piece of work you led. What went wrong, and what did you do about it?",
            "Situation, action, result — and give the result a number.",
            25,
        ),
        (
            "Whose research in the department interests you, and why?",
            "Reference the work, not just the name. A paper or a lab, and what you found interesting in it.",
            25,
        ),
        (
            "What would you want to be doing five years after you graduate?",
            "Tie it back to what this programme actually teaches.",
            25,
        ),
    ],
}

#: Cost-of-living catalog, from the portal's `countryFinance.json`. Only the
#: three destinations already in `COUNTRIES` are seeded here — the fixture
#: also covers Germany, but this catalog is deliberately small (see module
#: docstring) and a cost-of-living row needs a `Country` to hang off.
COUNTRY_COST_OF_LIVING = {
    "Canada": {
        "proof_of_funds_name": "Guaranteed Investment Certificate (GIC)",
        "note": "IRCC requires proof of first-year tuition plus a GIC covering living costs.",
        "tuition_usd": 42000,
        "living_cost_usd": 15000,
        "insurance_usd": 900,
        "visa_fee_usd": 175,
        "biometrics_usd": 63,
        "categories": [
            ("Tuition", 42000, True, "pre-arrival"),
            ("Living Expenses", 15000, True, "ongoing"),
            ("Health Insurance", 900, True, "pre-arrival"),
            ("Visa Fee", 175, True, "pre-arrival"),
            ("Biometrics", 63, True, "pre-arrival"),
            ("Flight Tickets", 1100, True, "pre-arrival"),
            ("Accommodation Deposit", 1600, True, "arrival"),
            ("Application Fees", 480, True, "pre-arrival"),
            ("IELTS / PTE", 260, True, "pre-arrival"),
            ("Initial Settlement Cost", 1800, False, "arrival"),
            ("Miscellaneous Buffer", 2000, False, "ongoing"),
        ],
    },
    "Australia": {
        "proof_of_funds_name": "Genuine Student financial capacity evidence",
        "note": "Home Affairs requires 12 months of living costs plus tuition and travel.",
        "tuition_usd": 36000,
        "living_cost_usd": 20000,
        "insurance_usd": 2100,
        "visa_fee_usd": 1030,
        "biometrics_usd": 0,
        "categories": [
            ("Tuition", 36000, True, "pre-arrival"),
            ("Living Expenses", 20000, True, "ongoing"),
            ("Overseas Student Health Cover", 2100, True, "pre-arrival"),
            ("Visa Fee", 1030, True, "pre-arrival"),
            ("Flight Tickets", 1250, True, "pre-arrival"),
            ("Accommodation Deposit", 2100, True, "arrival"),
            ("Application Fees", 330, True, "pre-arrival"),
            ("IELTS / PTE", 260, True, "pre-arrival"),
            ("Initial Settlement Cost", 2200, False, "arrival"),
            ("Miscellaneous Buffer", 2400, False, "ongoing"),
        ],
    },
    "United Kingdom": {
        "proof_of_funds_name": "28-day maintenance funds statement",
        "note": "Funds must sit in the account for 28 consecutive days before applying.",
        "tuition_usd": 33000,
        "living_cost_usd": 14000,
        "insurance_usd": 1400,
        "visa_fee_usd": 620,
        "biometrics_usd": 25,
        "categories": [
            ("Tuition", 33000, True, "pre-arrival"),
            ("Living Expenses", 14000, True, "ongoing"),
            ("Immigration Health Surcharge", 1400, True, "pre-arrival"),
            ("Visa Fee", 620, True, "pre-arrival"),
            ("Biometrics", 25, True, "pre-arrival"),
            ("Flight Tickets", 850, True, "pre-arrival"),
            ("Accommodation Deposit", 1400, True, "arrival"),
            ("Application Fees", 300, True, "pre-arrival"),
            ("IELTS / PTE", 260, True, "pre-arrival"),
            ("Initial Settlement Cost", 1500, False, "arrival"),
            ("Miscellaneous Buffer", 1800, False, "ongoing"),
        ],
    },
}

#: Currency rates, from `currencyRates.json`. A static snapshot — the source
#: file says as much — refreshed by re-seeding rather than a live feed.
CURRENCY_RATES = [
    # code, name, symbol, per_usd, change_pct
    ("USD", "US Dollar", "$", 1, 0),
    ("NPR", "Nepalese Rupee", "Rs", 137.4, 1.8),
    ("CAD", "Canadian Dollar", "C$", 1.36, -0.4),
    ("AUD", "Australian Dollar", "A$", 1.52, 0.9),
    ("GBP", "Pound Sterling", "£", 0.78, -0.2),
    ("EUR", "Euro", "€", 0.92, 0.3),
    ("JPY", "Japanese Yen", "¥", 151.2, 2.4),
]

WORKFLOW_STAGES = [
    ("profile", "Profile & Documents"),
    ("shortlist", "Course Shortlisting"),
    ("apply", "University Application"),
    ("offer", "Offer & Acceptance"),
    ("visa", "Visa Application"),
    ("predeparture", "Pre-departure"),
]


#: Matched by score at completion: the band with the highest `min_score` the
#: session's total meets or exceeds.
INTERVIEW_FEEDBACK_BANDS = [
    (
        "excellent",
        85,
        "Excellent",
        "green",
        "Panel-ready. Your answers were specific, structured and well paced.",
        [
            "Every answer opened with a clear position",
            "Concrete examples backed each claim",
            "Confident, unhurried delivery",
        ],
        ["Trim the closing sentence on longer answers", "Prepare one more research-specific reference"],
    ),
    (
        "good",
        70,
        "Strong",
        "blue",
        "A solid performance. Tighten your examples and you are interview ready.",
        ["Good programme knowledge", "Clear motivation for studying abroad"],
        [
            "Quantify results — numbers make projects memorable",
            "Avoid repeating the question back before answering",
            "Practise a 30-second version of your longest answer",
        ],
    ),
    (
        "average",
        50,
        "Developing",
        "orange",
        "The substance is there but the structure is loose. Rehearse before the real thing.",
        ["Honest, natural tone", "No factual contradictions"],
        [
            "Use a situation → action → result structure",
            "Name specific modules, labs or sponsors",
            "Cut filler openings such as 'I think that maybe'",
            "Rehearse the funding question until it is automatic",
        ],
    ),
    (
        "weak",
        0,
        "Needs work",
        "red",
        "Answers were too short or too general to convince a panel. Try the set again after preparing notes.",
        ["You completed the full set — that is the first step"],
        [
            "Write bullet notes for each question before retrying",
            "Aim for at least three sentences per answer",
            "Book a counsellor session to review your talking points",
        ],
    ),
]


async def _get_or_create(session: AsyncSession, model: type[Any], match: dict[str, Any], **values: Any) -> Any:
    """Fetch the row matching `match`, or insert one with `match | values`."""
    query = select(model)
    for column, value in match.items():
        query = query.where(getattr(model, column) == value)
    existing = await session.scalar(query)
    if existing is not None:
        return existing

    instance = model(**match, **values)
    session.add(instance)
    await session.flush()
    return instance


async def seed(session: AsyncSession) -> dict[str, int]:
    counts: dict[str, int] = {}

    # --- Users ---------------------------------------------------------------
    created_users = 0
    for email, role, first, last in STAFF:
        before = await session.scalar(select(User.id).where(User.email == email))
        await _get_or_create(
            session,
            User,
            {"email": email},
            first_name=first,
            last_name=last,
            role=role,
            status=UserStatus.ACTIVE,
            password_hash=hash_password(DEV_PASSWORD),
            must_change_password=True,
        )
        created_users += before is None
    for email, first, last in STUDENTS:
        before = await session.scalar(select(User.id).where(User.email == email))
        await _get_or_create(
            session,
            User,
            {"email": email},
            first_name=first,
            last_name=last,
            role=UserRole.STUDENT,
            status=UserStatus.ACTIVE,
            password_hash=hash_password(DEV_PASSWORD),
        )
        created_users += before is None
    counts["users"] = created_users

    # --- Catalog -------------------------------------------------------------
    countries: dict[str, Country] = {}
    for data in COUNTRIES:
        iso2 = data["iso2"]
        countries[iso2] = await _get_or_create(
            session, Country, {"iso2": iso2}, **{k: v for k, v in data.items() if k != "iso2"}
        )

    universities: dict[str, University] = {}
    for iso2, name, city, is_partner in UNIVERSITIES:
        universities[name] = await _get_or_create(
            session,
            University,
            {"name": name},
            country_id=countries[iso2].id,
            city=city,
            is_partner=is_partner,
        )

    for university_name, name, level, months, fee in PROGRAMS:
        await _get_or_create(
            session,
            Program,
            {"name": name, "university_id": universities[university_name].id},
            degree_level=level,
            duration_months=months,
            tuition_fee=fee,
            currency="AUD",
        )
    counts["countries"] = len(countries)
    counts["universities"] = len(universities)
    counts["programs"] = len(PROGRAMS)

    # --- Org structure -------------------------------------------------------
    for name in DEPARTMENTS:
        await _get_or_create(session, Department, {"name": name})
    counts["departments"] = len(DEPARTMENTS)

    for name, days, is_paid in LEAVE_TYPES:
        await _get_or_create(session, LeaveType, {"name": name}, default_days_per_year=days, paid=is_paid)
    counts["leave_types"] = len(LEAVE_TYPES)

    # --- Attendance policy ---------------------------------------------------
    # Singleton. Without it `GET /attendance/policy` 404s and the attendance
    # page has nothing to measure check-ins against.
    await _get_or_create(session, AttendancePolicy, {"is_singleton": True})
    counts["attendance_policy"] = 1

    # --- Progress ladder and points rules -------------------------------------
    for order, (key, label, weight) in enumerate(MILESTONES, start=1):
        await _get_or_create(session, ProgressMilestone, {"key": key}, label=label, weight=weight, order=order)
    counts["milestones"] = len(MILESTONES)

    for action, label, points, category, once in POINTS_RULES:
        await _get_or_create(
            session,
            PointsRule,
            {"action": action},
            label=label,
            points=points,
            category=category,
            once_per_student=once,
        )
    counts["points_rules"] = len(POINTS_RULES)

    # --- Journey checklist template -------------------------------------------
    # Students receive their copy on first read of /student/me/checklist, so
    # adding a rung here reaches everyone without a backfill.
    for order, (key, title, description, stage, depends_on, due_days) in enumerate(CHECKLIST_TEMPLATE, start=1):
        await _get_or_create(
            session,
            ChecklistTemplateItem,
            {"key": key},
            title=title,
            description=description,
            stage=stage,
            order=order,
            depends_on_key=depends_on,
            due_after_days=due_days,
        )
    counts["checklist_template"] = len(CHECKLIST_TEMPLATE)

    # --- Portal access fee ----------------------------------------------------
    #
    # The fallback row (country_id NULL), which is what a student from any
    # country without its own price is quoted. Without at least this row
    # `PortalAccessService.fee_for` returns None and the portal cannot name a
    # price — so every student is locked out of the workflow with no way to
    # find out what it costs, which is the state this seed found.
    await _get_or_create(
        session,
        PortalAccessFee,
        {"country_id": None},
        amount=500,
        currency="NPR",
        notes="Default one-time portal access fee.",
    )
    counts["portal_access_fee"] = 1

    # --- Mock interview catalog -----------------------------------------------
    #
    # This block UPDATES as well as inserts, unlike `_get_or_create` elsewhere in
    # this file. The catalog is content, and the seed is its source of truth: an
    # interview set that could only ever be written once would mean every wording
    # fix needed a migration or a manual UPDATE. Sessions and answers are student
    # data and are never touched here.
    for key, name, description, duration, passing in INTERVIEW_TYPES:
        interview_type = await _get_or_create(
            session,
            InterviewType,
            {"key": key},
            name=name,
            description=description,
            duration_minutes=duration,
            passing_score=passing,
        )
        interview_type.name = name
        interview_type.description = description
        interview_type.duration_minutes = duration
        interview_type.passing_score = passing
        interview_type.is_active = True

        for order, (prompt, hint, max_score) in enumerate(INTERVIEW_QUESTIONS[key], start=1):
            existing_question = await session.scalar(
                select(InterviewQuestion).where(
                    InterviewQuestion.type_id == interview_type.id, InterviewQuestion.order == order
                )
            )
            if existing_question is None:
                session.add(
                    InterviewQuestion(
                        type_id=interview_type.id, prompt=prompt, hint=hint, max_score=max_score, order=order
                    )
                )
            else:
                existing_question.prompt = prompt
                existing_question.hint = hint
                existing_question.max_score = max_score

    # Retire anything this file no longer defines — deactivated, never deleted.
    # The US-shaped "visa" (consular) and "merit" panel types were replaced by
    # the UK set above, but a student may already have sat one, and their
    # session rows point at these ids. Deactivating drops them out of
    # `list_types` (which filters `is_active`) while leaving that history intact.
    retired = await session.scalars(
        select(InterviewType).where(
            InterviewType.key.notin_([key for key, *_ in INTERVIEW_TYPES]),
            InterviewType.is_active.is_(True),
        )
    )
    retired_count = 0
    for interview_type in retired:
        interview_type.is_active = False
        retired_count += 1

    counts["interview_types"] = len(INTERVIEW_TYPES)
    counts["interview_types_retired"] = retired_count

    for key, min_score, band, tone, summary, strengths, improvements in INTERVIEW_FEEDBACK_BANDS:
        await _get_or_create(
            session,
            InterviewFeedbackBand,
            {"key": key},
            min_score=min_score,
            band=band,
            tone=tone,
            summary=summary,
            strengths=strengths,
            improvements=improvements,
        )
    counts["interview_feedback_bands"] = len(INTERVIEW_FEEDBACK_BANDS)
    await session.commit()

    # --- Finance catalog: cost of living and currency rates -------------------
    countries_by_name = {data["name"]: countries[data["iso2"]] for data in COUNTRIES}
    cost_of_living_rows = 0
    for country_name, entry in COUNTRY_COST_OF_LIVING.items():
        country = countries_by_name[country_name]
        cost_of_living = await _get_or_create(
            session,
            CountryCostOfLiving,
            {"country_id": country.id},
            proof_of_funds_name=entry["proof_of_funds_name"],
            note=entry["note"],
            tuition_usd=entry["tuition_usd"],
            living_cost_usd=entry["living_cost_usd"],
            insurance_usd=entry["insurance_usd"],
            visa_fee_usd=entry["visa_fee_usd"],
            biometrics_usd=entry["biometrics_usd"],
        )
        existing_categories = await session.scalar(
            select(func.count())
            .select_from(CostOfLivingCategory)
            .where(CostOfLivingCategory.cost_of_living_id == cost_of_living.id)
        )
        if not existing_categories:
            for order, (category, amount, essential, phase) in enumerate(entry["categories"], start=1):
                session.add(
                    CostOfLivingCategory(
                        cost_of_living_id=cost_of_living.id,
                        category=category,
                        amount_usd=amount,
                        essential=essential,
                        phase=phase,
                        order=order,
                    )
                )
        cost_of_living_rows += 1
    counts["cost_of_living"] = cost_of_living_rows

    for code, name, symbol, per_usd, change_pct in CURRENCY_RATES:
        await _get_or_create(
            session, CurrencyRate, {"code": code}, name=name, symbol=symbol, per_usd=per_usd, change_pct=change_pct
        )
    counts["currency_rates"] = len(CURRENCY_RATES)
    await session.commit()

    # --- Default workflow ----------------------------------------------------
    template = await _get_or_create(
        session,
        WorkflowTemplate,
        {"slug": "standard-application"},
        name="Standard Application",
        description="Default end-to-end pipeline from profile to departure.",
        is_default=True,
    )
    for order, (key, name) in enumerate(WORKFLOW_STAGES):
        await _get_or_create(
            session,
            WorkflowStage,
            {"template_id": template.id, "key": key},
            name=name,
            order=order,
        )
    counts["workflow_stages"] = len(WORKFLOW_STAGES)

    await session.commit()
    return counts


#: Domains this seeder has used. `--reset` matches on all of them, not just the
#: current one: renaming the seed domain once left nine `@ignition.test` rows
#: behind that `--reset` could no longer see, and a single unparseable address
#: was enough to 500 `GET /users` for the whole page.
SEEDED_EMAIL_DOMAINS = ("@ignition.example.com", "@ignition.test")


async def reset(session: AsyncSession) -> None:
    """Remove seeded rows, newest dependency first."""
    seeded_emails = [email for email, *_ in STAFF] + [email for email, *_ in STUDENTS]
    from sqlalchemy import or_

    matches = or_(
        User.email.in_(seeded_emails),
        *(User.email.like(f"%{domain}") for domain in SEEDED_EMAIL_DOMAINS),
    )
    for user in (await session.scalars(select(User).where(matches))).all():
        await session.delete(user)

    template = await session.scalar(select(WorkflowTemplate).where(WorkflowTemplate.slug == "standard-application"))
    if template is not None:
        await session.delete(template)

    for model, column, values in (
        (Program, "name", [p[1] for p in PROGRAMS]),
        (University, "name", [u[1] for u in UNIVERSITIES]),
        (Country, "iso2", [c["iso2"] for c in COUNTRIES]),
        (Department, "name", DEPARTMENTS),
        (LeaveType, "name", [lt[0] for lt in LEAVE_TYPES]),
    ):
        for row in (await session.scalars(select(model).where(getattr(model, column).in_(values)))).all():
            await session.delete(row)

    await session.commit()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete seeded rows before seeding")
    args = parser.parse_args()

    settings = get_settings()
    if settings.is_production:
        print("Refusing to seed a production database.", file=sys.stderr)
        return 1

    async with session_factory() as session:
        if args.reset:
            await reset(session)
            print("Removed previously seeded rows.")
        counts = await seed(session)

    print(f"Seeded {settings.DB_NAME}:")
    for label, count in counts.items():
        print(f"  {label:<16} {count}")
    print(f"\nStaff and student logins use the password: {DEV_PASSWORD}")
    print("Staff accounts are flagged must_change_password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
