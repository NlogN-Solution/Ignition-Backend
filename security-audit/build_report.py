"""Render SECURITY_AUDIT_REPORT.md and SECURITY_AUDIT_REPORT.html from one source.

Canonical data: findings.json + endpoint-security-matrix.csv + the section
blocks below. Both outputs are produced from the same block list, so the
Markdown and HTML (and the PDF printed from the HTML) cannot drift apart.

    python3 security-audit/build_report.py
    chromium --headless --no-pdf-header-footer \
        --print-to-pdf=security-audit/SECURITY_AUDIT_REPORT.pdf security-audit/SECURITY_AUDIT_REPORT.html
"""

from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = json.loads((HERE / "findings.json").read_text())
META = DATA["metadata"]
FINDINGS = DATA["findings"]
ROUTES = list(csv.DictReader((HERE / "endpoint-security-matrix.csv").open()))
SEV_ORDER = ["Critical", "High", "Medium", "Low", "Informational"]

# ── Block model ────────────────────────────────────────────────────────────────
# ("h", level, text) | ("p", text) | ("ul", [items]) | ("table", headers, rows)
# ("code", text) | ("kv", [(k, v)]) | ("pagebreak",)
blocks: list[tuple] = []


def h(level: int, text: str) -> None:
    blocks.append(("h", level, text))


def p(text: str) -> None:
    blocks.append(("p", text))


def ul(items: list[str]) -> None:
    blocks.append(("ul", items))


def table(headers: list[str], rows: list[list[str]]) -> None:
    blocks.append(("table", headers, rows))


def code(text: str) -> None:
    blocks.append(("code", text))


def kv(pairs: list[tuple[str, str]]) -> None:
    blocks.append(("kv", pairs))


def pagebreak() -> None:
    blocks.append(("pagebreak",))


# ── Derived numbers ────────────────────────────────────────────────────────────
sev_counts = Counter(f["severity"] for f in FINDINGS)
vulns = [f for f in FINDINGS if f["category"] == "vulnerability"]
hardening = [f for f in FINDINGS if f["category"] == "hardening"]
public_routes = [r for r in ROUTES if r["authentication"].startswith("None")]
auth_only = [r for r in ROUTES if r["authorization_dependency"] == "any authenticated user"]
student_routes = [r for r in ROUTES if "student" in r["authorization_dependency"]]
routes_with_findings = [r for r in ROUTES if r["findings"]]

# ── Cover ──────────────────────────────────────────────────────────────────────
h(1, "FastAPI Backend Security Audit — Ignition")
kv(
    [
        ("Repository", META["repository"]),
        ("Branch", META["branch"]),
        ("Commit SHA", META["commit"]),
        ("Audit date", META["audit_date"]),
        ("Auditor", META["auditor"]),
        ("Specification", META["specification"]),
        ("Scope", META["scope"]),
    ]
)
pagebreak()

# ── Executive summary ──────────────────────────────────────────────────────────
h(2, "1. Executive summary")
p(
    "Ignition's backend is well defended at the level most APIs get wrong. Every one of the 339 routes declares "
    "an authentication dependency, and a test fails the build if one does not. Public self-registration cannot set "
    "a role. JWT handling is correct, refresh tokens are rotated and stored server-side, uploaded file names are "
    "discarded, message HTML is sanitised on input, and the student portal reads through a repository that always "
    "filters by the calling student."
)
p(
    f"The weaknesses are in the next layer down: checks on which object a request may touch, and on business "
    f"flows. The audit confirmed {len(vulns)} vulnerabilities (no Critical or High; "
    f"{sev_counts['Medium']} Medium, {sev_counts['Low']} Low) and {len(hardening)} informational hardening items. "
    f"All confirmed vulnerabilities except the two that depend on the deployment environment were reproduced "
    f"with local, non-destructive regression tests against the ignition_test database."
)
p("The highest-priority problems:")
ul(
    [
        "FAPI-SEC-001: the simulated checkout is on by default and nothing turns it off in production, so any "
        "self-registered student can unlock the paid portal for free.",
        "FAPI-SEC-002: a checklist item accepts any document id. Students can self-verify requirements with an "
        "unrelated approved file, or link another student's document.",
        "FAPI-SEC-005: the counsellor 'own work only' privacy rule, enforced on /applications, /leads and the "
        "legacy /messages, is missing from the new /communication router (including internal staff notes) and "
        "from the application workflow/checklist routes.",
        "FAPI-SEC-010 (needs environment verification): rate limits key on the Render proxy's IP, which turns "
        "per-client login limits into one global bucket that an anonymous caller can exhaust.",
    ]
)

