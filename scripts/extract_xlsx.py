"""Phase 1 — spreadsheet to normalised JSON.

Pure: reads the September 2026 intake workbook, writes JSON and a review
report. Touches no database and imports nothing from `app`.

    .venv/bin/python -m scripts.extract_xlsx

Outputs into backend/data/catalogue/:

    universities.json  44 records
    routes.json        one per (university, entry route) — the criteria matrix
    offerings.json     every course row on the 44 university sheets
    report.md          everything a human must check before importing

The per-university sheets are authoritative (CATALOGUE-CMS-PLAN.md section 4);
the two master sheets are read only as a cross-check and any disagreement is
listed in the report.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.catalogue_source import (  # noqa: E402
    BRANCH_CAMPUS_UNIVERSITIES,
    CRITERIA_LABELS,
    INTAKE_NAME,
    PATHWAY_CENTRES,
    ROUTE_HEADERS,
    UNIVERSITIES,
    resolve_university,
)
from scripts.xlsx_reader import Workbook, collapse, key  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
WORKBOOK = REPO / "Ignition-Landing" / "SEPTEMBER 2026 INTAKE - UNIVERSITY DETAILS (1).xlsx"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "catalogue"

MASTER_SHEETS = {"UG_COURSES": "UG", "PG_COURSES": "PG"}
INDEX_SHEETS = {"HOME PAGE", "UG_COURSES", "PG_COURSES"}

# --- course-table column vocabulary ----------------------------------------
# Sheets do not agree on their headers, so columns are mapped by header text
# per block rather than by position.

TITLE_HEADERS = {"COURSE", "COURSES", "COURSE NAME", "PROGRAMME", "PROGRAMMES", "PROGRESSION DEGREE"}
LEVEL_HEADERS = {"LEVEL", "COURSE LEVEL"}
INTAKE_HEADERS = {"INTAKE", "INTAKE CLOSED"}
CAMPUS_HEADERS = {"CAMPUS", "CAMPUS LOCATION", "LOCATION"}
EXTRA_HEADERS = {"EXTRA REQUIREMENTS", "EXTRA REQUIREMENT", "ADDITIONAL REQUIREMENTS"}
DURATION_HEADERS = {"DURATION"}
PATHWAY_HEADERS = {"PATHWAY PROGRAMME"}
FEE_HEADERS = {"FEE STRUCTURE"}
SCHOLARSHIP_HEADERS = {"SCHOLARSHIP"}

BLOCK_START_HEADERS = TITLE_HEADERS | PATHWAY_HEADERS

# --- the LEVEL column vocabulary --------------------------------------------
# 23 distinct values across the 44 sheets. Most encode more than a study level:
# "PG with 2 Years" carries a duration, "Extended MSc" and "UG Top-Up" name an
# entry route, "MRes - Closed" says the route is withdrawn. A value that is not
# here is not a course row — it is a deadline or fee table that has drifted
# under a course header — and the row is rejected rather than guessed at.

LEVEL_MAP: dict[str, dict[str, Any]] = {
    "UG": {"level": "UG"},
    "PG": {"level": "PG"},
    "PG WITH 2 YEARS": {"level": "PG", "duration": 2.0},
    "PG WITH WORK PLACEMENT": {"level": "PG", "placement": True},
    "PG( CREATIVE ARTS)": {"level": "PG"},
    "PG (CREATIVE ARTS)": {"level": "PG"},
    "MRES": {"level": "PG", "route": "mres"},
    "MRES - CLOSED": {"level": "PG", "route": "mres", "published": False},
    "TOP-UP": {"level": "UG", "course_level": "Top-Up", "route": "top_up"},
    "UG TOP-UP": {"level": "UG", "course_level": "Top-Up", "route": "top_up"},
    "UG- TOP-UP": {"level": "UG", "course_level": "Top-Up", "route": "top_up"},
    "NURSING": {"level": "UG", "route": "nursing"},
    "EXTENDED MASTERS'": {"level": "PG", "route": "extended_masters", "duration": 2.0},
    "EXTENDED MASTERS": {"level": "PG", "route": "extended_masters", "duration": 2.0},
    "ENHANCED EXTENDED MASTERS'": {"level": "PG", "route": "extended_masters", "duration": 2.0},
    "ENHANCED EXTENDED MASTERS": {"level": "PG", "route": "extended_masters", "duration": 2.0},
    "EXTENDED MSC": {"level": "PG", "route": "extended_masters", "duration": 2.0},
    "ADVANCE EXTENDED MSC": {"level": "PG", "route": "extended_masters", "duration": 2.0},
    "ADVANCED PRACTICE": {"level": "PG", "route": "extended_masters", "duration": 2.0},
    "DOCTOR OF BUSINESS ADMINISTRATION (DBA)": {"level": "PG", "route": "dba"},
    "DOCTOR OF BUSINESS ADMINISTRATION(DBA)": {"level": "PG", "route": "dba"},
    # Pathway codes — an ISC sheet's "COURSE LEVEL" column names the pathway,
    # not the study level.
    "IFP": {"route": "international_foundation_year"},
    "IFY": {"route": "international_foundation_year"},
    "IY1": {"route": "international_year_one"},
    "IYO": {"route": "international_year_one"},
    "INTERNATIONAL YEAR ONE": {"route": "international_year_one"},
    "PMP": {"route": "pre_masters"},
    "N/A": {},
}

HEADER_WORDS = (
    TITLE_HEADERS | LEVEL_HEADERS | INTAKE_HEADERS | CAMPUS_HEADERS
    | EXTRA_HEADERS | DURATION_HEADERS | PATHWAY_HEADERS | FEE_HEADERS
    | SCHOLARSHIP_HEADERS
)


def is_header_row(cells: dict[str, str]) -> bool:
    """A repeated table header.

    Some sheets restate the header mid-table with different casing, and one
    writes the tier name into the first cell ("Upper Tier Course | Level |
    Intake | Campus"). Matching on column A alone misses both, so a row counts
    as a header when two or more of its cells are header words.
    """
    hits = sum(1 for value in cells.values() if key(value) in HEADER_WORDS)
    return hits >= 2


def is_tier_heading(value: str) -> str | None:
    text = key(value)
    if "TIER" not in text or len(text) > 40:
        return None
    if text.startswith("LOWER"):
        return "lower"
    if text.startswith("UPPER"):
        return "upper"
    return None


# --- derivations ------------------------------------------------------------

QUALIFICATIONS = [
    "BA (Hons)", "BSc (Hons)", "BEng (Hons)", "LLB (Hons)", "BN (Hons)", "BNurs (Hons)",
    "BMus (Hons)", "BDes (Hons)", "MEng (Hons)", "MSci (Hons)", "BArch (Hons)",
    "FdA", "FdSc", "HND", "HNC",
    "MSc", "MRes", "MBA", "MPH", "MArch", "MEng", "MSci", "MPharm", "MPhil", "MFA", "MMus", "MEd", "MA",
    "PGDip", "PGCert", "PGCE", "LLM", "DBA", "PhD", "EdD",
    "BA", "BSc", "BEng", "LLB", "BN", "BNurs", "BMus", "BDes", "BBA",
]

INTEGRATED_MASTERS_PREFIXES = ("MENG", "MSCI", "MPHARM", "MARCH", "MCHEM", "MPHYS", "MBIOL")

SUBJECT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Computing", (
        "comput", "software", "data science", "big data", "data analytic",
        "artificial intelligence", "cyber", "information technology", "informatics",
        "games development", "games design", "games programming", "games technology",
        "network", "web develop", "machine learning", "robotic", "blockchain",
        "internet of things", "information systems", "digital innovation", "cloud",
    )),
    ("Engineering", (
        "engineering", "engineer", "mechatronic", "manufactur", "aerospace", "aeronautic",
        "automotive", "electronic", "electrical", "motorsport", "civil engineering",
        "quantity surveying", "construction", "built environment",
    )),
    ("Health", (
        "nursing", "nurse", "midwif", "medicine", "medical", "physiotherap",
        "occupational therapy", "paramedic", "public health", "health", "pharmac",
        "dental", "dietetic", "nutrition", "radiograph", "optometry", "podiatr",
        "speech and language", "mental health", "social care", "sport", "exercise",
        "osteopath", "chiropract", "veterinary", "biomedical", "football", "coaching",
        "physical education", "rehabilitation",
    )),
    ("Sciences", (
        "biolog", "chemistr", "physics", "mathemat", "statistic", "environmental",
        "geolog", "geograph", "forensic", "zoolog", "ecolog", "astronom", "neuroscience",
        "biochemistr", "microbiolog", "genetic", "agricultur", "marine", "bioscience",
        "natural science", "applied science", "pharmacolog", "meteorolog",
    )),
    ("Business", (
        "business", "management", "marketing", "accounting", "accountancy", "finance",
        "financial", "economic", "mba", "human resource", "logistics", "supply chain",
        "entrepreneur", "tourism", "hospitality", "event", "banking", "advertising",
        "retail", "international trade", "real estate", "aviation", "airline", "airport",
        "procurement", "consultancy", "enterprise",
    )),
    ("Law", ("law", "llb", "llm", "legal", "criminal justice", "paralegal", "criminal law")),
    ("Arts & Design", (
        "design", "fashion", "film", "media", "music", "photograph", "animation",
        "acting", "theatre", "drama", "dance", "illustration", "fine art", "graphic",
        "architect", "creative writing", "journalism", "performance", "interior",
        "textile", "jewellery", "visual effect", "broadcast", "screenwriting",
        "curat", "publishing", "visual communication", "creative", "directing",
        "augmented reality", "virtual reality", "arts",
    )),
    ("Social Sciences", (
        "psycholog", "sociolog", "politic", "criminolog", "international relations",
        "social work", "social polic", "anthropolog", "development studies",
        "youth", "counselling", "policing", "security studies", "psychosocial",
        "communit", "societ",
    )),
    ("Education", (
        "education", "teaching", "pgce", "early years", "childhood", "pedagog",
        "tesol", "children",
    )),
    ("Humanities", (
        "history", "english", "philosoph", "theolog", "religio", "linguistic",
        "language", "literature", "classic", "heritage", "museum", "archaeolog",
        "cultural studies", "translation",
    )),
]

# Titles that match several buckets get the first hit above. These name the
# cases where that order gets it wrong.
SUBJECT_OVERRIDES: list[tuple[tuple[str, ...], str]] = [
    (("sport management", "sports management", "sport business", "football business"), "Business"),
    (("business psychology", "occupational psychology"), "Social Sciences"),
    (("health management", "healthcare management", "health service management"), "Business"),
    (("art history", "history of art"), "Humanities"),
    (("games art", "game art"), "Arts & Design"),
    (("music technology", "sound engineering", "audio engineering"), "Arts & Design"),
    (("biomedical engineering", "medical engineering"), "Engineering"),
    (("english language teaching", "teaching english"), "Education"),
    (("architectural engineering", "architectural technology"), "Engineering"),
    (("legal technology",), "Law"),
    (("sports journalism", "fashion journalism"), "Arts & Design"),
    (("health and social care", "social care"), "Health"),
    (("computer science", "computing", "software engineering"), "Computing"),
    (("civil engineering", "mechanical engineering", "electrical engineering"), "Engineering"),
    (("nursing", "midwifery"), "Health"),
    (("business management", "international business", "business administration"), "Business"),
    (("early childhood", "childhood studies", "primary education"), "Education"),
]

# Tokens short enough to match inside an unrelated word are matched on a word
# boundary instead of as a substring.
WORD_BOUNDED = {"law", "arts", "sport", "media", "event", "design", "creative", "cloud", "societ", "communit", "children", "youth", "english", "history", "language"}


def _matches(text: str, needle: str) -> bool:
    if needle in WORD_BOUNDED:
        return re.search(rf"\b{re.escape(needle)}", text) is not None
    return needle in text


def derive_qualification(title: str) -> str | None:
    text = collapse(title)
    for qualification in QUALIFICATIONS:
        if text.upper().startswith(qualification.upper() + " "):
            return qualification
    match = re.match(r"^([A-Za-z]{2,6}\s*\(Hons\))\s", text)
    if match:
        return collapse(match.group(1))
    return None


def derive_course_level(title: str, level: str | None, pathway_route: str | None) -> tuple[str | None, str]:
    """Return (course_level, confidence)."""
    text = key(title)
    if "TOP-UP" in text or "TOP UP" in text or "(TOPUP)" in text:
        return "Top-Up", "high"
    if "FOUNDATION YEAR" in text or "WITH FOUNDATION" in text or "INTEGRATED FOUNDATION" in text:
        return "Foundation", "high"
    if pathway_route in ("international_foundation_year",):
        return "Foundation", "high"
    if pathway_route == "international_year_one":
        return "Undergraduate", "high"
    if pathway_route == "pre_masters":
        return "Postgraduate", "medium"
    qualification = (derive_qualification(title) or "").upper().replace(" (HONS)", "")
    if qualification in INTEGRATED_MASTERS_PREFIXES and level in ("UG", None):
        return "Integrated Masters", "high" if level == "UG" else "medium"
    if level == "UG":
        return "Undergraduate", "high"
    if level == "PG":
        return "Postgraduate", "high"
    if qualification in ("MSC", "MA", "MBA", "MRES", "LLM", "PGDIP", "PGCERT", "MPH", "MED", "MFA", "MMUS", "PGCE"):
        return "Postgraduate", "medium"
    if qualification in ("BA", "BSC", "BENG", "LLB", "BN", "BNURS", "BMUS", "BDES", "BBA", "FDA", "FDSC", "HND", "HNC"):
        return "Undergraduate", "medium"
    return None, "none"


def derive_placement(title: str) -> bool:
    text = key(title)
    return any(marker in text for marker in ("PLACEMENT YEAR", "YEAR IN INDUSTRY", "SANDWICH", "WITH PLACEMENT"))


def derive_duration(title: str, course_level: str | None) -> tuple[float | None, str]:
    match = re.search(r"\((\d+(?:\.\d+)?)\s*YEARS?\)", key(title))
    if match:
        return float(match.group(1)), "high"
    if derive_placement(title) and course_level == "Undergraduate":
        return 4.0, "medium"
    defaults = {
        "Foundation": 1.0,
        "Undergraduate": 3.0,
        "Top-Up": 1.0,
        "Integrated Masters": 4.0,
        "Postgraduate": 1.0,
    }
    if course_level in defaults:
        return defaults[course_level], "low"
    return None, "none"


def derive_subject(title: str) -> tuple[str | None, str]:
    text = " " + collapse(title).lower() + " "
    for needles, subject in SUBJECT_OVERRIDES:
        if any(needle in text for needle in needles):
            return subject, "medium"
    hits = [
        subject for subject, needles in SUBJECT_KEYWORDS
        if any(_matches(text, needle) for needle in needles)
    ]
    if not hits:
        return None, "none"
    if len(hits) == 1:
        return hits[0], "medium"
    return hits[0], "low"


MONEY = re.compile(r"£\s*([\d][\d,]*(?:\.\d{2})?)")


def parse_money(text: str) -> list[float]:
    out = []
    for raw in MONEY.findall(text or ""):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return [value for value in out if value >= 1000]


def slugify(value: str) -> str:
    text = collapse(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:180]


def excel_serial_to_date(value: str) -> str | None:
    try:
        return (date(1899, 12, 30) + timedelta(days=int(float(value)))).isoformat()
    except (TypeError, ValueError):
        return None


CRITERIA_FIELDS = (
    "academic_criteria",
    "english_criteria",
    "english_waiver",
    "fee_structure",
    "scholarship_text",
    "gap_policy",
    "cas_deposit",
    "enrolment_fee",
    "deadlines",
    "previous_refusal",
)


def merge_routes(routes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Collapse two matrix columns that normalise to the same route key.

    Two cases exist in the file and both are real:

    - West London prints "Extended Masters" (£19,000) and "Enhanced Extended
      Masters" (£21,000) as separate columns; the route vocabulary maps both to
      `extended_masters`.
    - Aston prints two columns both headed "Postgraduate", with different
      academic criteria and a different net fee.

    Neither is a duplicate, and the schema allows one row per (university,
    route, applicant country). So the columns are concatenated with each
    variant kept under its own heading — the same treatment the criteria
    matrix's own sub-section dividers get. Nothing is dropped, and a reader
    can still see which fee belongs to which offer.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for route in routes:
        grouped[(route["university_slug"], route["route_key"])].append(route)

    merged: list[dict[str, Any]] = []
    notes: list[str] = []
    for (slug, route_key), group in grouped.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        labels = [route.get("label") or route_key for route in group]
        notes.append(
            f"`{slug}` — {len(group)} matrix columns normalise to `{route_key}` "
            f"({', '.join(repr(label) for label in labels)}); concatenated under their own headings"
        )

        base = dict(group[0])
        base["label"] = " / ".join(dict.fromkeys(labels))
        base["display_order"] = min(route.get("display_order", 0) for route in group)
        base["is_published"] = any(route.get("is_published", True) for route in group)

        for field in CRITERIA_FIELDS:
            parts = []
            for route, label in zip(group, labels, strict=True):
                value = route.get(field)
                if value and value not in parts:
                    parts.append(f"{label}\n\n{value}" if len(group) > 1 else value)
            base[field] = "\n\n".join(parts) if parts else None

        extras: dict[str, Any] = {}
        for route in group:
            extras.update(route.get("extras") or {})
        base["extras"] = extras or None

        amounts = sorted({
            amount
            for route in group
            if route.get("fee_proposal")
            for amount in route["fee_proposal"]["amounts"]
        })
        base["fee_proposal"] = (
            {"amounts": amounts, "min": min(amounts), "max": max(amounts), "confidence": "low"}
            if amounts
            else None
        )
        merged.append(base)

    return merged, notes


# --- sheet parsing ----------------------------------------------------------


def parse_criteria_matrix(rows: list[tuple[int, dict[str, str]]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the labels-down / routes-across matrix at the top of a sheet."""
    notes: list[str] = []
    header_row = None
    for number, cells in rows:
        if "RETURN TO MAIN PAGE" in key(cells.get("A")):
            header_row = number
            header_cells = cells
            break
    if header_row is None:
        return [], ["no 'RETURN TO MAIN PAGE' header row — criteria matrix not found"]

    columns: dict[str, dict[str, Any]] = {}
    for column, value in sorted(header_cells.items()):
        if column == "A" or not collapse(value):
            continue
        header = key(value)
        route_key, published = ROUTE_HEADERS.get(header, (None, True))
        if route_key is None:
            notes.append(f"unrecognised route header {collapse(value)!r} in column {column} — skipped")
            continue
        columns[column] = {
            "route_key": route_key,
            "label": collapse(value),
            "is_published": published,
            "fields": {},
            "extras": {},
            "display_order": len(columns),
        }

    # Labels run until the course table starts.
    stop_row = None
    for number, cells in rows:
        if number <= header_row:
            continue
        if key(cells.get("A")) in BLOCK_START_HEADERS:
            stop_row = number
            break
    divider: str | None = None
    for number, cells in rows:
        if not (header_row < number < (stop_row or 10**9)):
            continue
        label = key(cells.get("A"))
        if not label:
            continue

        if not any(collapse(cells.get(column)) for column in columns):
            # A label with no values across any route splits the matrix into
            # sub-sections. UWS states one set of criteria for its London
            # campuses and a second under "SCOTLAND CAMPUS". Both belong to the
            # same route, so they are concatenated — but the divider has to
            # survive, or the merged prose reads as one contradictory block.
            divider = collapse(cells.get("A"))
            notes.append(f"criteria matrix is split by {divider!r} — sections concatenated under that heading")
            continue

        field = CRITERIA_LABELS.get(label)
        for column, route in columns.items():
            value = collapse(cells.get(column))
            if not value:
                continue
            if field:
                if route["fields"].get(field):
                    prefix = f"\n\n{divider}\n\n" if divider and divider not in route["fields"][field] else "\n\n"
                    route["fields"][field] += prefix + value
                else:
                    route["fields"][field] = value
            else:
                route["extras"][collapse(cells.get("A"))] = value
                notes.append(f"unmapped criteria label {collapse(cells.get('A'))!r} -> extras")
    return list(columns.values()), notes


def parse_course_blocks(rows: list[tuple[int, dict[str, str]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], dict[str, int]]:
    """Read every course table on a sheet.

    Sheets carry between one and five tables and the columns differ between
    them, so each header row re-maps the columns for the rows beneath it. The
    tables are also followed by other content — deadline tables, per-course fee
    tables, and appendices listing accepted Indian universities — so a block
    has to end as deliberately as it starts.
    """
    offerings: list[dict[str, Any]] = []
    fee_rows: list[dict[str, Any]] = []
    notes: list[str] = []
    mapping: dict[str, str] | None = None
    tier: str | None = None
    group: str | None = None
    serials = 0
    rejected: Counter = Counter()
    headings: Counter = Counter()

    for number, cells in rows:
        if is_header_row(cells):
            mapping = {}
            for column, value in sorted(cells.items()):
                header = key(value)
                if header in TITLE_HEADERS:
                    mapping[column] = "title"
                elif header in LEVEL_HEADERS:
                    mapping[column] = "level"
                elif header in INTAKE_HEADERS:
                    mapping[column] = "intake"
                elif header in CAMPUS_HEADERS:
                    mapping[column] = "campus"
                elif header in EXTRA_HEADERS:
                    mapping[column] = "extra_requirements"
                elif header in DURATION_HEADERS:
                    mapping[column] = "duration"
                elif header in PATHWAY_HEADERS:
                    mapping[column] = "pathway"
                elif header in FEE_HEADERS:
                    mapping[column] = "fee"
                elif header in SCHOLARSHIP_HEADERS:
                    mapping[column] = "scholarship"
            # The first cell of a course header is often not the word
            # "Course" but a label for the group beneath it — "Upper Tier
            # Course", "LONDON CAMPUS COURSES", "BEng (Hons) COURSES", "OTHER
            # COURSES". When the rest of the row is recognisably a course
            # header, the unlabelled first column is the course title, and its
            # text is the grouping.
            heading = is_tier_heading(cells.get("A", ""))
            label = collapse(cells.get("A"))
            if label and "A" not in mapping:
                mapping["A"] = "title"
            # A course table names its courses in the first column — or in the
            # second, where the first is the pathway programme. Anything else
            # is a different table that merely has a column called "Course":
            # Ravensbourne's CAS deadline grid is "Intake | Course | Course
            # Start Date | ...". Whatever block was open has ended.
            titled = mapping.get("A") == "title" or (
                mapping.get("A") == "pathway" and mapping.get("B") == "title"
            )
            if not titled:
                mapping = None
                tier = None
                group = None
                continue
            tier = heading
            named = key(label) in TITLE_HEADERS or mapping.get("A") == "pathway"
            group = None if heading or named else (label or None)
            continue

        if mapping is None:
            continue

        record = {field: collapse(cells.get(column)) for column, field in mapping.items()}
        filled = {field: value for field, value in record.items() if value}

        if not filled:
            # A blank line inside a table separates groups; it does not end it.
            continue

        if set(filled) == {"title"}:
            heading = is_tier_heading(record["title"])
            if heading:
                tier = heading
                group = None
                continue
            # A lone first cell is a grouping heading inside the table —
            # "LAW COURSES", "Accounting, finance and economics", "2nd year
            # entry". It is never an offering itself, but it does say what the
            # rows beneath it are, which is better evidence of subject than
            # the course title alone.
            #
            # The same shape is also used by the trailing "Deadlines" and "MOI
            # accepted universities" sections. Those are not filtered here:
            # their rows carry a value the LEVEL column vocabulary does not
            # recognise, and are rejected below.
            group = record["title"]
            continue

        title = record.get("title", "")
        if not title:
            continue

        if "fee" in record or "scholarship" in record:
            fee_rows.append({
                "title": title,
                "fee_structure": record.get("fee") or None,
                "scholarship": record.get("scholarship") or None,
            })
            continue

        semantics: dict[str, Any] = {}
        if "level" in mapping.values():
            raw = key(record.get("level"))
            if raw:
                if raw not in LEVEL_MAP:
                    if excel_serial_to_date(record.get("level", "")):
                        serials += 1
                    rejected[record.get("level", "")] += 1
                    continue
                semantics = LEVEL_MAP[raw]
            elif derive_qualification(title) is None:
                # The table states a level for every course. A row that leaves
                # it blank and carries no qualification in its title is a
                # grouping heading that happens to have a stray cell beside it
                # ("Construction and building", "Undergraduate*").
                headings[title] += 1
                continue

        if record.get("intake") and excel_serial_to_date(record["intake"]):
            serials += 1

        offerings.append({
            "title": title,
            "level": semantics.get("level"),
            "route_hint": semantics.get("route"),
            "course_level_hint": semantics.get("course_level"),
            "duration_hint": semantics.get("duration"),
            "placement_hint": semantics.get("placement", False),
            "is_published": semantics.get("published", True),
            "level_raw": record.get("level") or None,
            "pathway_programme": record.get("pathway") or None,
            "campus": record.get("campus") or None,
            "extra_requirements": record.get("extra_requirements") or None,
            "duration_raw": record.get("duration") or None,
            "fee_tier": tier,
            "group": group,
            "row": number,
        })

    if serials:
        notes.append(f"{serials} intake cells were drag-filled Excel serials — discarded")
    for value, count in rejected.most_common():
        notes.append(f"rejected {count} row(s) with unrecognised level {value!r} — not a course table")
    if headings:
        notes.append(
            f"skipped {sum(headings.values())} unlevelled row(s) read as grouping headings: "
            + ", ".join(repr(v) for v in list(headings)[:5])
        )
    stats = {
        "rejected": sum(rejected.values()),
        "headings": sum(headings.values()),
    }
    return offerings, fee_rows, notes, stats


def parse_master_sheet(rows: list[tuple[int, dict[str, str]]], level: str) -> tuple[Counter, list[str]]:
    """Count offerings per university in UG_COURSES / PG_COURSES."""
    counts: Counter = Counter()
    unmapped: list[str] = []
    header = None
    for _number, cells in rows:
        values = {key(v) for v in cells.values()}
        if "UNIVERSITY" in values or "UNIVERSITY NAME" in values:
            header = {key(v): c for c, v in cells.items()}
            continue
        name_column = None
        if header:
            name_column = header.get("UNIVERSITY") or header.get("UNIVERSITY NAME")
        name = collapse(cells.get(name_column or "D"))
        if not name:
            continue
        slug = resolve_university(name)
        if slug is None:
            unmapped.append(name)
        else:
            counts[slug] += 1
    return counts, unmapped


# --- main -------------------------------------------------------------------


def extract(workbook_path: Path, out_dir: Path) -> dict[str, Any]:
    book = Workbook(workbook_path)
    sheet_lookup = {key(name): name for name in book.sheet_names()}

    universities: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    offerings: list[dict[str, Any]] = []
    problems: list[str] = []
    per_sheet_notes: dict[str, list[str]] = {}
    fee_tables: dict[str, list[dict[str, Any]]] = {}

    for entry in UNIVERSITIES:
        sheet_key = key(entry["sheet"])
        actual = sheet_lookup.get(sheet_key)
        if actual is None:
            problems.append(f"{entry['slug']}: sheet {entry['sheet']!r} not found in workbook")
            continue
        rows = book.rows(actual)
        notes: list[str] = []

        display_name = collapse(rows[0][1].get("A")) if rows else ""
        links = book.hyperlinks(actual)
        flyer = next((url for url in links if "drive.google" in url or url.lower().endswith(".pdf")), None)
        if flyer is None and links:
            flyer = links[0]

        matrix, matrix_notes = parse_criteria_matrix(rows)
        notes.extend(matrix_notes)

        blocks, fee_rows, block_notes, block_stats = parse_course_blocks(rows)
        notes.extend(block_notes)
        if fee_rows:
            fee_tables[entry["slug"]] = fee_rows

        route_keys = {route["route_key"] for route in matrix}
        for route in matrix:
            fee_amounts = parse_money(route["fields"].get("fee_structure", ""))
            routes.append({
                "university_slug": entry["slug"],
                "route_key": route["route_key"],
                "label": route["label"],
                "is_published": route["is_published"],
                "display_order": route["display_order"],
                "applicant_country": "NP",
                **route["fields"],
                "extras": route["extras"] or None,
                "fee_proposal": {
                    "amounts": sorted(set(fee_amounts)),
                    "min": min(fee_amounts),
                    "max": max(fee_amounts),
                    "confidence": "low",
                } if fee_amounts else None,
            })

        seen: dict[str, int] = defaultdict(int)
        exact: set[tuple[str, str | None]] = set()
        repeated = 0
        for block in blocks:
            title = block["title"]
            level = block["level"]
            route_hint = block["route_hint"]

            # Some sheets repeat an identical course row verbatim within a
            # single table — the same title at the same level, listed two or
            # three times in a row. That is a transcription artefact.
            #
            # A repeat under a *different* grouping is not: Coventry lists its
            # whole undergraduate catalogue three times, under "2nd year entry"
            # and "3rd year entry" headings, and those are real separate entry
            # points. The grouping, tier and campus are therefore part of an
            # offering's identity.
            identity = (
                key(title),
                block["level_raw"],
                key(block["group"] or ""),
                block["fee_tier"],
                key(block["campus"] or ""),
            )
            if identity in exact:
                repeated += 1
                continue
            exact.add(identity)

            course_level, level_confidence = derive_course_level(title, level, route_hint)
            if block["course_level_hint"]:
                course_level, level_confidence = block["course_level_hint"], "high"
            subject, subject_confidence = derive_subject(title)
            if subject is None and block["group"]:
                subject, subject_confidence = derive_subject(block["group"])
                if subject:
                    subject_confidence = "low"
            duration, duration_confidence = derive_duration(title, course_level)
            if block["duration_hint"]:
                duration, duration_confidence = block["duration_hint"], "high"
            qualification = derive_qualification(title)

            route_key = route_hint
            if route_key is None:
                if course_level == "Top-Up" and "top_up" in route_keys:
                    route_key = "top_up"
                elif level == "PG":
                    route_key = "postgraduate" if "postgraduate" in route_keys else None
                elif level == "UG":
                    route_key = "undergraduate" if "undergraduate" in route_keys else None
            if route_key is not None and route_key not in route_keys:
                # The course names a route the criteria matrix does not carry;
                # keep the offering and leave it unlinked rather than inventing
                # a route with no entry requirements.
                notes.append(f"route {route_key!r} used by a course but absent from the matrix")
                route_key = None

            base = f"{entry['slug']}-{slugify(title)}"
            seen[base] += 1
            slug = base if seen[base] == 1 else f"{base}-{seen[base]}"

            offerings.append({
                "slug": slug,
                "university_slug": entry["slug"],
                "title": title,
                "qualification": qualification,
                "level": level,
                "course_level": course_level,
                "subject": subject,
                "route_key": route_key,
                "pathway_programme": block["pathway_programme"],
                "campus": block["campus"],
                "extra_requirements": block["extra_requirements"],
                "duration_years": duration,
                "placement": derive_placement(title) or block["placement_hint"],
                "fee_tier": block["fee_tier"],
                "course_group": block["group"],
                "intake_name": INTAKE_NAME,
                "is_published": block["is_published"],
                "level_raw": block["level_raw"],
                "confidence": {
                    "course_level": level_confidence,
                    "subject": subject_confidence,
                    "duration_years": duration_confidence,
                },
                "source_row": block["row"],
            })

        if repeated:
            notes.append(f"{repeated} verbatim duplicate course row(s) collapsed")
        block_stats["collapsed"] = repeated

        duplicates = {base: count for base, count in seen.items() if count > 1}
        if duplicates:
            notes.append(f"{len(duplicates)} duplicate course titles disambiguated with a numeric suffix")

        universities.append({
            "slug": entry["slug"],
            "name": entry["name"],
            "sheet": actual,
            "sheet_title": display_name,
            "city": entry["city"],
            "region": entry["region"],
            "flyer_url": flyer,
            "is_pathway_centre": entry["slug"] in PATHWAY_CENTRES,
            "branch_campus_warning": entry["slug"] in BRANCH_CAMPUS_UNIVERSITIES,
            "offering_count": sum(1 for o in offerings if o["university_slug"] == entry["slug"]),
            "route_count": sum(1 for r in routes if r["university_slug"] == entry["slug"]),
            "stats": block_stats,
        })
        if notes:
            per_sheet_notes[entry["slug"]] = notes

    # Cross-check against the master sheets.
    master_counts: Counter = Counter()
    master_unmapped: list[str] = []
    for sheet_name, level in MASTER_SHEETS.items():
        actual = sheet_lookup.get(key(sheet_name))
        if actual is None:
            problems.append(f"master sheet {sheet_name!r} not found")
            continue
        counts, unmapped = parse_master_sheet(book.rows(actual), level)
        master_counts.update(counts)
        master_unmapped.extend(unmapped)

    routes, merge_notes = merge_routes(routes)
    for note in merge_notes:
        per_sheet_notes.setdefault(note.split("`")[1], []).append(note)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "universities.json").write_text(json.dumps(universities, indent=2, ensure_ascii=False) + "\n")
    (out_dir / "routes.json").write_text(json.dumps(routes, indent=2, ensure_ascii=False) + "\n")
    (out_dir / "offerings.json").write_text(json.dumps(offerings, indent=2, ensure_ascii=False) + "\n")

    report = build_report(universities, routes, offerings, master_counts, master_unmapped, per_sheet_notes, problems, fee_tables)
    (out_dir / "report.md").write_text(report)

    return {
        "universities": universities,
        "routes": routes,
        "offerings": offerings,
        "master_counts": master_counts,
        "master_unmapped": master_unmapped,
        "problems": problems,
    }


def build_report(universities, routes, offerings, master_counts, master_unmapped, per_sheet_notes, problems, fee_tables) -> str:
    lines: list[str] = []
    add = lines.append

    add("# Catalogue extraction report")
    add("")
    add(f"Generated by `scripts/extract_xlsx.py` from `{WORKBOOK.name}`.")
    add("")
    add("**Read this before running the import.** Everything below is either a")
    add("derivation the extractor guessed, or a disagreement between two parts of")
    add("the workbook. Nothing here is fatal on its own; all of it is a judgement")
    add("call that belongs to staff, not to a script.")
    add("")

    add("## Totals")
    add("")
    add("| | |")
    add("|---|---|")
    add(f"| Universities | {len(universities)} |")
    add(f"| Entry routes | {len(routes)} |")
    add(f"| Course offerings | {len(offerings)} |")
    add(f"| Master-sheet cross-check total | {sum(master_counts.values())} |")
    add(f"| Unmapped university names | {len(set(master_unmapped))} |")
    add("")

    if problems:
        add("## Blocking problems")
        add("")
        for problem in problems:
            add(f"- {problem}")
        add("")

    if master_unmapped:
        add("## Unmapped names in the master sheets")
        add("")
        add("These strings are not in the alias map. Add them to")
        add("`scripts/catalogue_source.py` — never let the importer create a new")
        add("university from one.")
        add("")
        for name, count in Counter(master_unmapped).most_common():
            add(f"- `{name}` ({count} rows)")
        add("")

    add("## Per-university counts")
    add("")
    add("`sheet` is authoritative. `master` is the cross-check. A large negative")
    add("delta means the master sheets are missing that university's courses —")
    add("expected, and the reason the university sheets win.")
    add("")
    add("| slug | kept | master | delta | collapsed | rejected | routes |")
    add("|---|---:|---:|---:|---:|---:|---:|")
    for uni in sorted(universities, key=lambda u: -u["offering_count"]):
        master = master_counts.get(uni["slug"], 0)
        delta = uni["offering_count"] - master
        stats = uni.get("stats", {})
        collapsed = stats.get("collapsed", 0)
        dropped = stats.get("rejected", 0) + stats.get("headings", 0)
        add(f"| `{uni['slug']}` | {uni['offering_count']} | {master} | {delta:+d} | "
            f"{collapsed or ''} | {dropped or ''} | {uni['route_count']} |")
    add("")
    add("`collapsed` counts rows repeated verbatim within one table; `rejected`")
    add("counts rows under a course header that are not courses — deadline and")
    add("fee grids, and grouping headings. Together they explain most negative")
    add("deltas: the master sheets count those rows, this extraction does not.")
    add("")
    add("Positive deltas are expected and are the reason the per-university")
    add("sheets are authoritative — the master sheets are an incomplete")
    add("aggregate. Essex is the extreme case: 264 courses on its own sheet,")
    add("13 in the masters.")
    add("")

    zero = [u for u in universities if u["offering_count"] == 0]
    if zero:
        add("### Universities with no offerings")
        add("")
        for uni in zero:
            add(f"- `{uni['slug']}` — sheet {uni['sheet']!r}. Check the course table header.")
        add("")

    add("## Derivations")
    add("")
    add("Every field below was inferred from the course title. The spreadsheet")
    add("does not state any of them.")
    add("")
    for field in ("course_level", "subject", "duration_years"):
        counts = Counter(o["confidence"][field] for o in offerings)
        total = sum(counts.values())
        add(f"**`{field}`** — " + ", ".join(f"{level}: {count} ({count * 100 // max(total, 1)}%)" for level, count in counts.most_common()))
        add("")

    missing_subject = [o for o in offerings if o["subject"] is None]
    low_subject = [o for o in offerings if o["confidence"]["subject"] == "low"]
    add(f"`subject` is a keyword classifier and **will misfile some courses**. "
        f"{len(missing_subject)} offerings got no subject at all and "
        f"{len(low_subject)} matched more than one bucket.")
    add("")
    add("Work the backlog in the admin course table, which filters on")
    add("`subject IS NULL`. A sample of the unclassified:")
    add("")
    for offering in missing_subject[:25]:
        add(f"- `{offering['university_slug']}` — {offering['title']}")
    if len(missing_subject) > 25:
        add(f"- …and {len(missing_subject) - 25} more")
    add("")

    add("### Multi-bucket titles (first match won)")
    add("")
    for offering in low_subject[:20]:
        add(f"- {offering['title']} → **{offering['subject']}**")
    if len(low_subject) > 20:
        add(f"- …and {len(low_subject) - 20} more")
    add("")

    add("## Fees")
    add("")
    add("Fee amounts were read out of `FEE STRUCTURE` prose. They are a")
    add("**proposal only** and are never applied automatically — the prose stays")
    add("authoritative, and `fee_tier` preserves the LOWER/UPPER grouping.")
    add("")
    with_fees = [r for r in routes if r.get("fee_proposal")]
    add(f"{len(with_fees)} of {len(routes)} routes yielded at least one £ amount.")
    add("")
    add("Every amount found is listed, not just the extremes: the lowest number")
    add("in a fee cell is often a placement supplement or a deposit rather than")
    add("a tuition floor, so `min` alone would be actively misleading.")
    add("")
    add("| university | route | amounts | prose |")
    add("|---|---|---|---|")
    for route in with_fees[:30]:
        prose = collapse(route.get("fee_structure", ""))[:60]
        amounts = ", ".join(f"{value:,.0f}" for value in route["fee_proposal"]["amounts"][:6])
        add(f"| `{route['university_slug']}` | {route['route_key']} | {amounts} | {prose} |")
    if len(with_fees) > 30:
        add(f"| … | … | | | {len(with_fees) - 30} more |")
    add("")

    if fee_tables:
        add("### Per-course fee tables")
        add("")
        add("Some sheets carry a second table keyed by course rather than by")
        add("route. These are not offerings and are not imported as such; they")
        add("are recorded here so nothing in the file is lost.")
        add("")
        for slug, entries in fee_tables.items():
            add(f"- `{slug}`: {len(entries)} rows — e.g. {entries[0]['title']!r}")
        add("")

    add("## Intake")
    add("")
    add("The workbook's intake column is a drag-fill artefact: the values are")
    add("Excel serials incrementing by one per row, not real start dates. The")
    add("column is **discarded**. Every offering carries a single named intake,")
    add(f"`{INTAKE_NAME}`.")
    add("")

    add("## Cities and regions")
    add("")
    add("`region` is proposed from the city and **must be confirmed by staff**.")
    add("")
    branch = [u for u in universities if u["branch_campus_warning"]]
    add("These list branch campuses rather than their home city in the course")
    add("sheets. The city below is the institution's actual home; the")
    add("per-offering `campus` keeps the branch string verbatim.")
    add("")
    for uni in branch:
        campuses = Counter(o["campus"] for o in offerings if o["university_slug"] == uni["slug"] and o["campus"])
        seen = ", ".join(f"{name} ({count})" for name, count in campuses.most_common(4)) or "—"
        add(f"- `{uni['slug']}` → **{uni['city']}** / {uni['region']}; campuses in file: {seen}")
    add("")
    add("`ulster` is the region most likely to be wrong: it is a Northern Irish")
    add("institution but every campus in this file is in England.")
    add("")

    add("## Pathway centres")
    add("")
    add("The four ISC rows are INTO/Study Group pathway centres marketed as")
    add("\"Progression Degree for Pathway\". They are separate universities on")
    add("purpose — their fees and criteria differ from the parent institution.")
    add("Their course tables have a different shape: the course title is the")
    add("**progression degree**, and the pathway programme is carried alongside.")
    add("")
    for uni in universities:
        if uni["is_pathway_centre"]:
            add(f"- `{uni['slug']}` — {uni['offering_count']} progression degrees")
    add("")

    add("## Flyers")
    add("")
    missing = [u["slug"] for u in universities if not u["flyer_url"]]
    add(f"{len(universities) - len(missing)} of {len(universities)} sheets carry a flyer link.")
    if missing:
        add("")
        add("Missing: " + ", ".join(f"`{slug}`" for slug in missing))
    add("")

    if per_sheet_notes:
        add("## Per-sheet notes")
        add("")
        for slug, notes in per_sheet_notes.items():
            add(f"**`{slug}`**")
            add("")
            for note in notes:
                add(f"- {note}")
            add("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=WORKBOOK)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    if not args.workbook.exists():
        print(f"workbook not found: {args.workbook}", file=sys.stderr)
        return 1

    result = extract(args.workbook, args.out)

    universities = result["universities"]
    offerings = result["offerings"]
    unmapped = set(result["master_unmapped"])

    print(f"universities : {len(universities)}")
    print(f"routes       : {len(result['routes'])}")
    print(f"offerings    : {len(offerings)}")
    print(f"unmapped     : {len(unmapped)}")
    print(f"written to   : {args.out}")

    failures = []
    if len(universities) != 44:
        failures.append(f"expected 44 universities, got {len(universities)}")
    if unmapped:
        failures.append(f"unmapped university names: {sorted(unmapped)}")
    empty = [u["slug"] for u in universities if u["offering_count"] == 0]
    if empty:
        failures.append(f"universities with no offerings: {empty}")
    unresolved = [o["slug"] for o in offerings if o["university_slug"] not in {u["slug"] for u in universities}]
    if unresolved:
        failures.append(f"offerings with no university: {len(unresolved)}")
    for problem in result["problems"]:
        failures.append(problem)

    if failures:
        print("\nFAILED", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("\nOK — 44 universities, 0 unmapped names, every offering resolves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
