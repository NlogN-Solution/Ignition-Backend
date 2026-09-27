# Security Remediation Plan — Ignition backend

Audited commit `1e58579` on `main`, 2026-09-25. Finding details are in `SECURITY_AUDIT_REPORT.md` and `findings.json`.
Each item names the regression test in `security-audit/tests/security/test_audit_findings.py` that currently
fails and must pass once the fix lands. After fixing, move the test into `tests/` so the main suite keeps it.

Run the security suite:

```
ENVIRONMENT=test .venv/bin/pytest security-audit/tests/security -q -p no:cacheprovider --rootdir=. -c /dev/null \
  -o asyncio_mode=auto -o asyncio_default_fixture_loop_scope=session -o asyncio_default_test_loop_scope=session
```

## P0: before or at the next production deploy

| # | Finding | Change | Depends on | Regression test |
|---|---|---|---|---|
| 1 | FAPI-SEC-001 Simulated checkout on in production | `config.py` `_validate_secrets`: raise if `ENVIRONMENT=production and SIMULATED_PAYMENTS`. Add `SIMULATED_PAYMENTS: "false"` to `render.yaml`. Check the live Render env var today, and review existing `SIMULATED-*` PORTAL_ACCESS payments in production. | none | `test_fapi_sec_001_*` (both) |
| 2 | FAPI-SEC-010 Rate limits keyed on proxy IP | First verify: log `request.client.host` and `X-Forwarded-For` on Render. If they differ, start uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy CIDR>` (via a `FORWARDED_ALLOW_IPS` env var) and add a per-account login limit. | Render networking facts | Manual: two source IPs, 11 logins from one, and the other must not be throttled |

## P1: next sprint (object-level authorization)

| # | Finding | Change | Depends on | Regression test |
|---|---|---|---|---|
| 3 | FAPI-SEC-005 Counsellor scoping on `/communication` and workflow/checklist | (a) `_assert_application_access`: for staff, `may_see_record(user, application.counsellor_id)`, else 404. (b) Thread visibility rule for non-admin staff, derived from `message_scope` (own student / own lead / own application / replied / unclaimed), applied in `staff_inbox`, `threads_for_student/lead/application`, `get_thread`, `post_message` and `_attachment_for`. Keep `include_internal` for staff but only inside that scope. | Product confirmation that the rule matches `/messages` | `test_fapi_sec_005_*` (+ control stays green) |
| 4 | FAPI-SEC-002 Checklist accepts any document | Checklist PATCH: document must exist, `document.student_id == application.student_id`, and its type must match `item.document_type`. Auto-VERIFY only when the approval is for this item's type. Defence in depth: filter `list_my_application_documents` on `Document.student_id == application.student_id`. | #3 changes the same helper, so land together | `test_fapi_sec_002_*` (both) |
| 5 | FAPI-SEC-003 Step id not bound to application | `get_step_for_application(step_id, application_id)` joining `ApplicationWorkflow`; use it in the PATCH step, GET activities and POST comment routes. | none | `test_fapi_sec_003_*` |
| 6 | FAPI-SEC-004 Foreign `application_id` / `intake_id` | Document upload: application must belong to `student_id` (staff uploads too). Student thread: `repo.get_or_404(Application, id)`. Student application create: intake must belong to program. Map FK IntegrityErrors to 404/409. | none | `test_fapi_sec_004_*` (both) |

## P2: planned

| # | Finding | Change | Depends on | Regression test |
|---|---|---|---|---|
| 7 | FAPI-SEC-007 Unbounded pagination | Move `PageParam`/`LimitParam` from `routes/public.py` into a shared module and use them on all ~45 list routes. Clamp inside `StudentScopedRepository.list` too. | none | `test_fapi_sec_007_*` (both) |
| 8 | FAPI-SEC-008 Drafts readable by students | Replace `Depends(get_current_user)` with `require_staff` (or the admin/marketing set) on CMS/catalogue reads. Check the admin console still loads. | Frontend check | `test_fapi_sec_008_*` |
| 9 | FAPI-SEC-012 Uploads buffered in memory | Chunked read with an early abort, a Content-Length pre-check middleware, and a per-message total cap. Verify Render's body limit. | none | New: 26 MB streamed upload returns 400 without an OOM (run locally only) |
| 10 | FAPI-SEC-009 E-mail handling | Lower-case e-mails on input; migration adding a unique index on `lower(email)` (dedupe first); map IntegrityError to 409; require `current_password` for e-mail change; plan e-mail verification. | Data cleanup of case-duplicates | `test_fapi_sec_009_*` (both) |
| 11 | FAPI-SEC-011 Lockout enumeration and targeted lockout | Same response for locked, bad-password and unknown accounts; notify the owner; move to progressive delay keyed on account + IP. | #2 (a correct IP is needed for keying) | `test_fapi_sec_011_*` |
| 12 | FAPI-SEC-006 Public leave attachments | `private=True`; store `stored_file_name`; add an owner-or-manager download route; migrate existing public assets (Cloudinary rename to the `authenticated` type) and update rows. | Admin frontend link change | `test_fapi_sec_006_*` |

## P3: hardening and policy

| # | Item | Change |
|---|---|---|
| 13 | FAPI-SEC-013 Non-expiring signed URLs | Enable Cloudinary token auth (`auth_token`, a few minutes), or stream bytes through the API with `Cache-Control: private, no-store`. |
| 14 | FAPI-SEC-014 Counsellors see all students' PII | Decide the policy. If own-work applies to people, scope student-profile and document staff branches the same way. |
| 15 | FAPI-SEC-015 Leave self-approval | Refuse review of your own request (except super_admin); use an atomic `UPDATE … WHERE status='pending'`. |
| 16 | FAPI-SEC-016 Token lifetime and refresh race | Atomic refresh rotation (`UPDATE … WHERE revoked_at IS NULL RETURNING`), reuse detection that revokes the token family, and optionally a `sid` claim. |
| 17 | FAPI-SEC-017 Dev `.env` with hosted credentials | Confirm whether it is the production database; if so, give development its own Neon branch and Cloudinary environment and rotate the keys. |
| 18 | FAPI-SEC-018 Eligibility attaches to existing leads | Store anonymous submissions as unverified and let staff merge them. |
| 19 | Tooling | Add `bandit`, `pip-audit` and a lockfile (`uv lock` / `pip-compile`) to CI; run pip-audit now, since dependency advisories were not assessed in this audit. |
| 20 | Tests | Extend `tests/test_scoping.py`-style negative tests to every id-taking staff route; add a CI check that every list route uses `LimitParam`. |

## Suggested sequencing

1. P0 #1 (a config change plus one validator line) and the P0 #2 investigation, the same day.
2. P1 #3 and #4 in one PR (both touch `routes/workflow.py`), then #5 and #6.
3. P2 in any order, except that #11 waits for #2.
4. Re-run the full suite, the security suite, ruff, mypy, bandit and pip-audit, and update
   `Verification after fix` in `findings.json`, then regenerate the report with
   `python3 security-audit/build_report.py`.