h(3, "Overall risk posture")
p(
    "Moderate. No finding gives an unauthenticated attacker access to personal data or privileged functions. "
    "The confirmed issues need an account (free for students) or a staff role, and several need a victim's random "
    "UUID. The business-flow bypass (FAPI-SEC-001) and the internal-privacy gap (FAPI-SEC-005) are the ones with "
    "direct real-world consequences. No numeric score is given, by design."
)

# ── Scope ──────────────────────────────────────────────────────────────────────
h(2, "2. Scope and exclusions")
p("In scope: " + META["scope"])
p("Excluded: " + META["exclusions"])

# ── Architecture ───────────────────────────────────────────────────────────────
h(2, "3. Repository and architecture overview")
ul(
    [
        "Stack: Python 3.12, FastAPI 0.141.1 / Starlette 1.3.1, Uvicorn 0.52, Pydantic 2.13, SQLAlchemy 2.0 "
        "(asyncpg), Alembic, PyJWT 2.13 (HS256), bcrypt 5.0, slowapi (Redis-backed), Cloudinary for media, httpx "
        "for the landing revalidation webhook.",
        "Entry point app/main.py: CORS (explicit origins; LAN regex only in development), a request-id middleware, "
        "a generic 500 handler (no stack traces to clients), and OpenAPI/docs disabled when ENVIRONMENT=production.",
        "29 routers mounted under /api/v1 by app/api/router.py; no WebSockets, SSE, static mounts or background "
        "workers (events run in-process after commit; the docker-compose Celery worker points to a module that "
        "does not exist).",
        "Data: single-tenant PostgreSQL (Neon in production), Redis (rate limits, dashboard cache, public "
        "response cache).",
        "Deployment: Docker (non-root uid 1001, slim image), Render Blueprint (render.yaml) running "
        "`alembic upgrade head` before each deploy.",
        "Trust boundaries: anonymous (public catalogue, eligibility, apply-intent, register/login); student "
        "(self-registered); staff roles (viewer, counsellor, frontdesk, staff, finance, marketing, support, "
        "admissions, manager, admin, super_admin); external services (Cloudinary, landing site webhook).",
    ]
)

h(2, "4. Authentication and authorization architecture")
ul(
    [
        "Bearer JWT (HTTPBearer). Access tokens last 15 minutes with type=access; refresh tokens last 30 days, "
        "carry a jti, are stored as a SHA-256 hash in user_sessions, rotate on every use and are revoked on "
        "logout and password change. Algorithms are pinned to settings.JWT_ALGORITHM, so alg=none is rejected "
        "(verified). A refresh token used as a bearer token is rejected (verified).",
        "Production refuses to boot with a missing, weak (<32 chars) or placeholder JWT secret, or without "
        "Cloudinary keys. Development generates a per-process secret.",
        "Passwords: bcrypt (cost 12) over base64(SHA-256(password)), which avoids 72-byte truncation. Timing is "
        "equalised for unknown accounts. Lockout after 5 failures for 15 minutes (see FAPI-SEC-011).",
        "Authorization dependencies: get_current_user (active, not deleted, checked per request), require_role "
        "(super_admin always passes), require_owner, require_staff, get_current_student, require_public. "
        "tests/test_endpoint_authorization.py fails the build if a route has no marker or if the public set "
        "changes.",
        "Object level: StudentScopedRepository for the student portal; inline owner checks on documents, "
        "appointments, payments, notifications, leave, payroll and attendance; can_manage_target rank rule for "
        "user administration; counsellor own-work scoping (api/scoping.py, services/message_scope.py) on leads, "
        "applications and legacy messages.",
        "No cookies, so CSRF does not apply. There are no password-reset or e-mail-verification flows yet: staff "
        "reset passwords administratively and temporary passwords are returned once.",
    ]
)

