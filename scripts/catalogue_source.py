"""Canonical vocabularies for the September 2026 intake workbook.

Separated from the extractor so the importer (Phase 3) can reuse the alias map
without importing spreadsheet-parsing code.

Everything here is transcribed from CATALOGUE-CMS-PLAN.md sections 4.4/4.5/5.2,
corrected where the workbook disagreed with the plan (see report.md).
"""

from __future__ import annotations

import re

# --- universities -----------------------------------------------------------
# slug, workbook sheet (as written, before whitespace normalisation), display
# name, modal city, proposed region. Region is a PROPOSAL and must be confirmed
# by staff; see report.md.

UNIVERSITIES: list[dict[str, str]] = [
    {"slug": "york-st-john", "sheet": "YSJU", "name": "York St John University", "city": "York", "region": "England — North"},
    {"slug": "worcester", "sheet": "WORCESTER", "name": "University of Worcester", "city": "Worcester", "region": "England — Midlands"},
    {"slug": "east-london", "sheet": "UEL", "name": "University of East London", "city": "London", "region": "England — South"},
    {"slug": "west-london", "sheet": "UWL", "name": "University of West London", "city": "London", "region": "England — South"},
    {"slug": "university-college-birmingham", "sheet": "UCB", "name": "University College Birmingham", "city": "Birmingham", "region": "England — Midlands"},
    {"slug": "hertfordshire", "sheet": "HERTS", "name": "University of Hertfordshire", "city": "Hatfield", "region": "England — South"},
    {"slug": "bedfordshire", "sheet": "BEDFORDSHIRE", "name": "University of Bedfordshire", "city": "Luton", "region": "England — South"},
    {"slug": "leeds-trinity", "sheet": "LTU", "name": "Leeds Trinity University", "city": "Leeds", "region": "England — North"},
    {"slug": "arden", "sheet": "ARDEN", "name": "Arden University", "city": "London", "region": "England — South"},
    {"slug": "creative-arts", "sheet": "UCA", "name": "University for the Creative Arts", "city": "Farnham", "region": "England — South"},
    {"slug": "buckinghamshire-new", "sheet": "BNU", "name": "Buckinghamshire New University", "city": "High Wycombe", "region": "England — South"},
    {"slug": "middlesex", "sheet": "MDX", "name": "Middlesex University", "city": "London", "region": "England — South"},
    {"slug": "regent-college-london", "sheet": "REGENT", "name": "Regent College London", "city": "London", "region": "England — South"},
    {"slug": "plymouth", "sheet": "PLYMOUTH", "name": "University of Plymouth", "city": "Plymouth", "region": "England — South"},
    {"slug": "wolverhampton", "sheet": "WOLVERHAMPTON", "name": "University of Wolverhampton", "city": "Wolverhampton", "region": "England — Midlands"},
    {"slug": "ravensbourne", "sheet": "RAVENSBOURNE", "name": "Ravensbourne University London", "city": "London", "region": "England — South"},
    {"slug": "roehampton", "sheet": "ROEHAMPTON", "name": "University of Roehampton", "city": "London", "region": "England — South"},
    {"slug": "health-science", "sheet": "HSU", "name": "Health Science University", "city": "London", "region": "England — South"},
    {"slug": "winchester", "sheet": "WINCHESTER", "name": "University of Winchester", "city": "Winchester", "region": "England — South"},
    {"slug": "portsmouth", "sheet": "PORTSMOUTH", "name": "University of Portsmouth", "city": "Portsmouth", "region": "England — South"},
    {"slug": "lancashire", "sheet": "UCLAN", "name": "University of Lancashire", "city": "Preston", "region": "England — North"},
    {"slug": "salford", "sheet": "SALFORD", "name": "University of Salford", "city": "Manchester", "region": "England — North"},
    {"slug": "essex", "sheet": "ESSEX", "name": "University of Essex", "city": "Colchester", "region": "England — South"},
    {"slug": "cardiff-metropolitan", "sheet": "CARDIFF", "name": "Cardiff Metropolitan University", "city": "Cardiff", "region": "Wales"},
    {"slug": "birmingham-city", "sheet": "BCU", "name": "Birmingham City University", "city": "Birmingham", "region": "England — Midlands"},
    {"slug": "bath-spa", "sheet": "BATHSPA", "name": "Bath Spa University", "city": "Bath", "region": "England — South"},
    {"slug": "brighton", "sheet": "BRIGHTON", "name": "University of Brighton", "city": "Brighton", "region": "England — South"},
    {"slug": "chester", "sheet": "CHESTER", "name": "University of Chester", "city": "Chester", "region": "England — North"},
    {"slug": "aston", "sheet": "ASTON", "name": "Aston University", "city": "Birmingham", "region": "England — Midlands"},
    {"slug": "hull", "sheet": "HULL", "name": "University of Hull", "city": "Hull", "region": "England — North"},
    {"slug": "northumbria", "sheet": "NORTHUMBRIA", "name": "Northumbria University", "city": "Newcastle", "region": "England — North"},
    {"slug": "sunderland", "sheet": "SUNDERLAND", "name": "University of Sunderland", "city": "Sunderland", "region": "England — North"},
    {"slug": "london-metropolitan", "sheet": "LMU", "name": "London Metropolitan University", "city": "London", "region": "England — South"},
    {"slug": "coventry", "sheet": "COVENTRY", "name": "Coventry University", "city": "Coventry", "region": "England — Midlands"},
    {"slug": "law", "sheet": "LAW", "name": "University of Law", "city": "London", "region": "England — South"},
    {"slug": "ulster", "sheet": "ULSTER", "name": "Ulster University", "city": "Belfast", "region": "Northern Ireland"},
    {"slug": "west-of-scotland", "sheet": "UWS", "name": "University of the West of Scotland", "city": "Paisley", "region": "Scotland"},
    {"slug": "canterbury-christ-church", "sheet": "CCCU", "name": "Canterbury Christ Church University", "city": "Canterbury", "region": "England — South"},
    {"slug": "edinburgh-napier", "sheet": "EDINBURGH", "name": "Edinburgh Napier University", "city": "Edinburgh", "region": "Scotland"},
    {"slug": "wales-trinity-saint-david", "sheet": "UWTSD", "name": "University of Wales Trinity Saint David", "city": "Swansea", "region": "Wales"},
    {"slug": "london-metropolitan-isc", "sheet": "LMUISC", "name": "London Metropolitan University ISC", "city": "London", "region": "England — South"},
    {"slug": "wolverhampton-isc", "sheet": "UOWISC", "name": "University of Wolverhampton ISC", "city": "Wolverhampton", "region": "England — Midlands"},
    {"slug": "liverpool-hope-isc", "sheet": "LHUISC", "name": "Liverpool Hope University ISC", "city": "Liverpool", "region": "England — North"},
    {"slug": "cumbria-isc", "sheet": "UCIC", "name": "University of Cumbria ISC", "city": "Carlisle", "region": "England — North"},
]

