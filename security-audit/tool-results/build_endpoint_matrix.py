import csv, inspect, re, sys
sys.path.insert(0, '.')
from tests.test_endpoint_authorization import _iter_api_routes, _auth_levels
from app.main import app

# Object-level control applied in each router module (from manual review).
MODULE_CONTROL = {
 'auth.py': 'Self only (token subject)',
 'users.py': 'can_manage_target rank check; counsellor limited to STUDENT targets; /me = self',
 'student_profile.py': 'Owner or ADMIN/SUPER_ADMIN/COUNSELLOR (no counsellor assignment scoping)',
 'employee_profile.py': 'Owner or ADMIN/SUPER_ADMIN/MANAGER',
 'academic.py': 'Reference data; writes admin/marketing',
 'catalogue.py': 'Reference data; reads any authenticated user incl. unpublished; writes admin/marketing',
 'content.py': 'CMS; reads any authenticated user incl. drafts; writes admin/marketing',
 'eligibility.py': 'Staff queue (admin/super_admin/counsellor)',
 'public.py': 'Unauthenticated; published-only; limit clamped; POSTs rate-limited per client IP',
 'imports.py': 'Admin; disabled in production',
 'departments.py': 'Staff read; admin/manager write',
 'employees.py': 'Admin/manager',
 'application.py': 'Student: own; counsellor: own-work scope (may_see_record); others: all',
 'document.py': 'Student: own (student_id); staff: any; paywall on offer/CAS',
 'appointment.py': 'Student: own; staff: any',
 'payment.py': 'Student: own; finance/admin: any',
 'task.py': 'Staff only',
 'priority_tasks.py': 'Staff only (admin/counsellor/admissions)',
 'notification.py': 'Owner or ADMIN',
 'message.py': 'Legacy staff inbox; message_scope own-work rule',
 'communication.py': 'Student: own SHARED threads; staff: ALL threads (no own-work scope)',
 'workflow.py': 'Student: own application; staff roles: any application; child step ids NOT bound to application',
 'leads.py': 'Counsellor own-work scope (may_see_record); others all',
 'attendance.py': 'Self or MANAGE_ROLES',
 'leave.py': 'Self or MANAGE_ROLES',
 'payroll.py': 'Self (payslips) or VIEW_ROLES',
 'activity_log.py': 'Admin/super_admin',
 'health.py': 'Unauthenticated liveness/readiness',
 'student.py': 'StudentScopedRepository / service calls keyed on authenticated student id',
}

FINDINGS = [
 (r'^POST /api/v1/student/me/access/checkout$', 'FAPI-SEC-001'),
 (r'^PATCH /api/v1/applications/\{application_id\}/checklist/\{item_id\}$', 'FAPI-SEC-002'),
 (r'/workflow/steps/\{step_id\}', 'FAPI-SEC-003'),
 (r'^POST /api/v1/documents/upload$', 'FAPI-SEC-004;FAPI-SEC-012'),
 (r'^POST /api/v1/student/me/threads$', 'FAPI-SEC-004'),
 (r'^POST /api/v1/student/me/applications$', 'FAPI-SEC-004'),
 (r'^(GET|POST|PATCH) /api/v1/communication/', 'FAPI-SEC-005'),
 (r'^(GET|POST|PATCH) /api/v1/applications/\{application_id\}/(workflow|checklist)', 'FAPI-SEC-005'),
 (r'^POST /api/v1/leave-requests$', 'FAPI-SEC-006;FAPI-SEC-012'),
 (r'^POST /api/v1/leave-requests/\{request_id\}/approve$', 'FAPI-SEC-015'),
 (r'^GET /api/v1/(content-pages|content-blocks|media-assets|blog-posts|country-guides|university-routes|course-profiles|scholarships)', 'FAPI-SEC-008'),
 (r'^PATCH /api/v1/users/me$', 'FAPI-SEC-009'),
 (r'^POST /api/v1/auth/register$', 'FAPI-SEC-009;FAPI-SEC-010;FAPI-SEC-011'),
 (r'^POST /api/v1/auth/login$', 'FAPI-SEC-010;FAPI-SEC-011'),
 (r'^POST /api/v1/auth/(refresh|change-password)$', 'FAPI-SEC-010'),
 (r'^POST /api/v1/auth/(logout|change-password)$', 'FAPI-SEC-016'),
 (r'^POST /api/v1/public/(eligibility|apply-intents)$', 'FAPI-SEC-010'),
 (r'^POST /api/v1/public/eligibility$', 'FAPI-SEC-018'),
 (r'/(link|download)$', 'FAPI-SEC-013'),
 (r'^POST /api/v1/(users/me/avatar|users/\{user_id\}/avatar|communication/threads/\{thread_id\}/messages|student/me/documents/\{document_id\}/replace|imports/catalogue|media-assets/upload)', 'FAPI-SEC-012'),
]

rows = []
for r in _iter_api_routes(app.routes):
    if r.path in {"/openapi.json","/docs","/docs/oauth2-redirect","/redoc"}: continue
    ep = inspect.unwrap(r.endpoint)
    src = inspect.getsourcefile(ep).split('backend/')[-1]; line = inspect.getsourcelines(ep)[1]
    d = r.dependant
    levels = sorted(_auth_levels(d))
    authn = 'None (public)' if not levels or set(levels) <= {'public'} else 'Bearer JWT (access)'
    authz = ';'.join(l for l in levels if l != 'authenticated') or ('any authenticated user' if levels else 'none')
    idparams = ';'.join(p.name for p in d.path_params)
    query = ';'.join(p.name for p in d.query_params)
    body = ';'.join(f"{p.name}:{getattr(p.field_info.annotation,'__name__',str(p.field_info.annotation))}" for p in d.body_params)
    rm = getattr(r,'response_model',None); rm = getattr(rm,'__name__',str(rm)) if rm else ''
    control = MODULE_CONTROL.get(src.split('/')[-1], '')
    for m in sorted(r.methods or []):
        if m in {"HEAD","OPTIONS"}: continue
        key = f"{m} {r.path}"
        fids = sorted({f for pat, fs in FINDINGS if re.search(pat, key) for f in fs.split(';')})
        rows.append({'method':m,'path':r.path,'handler':f"{src}:{line}:{ep.__name__}",'authentication':authn,
            'authorization_dependency':authz,'object_ids':idparams,'query_params':query,'request_body':body,
            'object_level_control':control,'response_model':rm,'review_status':'Reviewed','findings':';'.join(fids)})
rows.sort(key=lambda x:(x['path'],x['method']))
with open('security-audit/endpoint-security-matrix.csv','w',newline='') as f:
    w=csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(len(rows), 'routes;', sum(1 for r in rows if r['findings']), 'with findings;', sum(1 for r in rows if r['authentication'].startswith('None')), 'public')