# ── Attack surface ─────────────────────────────────────────────────────────────
h(2, "5. Attack surface and endpoint inventory summary")
by_level = Counter(r["authorization_dependency"] for r in ROUTES)
table(
    ["Metric", "Count"],
    [
        ["HTTP endpoints discovered (method + path)", str(len(ROUTES))],
        ["Route decorators in app/routes (cross-check)", "339"],
        ["Unauthenticated endpoints", str(len(public_routes))],
        ["Authenticated, no role restriction (object checks inline)", str(len(auth_only))],
        ["Student-role endpoints", str(len(student_routes))],
        ["Endpoints mapped to at least one finding", str(len(routes_with_findings))],
        ["Endpoints marked Reviewed in endpoint-security-matrix.csv", str(sum(r["review_status"] == "Reviewed" for r in ROUTES))],
    ],
)
p("Unauthenticated surface:")
table(["Method", "Path"], [[r["method"], r["path"]] for r in public_routes])
p("The full per-route matrix (authentication, role dependency, object ids, object-level control, body, response "
  "model, review status, findings) is in endpoint-security-matrix.csv.")

# ── Methodology ────────────────────────────────────────────────────────────────
h(2, "6. Methodology and tools actually executed")
ul(
    [
        "Phase 0, inventory: repository layout, configuration, deployment files. The route inventory was built "
        "by introspecting the live FastAPI app with the project's own traversal (FastAPI 0.141 nests included "
        "routers) and cross-checked against the 339 decorators in app/routes.",
        "Phase 1, deterministic: pytest (existing suite), ruff, mypy. Bandit, pip-audit, Semgrep and a secret "
        "scanner are not installed, and per the specification were not installed silently (see section 10).",
        "Phase 2, manual semantic review of every router and the services behind each object-level decision, "
        "against OWASP API Top 10 2023 and the FastAPI-specific checklist.",
        "Phase 3, source-to-sink tracing for each candidate: request input, dependency, ownership check, "
        "service, then SQL/storage sink.",
        "Phase 4, safe verification: 30 pytest cases in security-audit/tests/security/ using the project's "
        "rolled-back ignition_test harness and synthetic users only. 16 reproduce findings; 14 are positive "
        "controls that pass.",
        "Phase 5, triage: severity by likelihood and impact, grouped by root cause, false positives recorded.",
    ]
)

# ── Findings summary ───────────────────────────────────────────────────────────
pagebreak()
h(2, "7. Findings summary")
table(
    ["Severity", "Count"],
    [[s, str(sev_counts.get(s, 0))] for s in SEV_ORDER],
)
table(
    ["ID", "Severity", "Confidence", "Title", "Affected area", "Status"],
    [
        [f["id"], f["severity"], f["confidence"], f["title"], f["affected_endpoints"][0], f["status"]]
        for f in sorted(FINDINGS, key=lambda f: (SEV_ORDER.index(f["severity"]), f["id"]))
    ],
)
p("FAPI-SEC-001 to 012 are vulnerabilities. FAPI-SEC-013 to 018 are hardening recommendations or "
  "environment-dependent observations, kept separate as the specification requires.")

# ── Detailed findings ──────────────────────────────────────────────────────────
h(2, "8. Detailed findings")
FIELDS = [
    ("Severity", "severity"),
    ("Confidence", "confidence"),
    ("Status", "status"),
    ("CWE", "cwe"),
    ("OWASP API", "owasp_api"),
]
for f in FINDINGS:
    if f["id"] == "FAPI-SEC-013":
        h(3, "Hardening recommendations and environment observations")
    h(3, f"{f['id']} — {f['title']}")
    kv([(label, f[key]) for label, key in FIELDS])
    p("**Affected endpoints**")
    ul(f["affected_endpoints"])
    p("**Affected code**")
    ul(f["affected_code"])
    for label, key in [
        ("Evidence / code path", "evidence"),
        ("Preconditions", "preconditions"),
        ("Attack scenario", "attack_scenario"),
        ("Impact", "impact"),
        ("Safe reproduction / regression test", "reproduction"),
        ("Root cause", "root_cause"),
        ("Remediation", "remediation"),
    ]:
        p(f"**{label}.** {f[key]}")
    p("**Minimal patch (illustrative, not applied)**")
    code(f["patch"])
    p(f"**Verification after fix.** {f['verification_after_fix']}")

