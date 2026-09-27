# Security Remediation Report — Ignition backend

| | |
|---|---|
| Audit remediated | `SECURITY_AUDIT_REPORT.md` (commit `1e58579`, 2026-09-25), 18 findings |
| Remediation date | 2026-09-26 |
| Branch / state | `main`, uncommitted working tree (61 files changed, 8 files added) |
| Result | All 18 findings addressed. 16/16 audit reproductions now pass, 14/14 positive controls still pass |
| Test suite | **515 passed, 0 failed** (was 440). Includes the 30 audit tests moved into `tests/` and 45 new regression tests |
| Dependency scan | `pip-audit`: 65 installed packages, **0 known vulnerabilities** |
| Static analysis | `bandit`: 0 high; 4 medium (admin-only XML import, disabled in production); 14 low (false positives / type-narrowing asserts) |
| Lint / types | ruff: 2 pre-existing style nits, 0 new. mypy: 1 error, the audit's triaged false positive (was 3) |

---

## 1. Summary

Every vulnerability in the audit is fixed in code and covered by a regression test. The informational items are either fixed (013, 014, 015, 016, 018) or mitigated with a clear owner action (017). While fixing them I found and fixed **six more issues** the audit did not list. The most serious was that `must_change_password` was enforced only by the admin console. Combined with `scripts/SEED_NEON.md` seeding the hosted database with a password published in this repository, anyone calling the API directly could use a seeded admin account.

A few fixes change API behaviour that the frontends rely on. They are listed in section 5. Read that section before you deploy.

## 2. Verification performed

| Check | Before | After |
|---|---|---|
| Audit security suite (`security-audit/tests/security`) | 16 failed, 14 passed | **30 passed** |
| Main suite (`pytest`) | 440 passed | **515 passed**, 0 failed |
| Migration `b4e1d9c2a7f3` on Postgres 16 (scratch DB) | — | upgrade ✔, downgrade ✔, re-upgrade ✔, index rejects `Case@X.com` vs `case@x.com` ✔, refuses to run when case-duplicates exist ✔ |
| Live server smoke test (real uvicorn + Redis, scratch DB) | — | case-insensitive register (409) and login ✔; `limit=1000000` and `page=0` → 422 ✔; checkout → 400 ✔; student reading CMS → 403 ✔; 1 GB `Content-Length` → 413 **with CORS headers** ✔; token dead immediately after logout ✔; 5 bad logins then generic 400, 11th request → 429 ✔; Redis keys hashed with a 900 s TTL ✔; no `server` banner ✔ |
| pip-audit | not run | 0 vulnerabilities (`tool-results/pip-audit.json`) |
| bandit | not run | triaged below (`tool-results/bandit.json`) |

All test and smoke databases were local and disposable. Neither the Neon database in `.env` nor any production system was contacted.

## 3. Finding-by-finding

### FAPI-SEC-001 (Medium): simulated checkout on in production — FIXED
- `config.py`: `SIMULATED_PAYMENTS` now **defaults to `False`**, and the settings validator **refuses to boot** in production when it is true.
- `routes/student.py`: checkout also refuses whenever `ENVIRONMENT=production`, whatever the flag says, so one mistyped env var cannot reopen the paywall.
- `render.yaml`: `SIMULATED_PAYMENTS: "false"` is now explicit.
- Tests: `test_fapi_sec_001_*`, `test_simulated_payments_are_off_by_default`, `test_production_refuses_to_boot_with_simulated_payments`. The paywall test module opts into the demo checkout explicitly (`simulated_payments` fixture).
- **Owner action:** in production, look for `payments` rows with `transaction_reference LIKE 'SIMULATED-%'` and decide what to do about access already granted.

### FAPI-SEC-002 (Medium): checklist accepts any document — FIXED
- `routes/workflow.py`: a `document_id` must belong to **the application's student** (404 otherwise) and match the item's `document_type` (400 otherwise). This applies to **students and staff**.
- `services/workflow_service.py`: an approved document auto-verifies an item only when its type matches the item's type. For a custom item with no type, only staff linking counts as verification.
- `routes/student.py`: the student's application-documents list is also filtered on `Document.student_id` (defence in depth).
- Tests: both audit tests, plus a staff cross-student link test and a test that the legitimate "approved passport → passport item → verified" path still works.