BY_SLUG = {u["slug"]: u for u in UNIVERSITIES}
BY_SHEET = {u["sheet"]: u for u in UNIVERSITIES}

# Six institutions list branch campuses rather than their home city in the
# course sheets. The table above uses the real home city; the per-offering
# campus column keeps the branch string verbatim.
BRANCH_CAMPUS_UNIVERSITIES = {"ulster", "aston", "coventry", "law", "hull", "west-of-scotland"}

# The pathway centres are separate institutions on purpose: their fees and
# criteria differ from the parent.
PATHWAY_CENTRES = {"london-metropolitan-isc", "wolverhampton-isc", "liverpool-hope-isc", "cumbria-isc"}


def normalise_name(value: str) -> str:
    """Collapse whitespace so the double-spaced and padded variants match."""
    return re.sub(r"\s+", " ", (value or "").strip())


# --- name variants ----------------------------------------------------------
# Every distinct string in UG_COURSES.D / PG_COURSES.D, whitespace-collapsed.
# A name that misses this map is a hard error, never a new university.

ALIASES: dict[str, str] = {
    "Arden University": "arden",
    "Aston University": "aston",
    "University of Aston": "aston",
    "Bath Spa University": "bath-spa",
    "Birmingham City University": "birmingham-city",
    "Buckinghamshire New University": "buckinghamshire-new",
    "Canterbury Christ Church University": "canterbury-christ-church",
    "Canterbury Christ Church Cniversity": "canterbury-christ-church",
    "Cardiff Metropolitan University": "cardiff-metropolitan",
    "Coventry University": "coventry",
    "University of Coventry": "coventry",
    "Edinburgh Napier University": "edinburgh-napier",
    "Health Science University": "health-science",
    "Leeds Trinity University": "leeds-trinity",
    "Liverpool Hope University ISC(Progression Degree for Pathway)": "liverpool-hope-isc",
    "London Metropolitan University": "london-metropolitan",
    "London Metropolitan University ISC (Progression Degree for Pathway)": "london-metropolitan-isc",
    "Middlesex University": "middlesex",
    "Northumbria University": "northumbria",
    "University of Northumbria": "northumbria",
    "Ravensbourne University": "ravensbourne",
    "University of Ravensbourne": "ravensbourne",
    "Regent College London": "regent-college-london",
    "Ulster University": "ulster",
    "University College Birmingham": "university-college-birmingham",
    "University for the Creative Arts": "creative-arts",
    "University of Bedfordshire": "bedfordshire",
    "University of Brighton": "brighton",
    "University of Chester": "chester",
    "University of Cumbria ISC(Progression Degree for Pathway)": "cumbria-isc",
    "University of East London": "east-london",
    "University of Essex": "essex",
    "University of Hertfordshire": "hertfordshire",
    "University of Hull": "hull",
    "University of Lancashire": "lancashire",
    "University of Law": "law",
    "University of Plymouth": "plymouth",
    "University of Portsmouth": "portsmouth",
    "University of Roehampton": "roehampton",
    "University of Salford": "salford",
    "University of Sunderland": "sunderland",
    "University of Wales Trinity Saint David": "wales-trinity-saint-david",
    "University of West London": "west-london",
    "University of West of Scotland": "west-of-scotland",
    "University of Winchester": "winchester",
    "University of Wolverhampton": "wolverhampton",
    "University of Wolverhampton ISC(Progression Degree for Pathway)": "wolverhampton-isc",
    "University of Worcester": "worcester",
    "York St. John University": "york-st-john",
}