# ── OWASP matrix ───────────────────────────────────────────────────────────────
pagebreak()
h(2, "9. OWASP API Security Top 10 (2023) coverage")


def ids_for(tag: str) -> str:
    hits = [f["id"] for f in FINDINGS if tag in f["owasp_api"]]
    return ", ".join(hits) if hits else "—"


table(
    ["Category", "Status", "Findings", "Notes"],
    [
        ["API1 BOLA", "Finding(s)", ids_for("API1"), "Every id-taking route traced; portal repository and most inline checks sound; gaps are child ids and scoping on newer routers."],
        ["API2 Broken Authentication", "Finding(s)", ids_for("API2"), "JWT alg pinning, token type separation, refresh rotation and bcrypt verified; e-mail/lockout issues."],
        ["API3 Object Property Level", "Finding(s)", ids_for("API3"), "Mass assignment checked on every create/update schema (extra=forbid on register and self-update; role/status/verified_* not client-writable). Leave attachment exposure."],
        ["API4 Resource Consumption", "Finding(s)", ids_for("API4"), "Public limits clamped; staff/student lists and uploads not."],
        ["API5 Function Level Authorization", "Finding(s)", ids_for("API5"), "Build-time guard on auth markers; draft CMS reads too broad."],
        ["API6 Sensitive Business Flows", "Finding(s)", ids_for("API6"), "Payment/paywall, checklist verification, leave approval, public lead capture."],
        ["API7 SSRF", "Reviewed, no confirmed finding", "—", "Only outbound HTTP is the landing revalidation webhook to a config-fixed URL (5s timeout). No user-controlled URLs are fetched."],
        ["API8 Security Misconfiguration", "Finding(s)", ids_for("API8"), "Docs disabled in production, generic 500s, explicit CORS origins with credentials (no wildcard); fail-open payment flag and proxy config."],
        ["API9 Improper Inventory Management", "Reviewed, no confirmed finding", "—", "Legacy /messages router intentionally still mounted (documented); no debug or test routes; one API version."],
        ["API10 Unsafe Consumption of APIs", "Reviewed, no confirmed finding", "—", "Cloudinary SDK responses used only for URL and size; webhook response ignored apart from status; TLS verification on by default."],
    ],
)

# ── Tools ──────────────────────────────────────────────────────────────────────
h(2, "10. Dependency and security-tool results")
table(
    ["Tool", "Version", "Result", "Output"],
    [
        ["pytest (existing suite)", "9.1.1", "440 passed, 0 failed (exit 0)", "tool-results/pytest.txt"],
        ["ruff check .", "0.9.2", "6 style findings, none security-relevant (exit 1)", "tool-results/ruff.txt"],
        ["mypy app", "1.14.1", "3 typing errors; the text() one was triaged as a false positive", "tool-results/mypy.txt"],
        ["security regression suite", "—", "16 failed (reproductions), 14 passed (controls)", "tool-results/security-tests-pre-fix.txt"],
        ["bandit", "not available", "Not run", "Proposed: uvx bandit -r app scripts -f json -o security-audit/tool-results/bandit.json"],
        ["pip-audit", "not available", "Not run: dependency advisories NOT assessed", "Proposed: uvx pip-audit --path .venv/lib/python3.12/site-packages -f json -o security-audit/tool-results/pip-audit.json"],
        ["semgrep", "not available", "Not run", "Proposed: uvx semgrep --config p/python --config p/fastapi --json -o security-audit/tool-results/semgrep.json app"],
        ["secret scanner", "not configured", "Manual check: .env untracked and ignored; not in git history; values redacted", "—"],
    ],
)
p("Installed versions of security-relevant packages are recorded in tool-results/installed-versions.txt. They "
  "are pinned exactly in pyproject.toml, but there is no lockfile covering transitive dependencies. No advisory "
  "claims are made without pip-audit output.")