### FAPI-SEC-003 (Low): step id not bound to application — FIXED
- New `ApplicationWorkflowService.get_step_for_application()` joins on `application_id`. PATCH step, GET activities and POST comment all use it and return 404 on a mismatch.
- Tests: the audit test plus write-path tests (PATCH and comment).

### FAPI-SEC-004 (Low): foreign `application_id` / `intake_id` — FIXED
- Document upload: the application must belong to `student_id` (staff too), checked **before** the file is stored. A staff upload's target must be a student, or the uploader themselves.
- Student thread: the application is resolved through `StudentScopedRepository`.
- Student application create: the intake must belong to the course.
- Staff application create/update (`ApplicationService.validate_references`): the student must be a student, the counsellor must be active staff, the program must exist, and the intake must belong to the program.
- Staff thread create: the student, lead and application ids must exist and agree with each other.
- `POST /documents` (staff): refuses a `stored_file_name` that already belongs to another student's document. That closes a staff-assisted route to reading another student's file. `uploaded_by`/`verified_by` are now stamped with the caller.
- `main.py`: a global `IntegrityError` handler returns **409** with a neutral message instead of a 500.

### FAPI-SEC-005 (Medium): counsellor scoping on /communication and workflow — FIXED
- `_assert_application_access` applies `may_see_record`, so a colleague's application returns 404, the same as `GET /applications/{id}`.
- New `message_scope.visible_threads_condition()` works at thread level from every link a thread carries (student, lead and the account it converted into, application, author). It is applied to the inbox, the student, lead and application thread lists, the by-id thread, replies and attachments. Admins and super-admins see everything; everyone else sees own work plus unclaimed threads. That is the same policy as the legacy `/messages`.
- Staff may only **open** threads about their own or unclaimed students, leads and applications (`may_open_thread_about`).
- `/workflow-steps` (the dashboard bottleneck widget) is scoped too.
- The NULL-safe SQL (EXISTS rather than bare `IN`) is deliberate: a lead-only thread has `student_id IS NULL`, and `NOT (NULL IN …)` would otherwise hide every unclaimed lead thread.

### FAPI-SEC-006 (Low): public leave attachments — FIXED
- Uploads are `private=True` and stored in the new `leave_requests.attachment_stored_file_name` column (migration `b4e1d9c2a7f3`).
- `attachment_url` is **never returned**. It is always `null`, and a new `has_attachment` flag replaces it.
- New routes `GET /leave-requests/{id}/attachment` and `/attachment/link` serve the file to the requester or a manager (others get 403).
- `store_upload(private=…)` no longer has a default, so no future caller can fall into public storage by accident.
- **Owner action:** run `python -m scripts.migrate_leave_attachments_private` (a dry run) and then `--apply` against production. It renames the old public Cloudinary assets to `authenticated` and updates the rows.

### FAPI-SEC-007 (Low): unbounded pagination — FIXED
- New `app/api/pagination.py`: `page` 1..10,000 and `limit` 1..**200**. The ceiling is 200, not 100, because the admin dashboard and student portal already request `limit=200`; 100 would have broken them.
- Applied to every list route. A build-time test (`test_every_paginated_route_bounds_page_and_limit`) fails if a route declares an unbounded `page`/`limit`. That test found 6 more routes (`/public/*` pages and the communication inbox) with no upper bound on `page`, and those are fixed too.
- `StudentScopedRepository.list` clamps as well. `year`/`month` on the attendance and leave-balance routes are bounded, because `date(0, 13, 1)` used to cause a 500.

### FAPI-SEC-008 (Low): drafts readable by students — FIXED
- All 16 CMS and catalogue read routes now use `require_staff`. Workflow-template reads are staff-only too.
- **Also found:** `/universities`, `/programs` and `/intakes` (which students may read by design) returned **unpublished** rows. Students now get published-only rows from these, and a 404 for a draft by id.

