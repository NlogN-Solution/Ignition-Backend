"""Architectural guard: Ignition is single-tenant.

ED360's backend is being ported here with multi-tenancy stripped (strip rules
R1-R10 in IGNITION_PLATFORM_PLAN.md). The port is mechanical and touches 74
files, so it is entirely possible to miss an `organization_id` parameter or a
leftover `scoped_org_id` call. This test fails the build if any survive.

It is deliberately a test rather than a lint rule so it shows up in the same
place as every other regression.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

BANNED = {
    "organization": "Ignition has no Organization — see strip rules R1/R3.",
    "organisation": "Ignition has no Organization — see strip rules R1/R3.",
    "scoped_org_id": "Tenant scoping helper was deleted — see strip rule R2.",
    "TenantMixin": "Tenant mixins were deleted — see strip rule R1.",
    "is_platform_admin": "There is no platform above the single tenant — see strip rule R3.",
    "require_platform_admin": "Deleted with the platform-admin surface — see strip rule R6.",
    "OrganizationSubscription": "Seat/quota billing is multi-tenant only — Bucket A.",
    "OrgSubscriptionPlan": "Seat/quota billing is multi-tenant only — Bucket A.",
}

# Substring match, case-insensitive, and deliberately NOT \b-anchored: the
# leftovers this guard exists to catch are `organization_id`, `organizations`,
# and `NullableTenantMixin`, none of which sit on word boundaries. Longest term
# first so the most specific message wins when two terms overlap.
PATTERN = re.compile(
    "|".join(re.escape(term) for term in sorted(BANNED, key=len, reverse=True)),
    re.IGNORECASE,
)

# The match keeps the casing it had in the source, so reasons are looked up
# case-insensitively.
REASONS = {term.lower(): reason for term, reason in BANNED.items()}


def _docstring_lines(source: str) -> set[int]:
    """Line numbers occupied by docstrings.

    Naming what Ignition deliberately does *not* have is the whole point of
    several docstrings ("ED360 kept one row per organization; here..."), so
    prose is exempt. Only docstrings are — every other string literal is still
    checked, because a banned term inside e.g. raw SQL or a `getattr` name is a
    real reference, not documentation.
    """
    exempt: set[int] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and first.end_lineno is not None
        ):
            exempt.update(range(first.lineno, first.end_lineno + 1))
    return exempt


def test_no_tenancy_references_survive_the_port() -> None:
    offenders: list[str] = []

    for path in sorted(APP_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        exempt = _docstring_lines(source)

        for lineno, line in enumerate(source.splitlines(), start=1):
            # Comments and docstrings may name the banned concepts to explain
            # their absence; that explanation is the point, so let it through.
            if lineno in exempt or line.lstrip().startswith("#"):
                continue
            match = PATTERN.search(line)
            if match:
                term = match.group(0)
                rel = path.relative_to(APP_DIR.parent)
                offenders.append(f"{rel}:{lineno}: {term!r} — {REASONS[term.lower()]}")

    assert not offenders, "Multi-tenancy references survived the port:\n  " + "\n  ".join(offenders)