# ── Positive controls ──────────────────────────────────────────────────────────
h(2, "11. Positive security controls observed")
ul(
    [
        "A structural authorization guard: a test fails the build for any route without an auth marker and for "
        "any change to the public endpoint set.",
        "Public registration forbids role/status with extra='forbid'. The rank-based can_manage_target stops "
        "admins from creating or promoting super_admins (verified).",
        "JWT: algorithm allow-list, token type separation, per-request user status check, production secret "
        "validation. Refresh tokens are server-side, hashed, single-use (verified) and revoked on password change.",
        "Password hashing avoids bcrypt's 72-byte truncation and NUL-byte pitfalls; login timing is equalised.",
        "Uploads: extension allow-list, generated storage names (client filenames never reach storage), size "
        "limit, and private documents served only through ownership-checked, signed Cloudinary URLs; the "
        "paywall is enforced where the bytes are served.",
        "Stored-XSS defence: message HTML is sanitised on input with an allow-list and href scheme filtering "
        "(8 bypass payloads tested, all neutralised).",
        "StudentScopedRepository makes student-portal queries owner-filtered by construction; other students' "
        "ids return 404, not 403.",
        "The public API returns published data only, clamps limits, rate-limits both write endpoints, and never "
        "caches authenticated requests.",
        "Operational: generic 500 bodies with request ids, JSON logs in production, docs disabled in "
        "production, non-root container, .env excluded from git and from the Docker context, hermetic test DB "
        "with a destructive-operation name guard.",
    ]
)

# ── Roadmap ────────────────────────────────────────────────────────────────────
h(2, "12. Remediation roadmap")
table(
    ["Priority", "Findings", "Why"],
    [
        ["P0: before or at next production deploy", "FAPI-SEC-001, FAPI-SEC-010 (verify, then config)", "Revenue bypass on a public flow; configuration-only fixes."],
        ["P1: next sprint", "FAPI-SEC-002, FAPI-SEC-005, FAPI-SEC-003, FAPI-SEC-004", "Object-level authorization gaps; small, local code changes with tests already written."],
        ["P2: planned", "FAPI-SEC-007, FAPI-SEC-008, FAPI-SEC-009, FAPI-SEC-011, FAPI-SEC-012, FAPI-SEC-006", "Resource limits, identity hygiene, sensitive-file storage."],
        ["P3: hardening / policy", "FAPI-SEC-013 to FAPI-SEC-018", "Defence in depth, policy decisions, environment hygiene."],
    ],
)
p("Details, dependencies and the regression test for each item are in SECURITY_REMEDIATION_PLAN.md.")

# ── Verification ───────────────────────────────────────────────────────────────
h(2, "13. Verification and regression-test results")
p("Command: `ENVIRONMENT=test .venv/bin/pytest security-audit/tests/security -q -p no:cacheprovider --rootdir=. "
  "-c /dev/null -o asyncio_mode=auto -o asyncio_default_fixture_loop_scope=session "
  "-o asyncio_default_test_loop_scope=session`")
p("Result on the audited commit: 16 failed (each is a reproduction of the finding named in the test), "
  "14 passed (positive controls). Fixes were not applied, because the specification allows changing "
  "application code only on explicit request. Verification after fix is therefore 'Not tested' for every "
  "finding.")

# ── Residual ───────────────────────────────────────────────────────────────────
h(2, "14. Residual risks and items requiring runtime verification")
ul(
    [
        "Dependency advisories were not assessed (pip-audit unavailable). Run it before relying on this report "
        "for supply-chain assurance.",
        "FAPI-SEC-010: confirm request.client.host behind Render and Render's forwarded-header behaviour.",
        "FAPI-SEC-012: confirm Render's maximum request body size.",
        "FAPI-SEC-017: confirm whether the developer .env points at the production Neon database.",
        "Production environment variables in the Render dashboard (SIMULATED_PAYMENTS, CORS_ORIGINS, "
        "LANDING_*) were not visible to this audit.",
        "Cloudinary account settings (strict transformations, token auth, allowed delivery types) were not "
        "reviewed.",
        "The landing site's /api/revalidate and /api/preview handlers (which receive LANDING_REVALIDATE_SECRET) "
        "are out of scope.",
        "No negative authorization tests exist for most staff routes beyond the ones added here; the absence "
        "of further findings does not mean the system is secure.",
    ]
)