### FAPI-SEC-009 (Low): e-mail handling — FIXED
- `schemas/email.py`: every inbound address is trimmed and lower-cased. Lookups compare `lower(email)`, which covers legacy rows.
- Unique index `uq_users_email_lower` is declared on the model and created by the migration. The migration lower-cases existing rows and **stops with instructions** if two accounts already differ only by case. It does not merge them for you.
- `PATCH /users/me`: changing the e-mail requires `current_password`. Re-sending the unchanged address (which the console's profile form does) does not.
- Duplicates return 409 instead of 500.
- **Not done:** e-mail verification at signup. There is no mail infrastructure yet; this is recorded as a requirement in the SRS.

### FAPI-SEC-010 (Medium): rate limits keyed on the proxy — FIXED IN CODE, CONFIRM AFTER DEPLOY
- New `core/client_ip.py` counts `X-Forwarded-For` **from the right** by `TRUSTED_PROXY_HOPS` (0 locally, 1 in `render.yaml`). It falls back to the socket peer on garbage, because the value lands in `INET` columns.
- **The audit's suggested patch was not used.** uvicorn's `--forwarded-allow-ips='*'` trusts the *leftmost* entry, which the client controls, so every per-IP limit would have become trivially bypassable. I verified this in the uvicorn 0.52 source.
- The resolver is used by slowapi and by every audit-log IP.
- **Owner action:** after deploying, sign in as the super-admin and open `GET /api/v1/health/client-ip`. `resolved` must be your own public IP. If it shows a Render or Cloudflare address, set `TRUSTED_PROXY_HOPS=2`. Never set it higher than the real number of proxies.

### FAPI-SEC-011 (Low): lockout DoS and enumeration — FIXED
- Unknown account, wrong password, throttled and locked all return the same **400 "Invalid email or password"** after a bcrypt round.
- Tier 1 counts failures per **account + IP** (`core/login_throttle.py`, in Redis, 5 per 15 min, hashed key names). An attacker only locks *themselves* out.
- Tier 2 locks the whole account after 20 failures from several addresses and sends the owner an **in-app notification**.
- If Redis is unavailable the throttle fails open, and the tier-2 database lock still applies.

### FAPI-SEC-012 (Low): uploads buffered in memory — FIXED
- `BodySizeLimitMiddleware` rejects a declared `Content-Length` above `MAX_REQUEST_BODY_MB` (60) before reading anything. It also cuts off chunked or lying bodies mid-stream with a **413**.
- It sits inside the `BaseHTTPMiddleware` layers on purpose: outside them, the 413 was wrapped in an `ExceptionGroup` and reported as a 400.
- `store_upload` reads 1 MB chunks and aborts at the per-file limit.

### FAPI-SEC-013 (Info): non-expiring signed URLs — FIXED (opt-in)
- Set `CLOUDINARY_AUTH_TOKEN_KEY` and every private-file URL carries an expiring `__cld_token__` (TTL `CLOUDINARY_URL_TTL_SECONDS`, default 300).
- I checked the SDK source: it mints the token only when `sign_url=True` is passed *with* `auth_token`. Otherwise it silently returns an unsigned URL.
- All API responses now default to `Cache-Control: no-store`, including the 307 redirects to signed URLs.
- **Owner action:** enable token-based authentication in the Cloudinary console (a paid feature) and set the key.

### FAPI-SEC-014 (Info): counsellors see all students' PII — FIXED (policy applied)
- New `student_visibility_condition` / `may_see_student` in `api/scoping.py` apply the same own-or-unclaimed rule, for counsellors, to:
  - student profile, education, experience, research and shortlist;
  - documents (list, folders, by id, link and download, verify, reject, comment, upload, create);
  - users (list, get, patch, avatar, enable-portal).
- A colleague's student returns 404.
- **This is a policy decision.** I applied the rule the codebase already documents for leads and applications. If counsellors must see every student, remove the calls to `student_visibility_condition` and `assert_may_see_student`.

### FAPI-SEC-015 (Info): self-approval of leave — FIXED
- Reviewing your own leave request returns 403 (super-admin excepted).
- Approve, reject and cancel are single conditional `UPDATE … WHERE status IN (…) RETURNING`. A double decision gets a 409 instead of the last write winning.

### FAPI-SEC-016 (Info): tokens survive logout; refresh race — FIXED
- Access tokens now carry `sid`. `get_current_user` requires a live `user_sessions` row, so logout, password change, admin reset and reuse detection take effect **on the next request**.
- Refresh rotation is one atomic `UPDATE … WHERE revoked_at IS NULL RETURNING`.
- A rotated token reused **outside a 10 s grace window** revokes every session. Inside the window it is treated as two tabs racing and simply returns 401.
- Password change keeps the caller's own session (the route's documented "revokes every other session"). Logout by refresh token only affects the caller's own sessions.
- JWT `exp` and `sub` are now *required* claims.