ALIAS_LOOKUP = {normalise_name(k).upper(): v for k, v in ALIASES.items()}


def resolve_university(name: str) -> str | None:
    return ALIAS_LOOKUP.get(normalise_name(name).upper())


# --- entry routes -----------------------------------------------------------

ROUTE_KEYS = [
    "undergraduate",
    "international_year_one",
    "international_foundation_year",
    "pre_masters",
    "postgraduate",
    "top_up",
    "extended_masters",
    "mres",
    "dba",
    "nursing",
]

# header (normalised, upper) -> (route_key, is_published)
ROUTE_HEADERS: dict[str, tuple[str, bool]] = {
    "UNDERGRADUATE": ("undergraduate", True),
    "DIRECT UNDERGRADUATE": ("undergraduate", True),
    "UDERGRADUATE": ("undergraduate", True),
    "POSTGRADUATE": ("postgraduate", True),
    "INTERNATIONAL YEAR ONE": ("international_year_one", True),
    "INTERNATIONAL FOUNDATION YEAR": ("international_foundation_year", True),
    "PRE-MASTERS": ("pre_masters", True),
    "TOP-UP": ("top_up", True),
    "EXTENDED MASTERS": ("extended_masters", True),
    "ENHANCED EXTENDED MASTERS": ("extended_masters", True),
    "MRES": ("mres", True),
    "MRES - NO LONGER AVAILABLE FOR SEP 2026": ("mres", False),
    "DOCTOR OF BUSINESS ADMINISTRATION(DBA)": ("dba", True),
    "NURSING": ("nursing", True),
    "BNURS(ADULT NURSING)": ("nursing", True),
}

# --- criteria matrix labels -------------------------------------------------
# Row label (normalised, upper) -> field on university_routes. Anything not
# here lands in `extras` rather than being dropped.

CRITERIA_LABELS: dict[str, str] = {
    "ACADEMIC CRITERIA": "academic_criteria",
    "ENGLISH LANGUAGE CRITERIA": "english_criteria",
    "ENGLISH WAIVER CRITERIA": "english_waiver",
    "ENGLISH WAIVER": "english_waiver",
    "ENGLISH LANGUAGE WAIVER": "english_waiver",
    "ENGLISH WAAIVER CRITERIA": "english_waiver",
    "FEE STRUCTURE": "fee_structure",
    "SCHOLARSHIP": "scholarship_text",
    "GAP": "gap_policy",
    "CAS DEPOSIT": "cas_deposit",
    "ENROLLMENT FEE": "enrolment_fee",
    "ENROLMENT FEE": "enrolment_fee",
    "DEADLINES": "deadlines",
    "PREVIOUS REFUSAL CASE": "previous_refusal",
}

INTAKE_NAME = "September 2026"