# ── Appendix ───────────────────────────────────────────────────────────────────
pagebreak()
h(2, "Appendix A: Reviewed files")
ul(
    [
        "app/main.py, app/api/* (auth, student, scoping, deps, exceptions, router)",
        "app/core/* (config, security, rate_limit, middleware, logging, uploads, sanitize, landing, public_cache, cache, rbac, events, subscribers)",
        "app/routes/*: all 29 router modules (every handler)",
        "app/services/*: every service reached by an object-level decision (auth, user, student_profile, document, workflow, communication, message_scope, portal_access, application, eligibility, leave, notification, apply_intent, academic)",
        "app/schemas/*: every create/update schema, for mass assignment",
        "app/models/user.py, student_portal.py, payment.py (constraints and properties)",
        "Dockerfile, docker-compose.yml, render.yaml, start_backend.sh, .gitignore, .dockerignore, .env.example, .env (keys only), pyproject.toml",
        "scripts/xlsx_reader.py, scripts/extract_xlsx.py (import parser), tests/conftest.py, tests/test_endpoint_authorization.py, tests/test_scoping.py",
    ]
)
h(2, "Appendix B: Commands run")
code(
    "git rev-parse HEAD; git ls-files; git log --all -p -S 'CLOUDINARY_API_SECRET='\n"
    "ENVIRONMENT=test .venv/bin/python <route inventory via tests.test_endpoint_authorization._iter_api_routes>\n"
    ".venv/bin/pytest -q -p no:cacheprovider\n"
    ".venv/bin/ruff check . --no-cache --output-format concise\n"
    ".venv/bin/mypy app --no-incremental\n"
    "ENVIRONMENT=test .venv/bin/pytest security-audit/tests/security -q -p no:cacheprovider ...\n"
    "python3 security-audit/build_report.py\n"
    "chromium --headless --print-to-pdf=security-audit/SECURITY_AUDIT_REPORT.pdf security-audit/SECURITY_AUDIT_REPORT.html"
)
h(2, "Appendix C: Tool and runtime versions")
table(
    ["Component", "Version"],
    [
        ["Python", "3.12.3"],
        ["fastapi / starlette / uvicorn", "0.141.1 / 1.3.1 / 0.52.0"],
        ["pydantic / SQLAlchemy / asyncpg", "2.13.4 / 2.0.51 / 0.31.0"],
        ["PyJWT / bcrypt / python-multipart", "2.13.0 / 5.0.0 / 0.0.32"],
        ["pytest / ruff / mypy", "9.1.1 / 0.9.2 / 1.14.1"],
    ],
)
h(2, "Appendix D: False positives and accepted observations")
table(["Source", "Item", "Disposition"], [[x["source"], x["item"], x["disposition"]] for x in DATA["false_positives_and_accepted_observations"]])


# ── Renderers ──────────────────────────────────────────────────────────────────
def md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown() -> str:
    out: list[str] = []
    for block in blocks:
        kind = block[0]
        if kind == "h":
            out.append("#" * block[1] + " " + block[2] + "\n")
        elif kind == "p":
            out.append(block[1] + "\n")
        elif kind == "ul":
            out.append("\n".join(f"- {item}" for item in block[1]) + "\n")
        elif kind == "kv":
            out.append("\n".join(f"- **{k}:** {v}" for k, v in block[1]) + "\n")
        elif kind == "table":
            headers, rows = block[1], block[2]
            lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
            lines += ["| " + " | ".join(md_cell(c) for c in row) + " |" for row in rows]
            out.append("\n".join(lines) + "\n")
        elif kind == "code":
            out.append("```\n" + block[1] + "\n```\n")
        elif kind == "pagebreak":
            out.append("---\n")
    return "\n".join(out)


def inline(text: str) -> str:
    """Escape, then render **bold** and `code` the same way the Markdown does."""
    escaped = html.escape(text)
    parts = escaped.split("**")
    escaped = "".join(f"<strong>{part}</strong>" if i % 2 else part for i, part in enumerate(parts))
    parts = escaped.split("`")
    return "".join(f"<code>{part}</code>" if i % 2 else part for i, part in enumerate(parts))


SEV_CLASS = {s: s.lower() for s in SEV_ORDER}
#: Short columns that must not be broken mid-word by the wrapping rule.
NOWRAP = {"ID", "Severity", "Confidence", "Count", "Method", "Version"}
#: Columns holding long paths or commands, which may break anywhere.
BREAK_ANY = {"Affected area", "Path", "Item", "Output", "Findings"}


def cell_html(text: str, badge: bool = False) -> str:
    if badge and text in SEV_CLASS:
        return f'<span class="sev {SEV_CLASS[text]}">{text}</span>'
    return inline(text)