### FAPI-SEC-017 (Info): dev .env holds hosted credentials — MITIGATED, OWNER ACTION REQUIRED
- **Found while fixing:** the `Settings` repr printed `DATABASE_URL` **with its password**, plus the JWT and Cloudinary secrets, into any traceback, debug log or pytest failure. It happened during this session: a failing assertion printed your Neon connection string, password included, into the test output. Every credential field is now `repr=False`, and a test pins it.
- Startup logs a warning when `ENVIRONMENT=development` points at a non-local database.
- **Owner action (do this now):** rotate the Neon database password. If that database is production, also give development its own Neon branch and Cloudinary environment and rotate the Cloudinary keys.

### FAPI-SEC-018 (Info): anonymous eligibility attaches to leads — FIXED
- An anonymous submission matched to an existing lead no longer modifies the lead, which also removes a 500 on e-mail collision. It is logged on the lead's timeline as **"(unverified)"**, with a warning to confirm with the student.
- A full staff "merge" UI is left for the product backlog.

## 4. Additional issues found and fixed (not in the audit)

| # | Issue | Fix |
|---|---|---|
| A1 | **`must_change_password` enforced only by the admin frontend.** Temporary passwords, and the published seed password `SEED_NEON.md` puts into the hosted database, gave full API access to direct callers | Staff accounts with the flag set can only call `/auth/me`, `/auth/change-password` and `/auth/logout` until they change it. Test added. **Owner action:** in production, delete or re-password every `@ignition.example.com` seed account, including the two seed *students*, which are not flagged |
| A2 | Settings repr leaked every secret | `repr=False` on all credential fields (see 017) |
| A3 | Students could list and read **unpublished** universities, programs and intakes | Published-only for students (see 008) |
| A4 | Lead conversion matched a lead's e-mail to **any** user, including staff, so a public form submission using a staff e-mail could file that staff member as a student. An explicit `converted_user_id` accepted any id | Only student accounts; otherwise 400 |
| A5 | `/auth/change-password` (and the new e-mail-change prompt) were password-guessing oracles for anyone holding a stolen access token | Per-account re-authentication throttle, 5 attempts per 15 min |
| A6 | Docker build context shipped `tests/`, `security-audit/` and the audit PDF into the production image; uvicorn advertised its version | `.dockerignore` extended; `--no-server-header` |
| A7 | No security headers; client-supplied `X-Request-ID` echoed without limits | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `COOP`, HSTS in production, `no-store` default; request ids limited to 128 safe characters |
| A8 | Workflow step `assigned_to` accepted any UUID (a 500 on a bad FK, or assignment to a student) | Must be active staff |

## 5. Behaviour changes that affect the frontends

Check these in the admin console and student portal before you deploy:

1. **Changing e-mail in the admin profile form** now needs a `current_password` field in the `PATCH /users/me` body. Without it, the API returns 400 "Enter your current password to change your email address". Saving the form with an unchanged address still works.
2. **Leave attachments:** `attachment_url` is always `null`. Use `has_attachment` and open `GET /leave-requests/{id}/attachment/link`, which returns `{url, file_name, mime_type}`. The console does not currently render the attachment, so nothing breaks; the link just isn't shown yet.
3. **Page sizes above 200** now return 422. Every current frontend call uses 200 or less.
4. **Counsellors** now see only their own and unclaimed students' threads, profiles, documents, users and workflow steps. Admins and managers are unaffected, except that managers and admissions are also scoped in `/communication`, matching legacy `/messages`.
5. **Students** get 403 on `/content-*`, `/media-assets`, `/blog-posts`, `/country-guides`, `/university-routes`, `/course-profiles`, `/scholarships` and `/workflow-templates`. The student portal does not call any of them; it uses `/student/catalog/*`.
6. **Logout, password change and admin reset end access tokens immediately.** The frontends already refresh on 401. Access tokens issued before the deploy carry no `sid`, so every signed-in user gets one refresh or re-login.
7. **Lockout message:** a locked account now sees "Invalid email or password". The owner is told through an in-app notification instead.
8. **Staff with temporary passwords** can only change their password. The admin console already routes them to `/change-password`.
9. **The demo checkout** is off unless `SIMULATED_PAYMENTS=true` is in `.env`. Add it locally if you demo the unlock flow.

## 6. Deployment checklist

1. [ ] **Rotate the Neon password now.** It appeared in a local test traceback during this session. Also rotate the Cloudinary keys if `.env` points at production.
2. [ ] Delete or re-password every seed account in production (`*@ignition.example.com`).
3. [ ] Before migrating, run the duplicate query in `migrations/versions/b4e1d9c2a7f3_security_hardening.py` against production and resolve any case-duplicate e-mails.
4. [ ] Deploy. `preDeployCommand: alembic upgrade head` applies `b4e1d9c2a7f3`.
5. [ ] In Render, confirm `SIMULATED_PAYMENTS=false`, `TRUSTED_PROXY_HOPS=1` and `MAX_REQUEST_BODY_MB=60` (the Blueprint sets them; check that a stale dashboard value does not override them).
6. [ ] As super-admin, open `/api/v1/health/client-ip` and confirm `resolved` is your IP. Adjust `TRUSTED_PROXY_HOPS` if it isn't.
7. [ ] Run `python -m scripts.migrate_leave_attachments_private`, review the output, then run it again with `--apply`.
8. [ ] Review `SIMULATED-*` portal-access payments already in production.
9. [ ] Optional: enable Cloudinary token auth and set `CLOUDINARY_AUTH_TOKEN_KEY`.
10. [ ] Frontend: add the current-password field to the e-mail change form, and a "View attachment" link on leave requests.
11. [ ] Replace the placeholder `CORS_ORIGINS` in `render.yaml` with the real frontend origins.

## 7. Residual risk and backlog

- **E-mail verification** and **password-reset-by-email** do not exist yet (no mail provider). They are required before the account e-mail can safely be used for recovery.
- **Payment gateway:** none is integrated. The access fee cannot be taken online until one is. Checkout refuses in production, which is the safe state.
- **Scanned-file content:** uploads are checked by extension and size, not by content or with antivirus. Files a student uploads are opened by staff, so consider Cloudinary's moderation or a ClamAV step.
- **Dependency lockfile:** direct dependencies are pinned, but transitive ones are not. Add `uv lock` or `pip-compile`, and run `pip-audit` and `bandit` in CI (there is no CI configuration in this repository today).
- **`scripts/xlsx_reader.py`** parses workbook XML with `xml.etree` (bandit B314). It is admin-only and hard-disabled in production. Python 3.12's expat does not resolve external entities, but switch to `defusedxml` and cap the decompressed size if the import is ever enabled in production.
- **Access-token lifetime** stays 15 minutes. Revocation is now immediate, so this only bounds stolen-token use when the session isn't revoked.
- The absence of further findings is not proof of security. Keep adding negative authorization tests for each new id-taking route, in the style of `tests/test_security_hardening.py`.

## 8. Files

**New:** `app/api/pagination.py`, `app/core/client_ip.py`, `app/core/login_throttle.py`, `app/schemas/email.py`, `migrations/versions/b4e1d9c2a7f3_security_hardening.py`, `scripts/migrate_leave_attachments_private.py`, `tests/test_security_audit_findings.py`, `tests/test_security_hardening.py`, this report.

**Materially changed:** `app/core/{config,middleware,security,uploads,rate_limit}.py`, `app/main.py`, `app/api/{auth,scoping,student}.py`, `app/services/{auth,user,workflow,communication,message_scope,document,application,academic,leave,lead,eligibility}_service.py`, and `app/routes/{auth,users,workflow,communication,document,student,student_profile,leave,academic,content,catalogue,application,leads,health}.py`. Every other list router changed only for pagination. Also changed: `Dockerfile`, `render.yaml`, `.dockerignore`, `.env.example`, `tests/{conftest,test_auth,test_catalogue,test_milestones_and_paywall}.py`. `ruff --fix` also cleared two pre-existing lint nits in `portal_access_service.py` and `student_profile_service.py`.

`security-audit/findings.json` records "Verification after fix" for every finding.