def to_html() -> str:
    body: list[str] = []
    for block in blocks:
        kind = block[0]
        if kind == "h":
            level, text = block[1], block[2]
            anchor = text.split(" — ")[0].lower().replace(" ", "-") if text.startswith("FAPI") else ""
            body.append(f'<h{level}{f" id={anchor!r}" if anchor else ""}>{html.escape(text)}</h{level}>')
        elif kind == "p":
            body.append(f"<p>{inline(block[1])}</p>")
        elif kind == "ul":
            body.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in block[1]) + "</ul>")
        elif kind == "kv":
            body.append(
                '<dl class="kv">'
                + "".join(f"<dt>{html.escape(k)}</dt><dd>{cell_html(v, badge=k == 'Severity')}</dd>" for k, v in block[1])
                + "</dl>"
            )
        elif kind == "table":
            headers, rows = block[1], block[2]
            body.append(
                '<div class="tw"><table><thead><tr>'
                + "".join(f"<th{' class=nw' if x in NOWRAP else ''}>{html.escape(x)}</th>" for x in headers)
                + "</tr></thead><tbody>"
                + "".join(
                    "<tr>" + "".join(
                        f"<td{' class=nw' if headers[i] in NOWRAP else ' class=brk' if headers[i] in BREAK_ANY else ''}>{cell_html(c, badge=headers[i] == 'Severity')}</td>"
                        for i, c in enumerate(row)
                    ) + "</tr>"
                    for row in rows
                )
                + "</tbody></table></div>"
            )
        elif kind == "code":
            body.append(f"<pre><code>{html.escape(block[1])}</code></pre>")
        elif kind == "pagebreak":
            body.append('<div class="pb"></div>')
    return TEMPLATE.replace("{{BODY}}", "\n".join(body))


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ignition Security Audit</title>
<style>
:root{--bg:#fff;--fg:#1b1f24;--muted:#57606a;--line:#d8dee4;--code:#f6f8fa;--accent:#0b5cad;
--crit:#8b0000;--high:#c62828;--med:#b45309;--low:#1d6f42;--info:#4b5563}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0d1117;--fg:#e6edf3;--muted:#9da7b3;--line:#30363d;--code:#161b22;--accent:#58a6ff}}
:root[data-theme="dark"]{--bg:#0d1117;--fg:#e6edf3;--muted:#9da7b3;--line:#30363d;--code:#161b22;--accent:#58a6ff}
body{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0 auto;max-width:980px;padding:24px 16px}
h1{font-size:28px;margin:8px 0 16px}h2{font-size:21px;border-bottom:1px solid var(--line);padding-bottom:4px;margin-top:36px}
h3{font-size:17px;margin-top:28px;color:var(--accent)}p,li{overflow-wrap:anywhere}
code{background:var(--code);padding:1px 4px;border-radius:4px;font-size:.9em}
pre{background:var(--code);border:1px solid var(--line);border-radius:6px;padding:10px;overflow-x:auto;white-space:pre-wrap;word-break:break-word}
pre code{padding:0;background:none}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13.5px}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top;}td.brk{overflow-wrap:anywhere}td.nw,th.nw{white-space:nowrap}th{background:var(--code)}
dl.kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:8px 0}dl.kv dt{font-weight:600;color:var(--muted)}dl.kv dd{margin:0}
.sev{font-weight:700;padding:1px 6px;border-radius:4px;color:#fff;font-size:12px;white-space:nowrap}
.sev.critical{background:var(--crit)}.sev.high{background:var(--high)}.sev.medium{background:var(--med)}.sev.low{background:var(--low)}.sev.informational{background:var(--info)}
.pb{break-after:page}
@media print{body{max-width:none;font-size:11.5px}.tw{overflow:visible}h3{break-after:avoid}pre,table{break-inside:auto}tr{break-inside:avoid}}
</style></head><body>
{{BODY}}
</body></html>
"""

(HERE / "SECURITY_AUDIT_REPORT.md").write_text(to_markdown())
(HERE / "SECURITY_AUDIT_REPORT.html").write_text(to_html())
print("wrote SECURITY_AUDIT_REPORT.md and SECURITY_AUDIT_REPORT.html")
