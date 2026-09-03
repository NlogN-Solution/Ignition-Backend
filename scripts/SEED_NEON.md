# Seeding the hosted database from the Neon SQL Editor

`scripts/seed.py` needs a shell on a box that can reach the database, and it
[refuses to run against a production environment](seed.py) anyway. On Render you
have neither, so this document is the same seed expressed as SQL you can paste
into the **Neon console → your project → SQL Editor**.

It produces exactly what `python -m scripts.seed` produces: the same rows, the
same natural keys, the same login password. It is **idempotent** — every
statement is guarded by `ON CONFLICT DO NOTHING` or `WHERE NOT EXISTS`, so
running it twice adds nothing and errors on nothing.

> **Before you start:** these are development credentials on a publicly reachable
> deployment. Seed them, sign in, and change the passwords (see
> [After seeding](#7-after-seeding)). Staff rows are created with
> `must_change_password = true` so the app forces this on first login; the two
> student rows are not, so change those yourself.

---

## 1. Check the schema is actually there

Seeding writes rows; it does not create tables. If the Render service has never
run `alembic upgrade head`, stop here and run migrations first — otherwise every
statement below fails with `relation ... does not exist`.

```sql
SELECT version_num FROM alembic_version;
```

You should get one row. If the table itself is missing, the database has no
schema yet.

## 2. Check what is already seeded

Run this first so you know what you are looking at, and again at the end to
confirm the result.

```sql
SELECT 'users'                    AS table, count(*) FROM users
UNION ALL SELECT 'countries',                count(*) FROM countries
UNION ALL SELECT 'universities',             count(*) FROM universities
UNION ALL SELECT 'programs',                 count(*) FROM programs
UNION ALL SELECT 'departments',              count(*) FROM departments
UNION ALL SELECT 'leave_types',              count(*) FROM leave_types
UNION ALL SELECT 'attendance_policies',      count(*) FROM attendance_policies
UNION ALL SELECT 'progress_milestones',      count(*) FROM progress_milestones
UNION ALL SELECT 'points_rules',             count(*) FROM points_rules
UNION ALL SELECT 'checklist_template_items', count(*) FROM checklist_template_items
UNION ALL SELECT 'interview_types',          count(*) FROM interview_types
UNION ALL SELECT 'interview_questions',      count(*) FROM interview_questions
UNION ALL SELECT 'interview_feedback_bands', count(*) FROM interview_feedback_bands
UNION ALL SELECT 'country_cost_of_living',   count(*) FROM country_cost_of_living
UNION ALL SELECT 'cost_of_living_categories',count(*) FROM cost_of_living_categories
UNION ALL SELECT 'currency_rates',           count(*) FROM currency_rates
UNION ALL SELECT 'workflow_templates',       count(*) FROM workflow_templates
UNION ALL SELECT 'workflow_stages',          count(*) FROM workflow_stages
ORDER BY 1;
```

## 3. Enable `pgcrypto`

The user insert computes bcrypt hashes in the database, so you never have to
paste a hash around. Neon ships the extension; you just have to switch it on
once per database.

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

### Why not just paste a bcrypt hash from an online generator?

Because `app/core/security.py` does **not** bcrypt the password directly. It
pre-hashes first — SHA-256 → base64 → bcrypt — so that passwords longer than
bcrypt's 72-byte limit don't blow up:

```python
def _prepare(password: str) -> bytes:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)          # always 44 bytes

def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt(rounds=12)).decode()
```

A hash from a plain bcrypt generator would therefore **never verify**. The SQL
below reproduces `_prepare` exactly:

```sql
crypt(encode(digest(<password>, 'sha256'), 'base64'), gen_salt('bf', 12))
```

`encode(...,'base64')` of a 32-byte digest is 44 characters with no line break,
which is byte-for-byte what Python's `base64.b64encode` returns, and
`gen_salt('bf', 12)` is the same cost factor the app uses in production.

## 4. Seed the users

Change `'ignition-dev-password'` in the last column of each row if you want a
different password — it is the plaintext, and the hash is computed per row with
its own salt. Existing accounts are left untouched (`ON CONFLICT (email)`), so
this will not reset anybody's password.

```sql
INSERT INTO users (email, first_name, last_name, role, status, must_change_password, password_hash)
SELECT
    v.email,
    v.first_name,
    v.last_name,
    v.role::user_role,
    'active'::user_status,
    v.must_change_password,
    crypt(encode(digest(v.password, 'sha256'), 'base64'), gen_salt('bf', 12))
FROM (VALUES
    ('owner@ignition.example.com',      'Ignition', 'Owner',    'super_admin', true,  'ignition-dev-password'),
    ('admin@ignition.example.com',      'Asha',     'Adhikari', 'admin',       true,  'ignition-dev-password'),
    ('manager@ignition.example.com',    'Manish',   'Karki',    'manager',     true,  'ignition-dev-password'),
    ('counsellor@ignition.example.com', 'Chandra',  'Bhatta',   'counsellor',  true,  'ignition-dev-password'),
    ('admissions@ignition.example.com', 'Anjali',   'Shrestha', 'admissions',  true,  'ignition-dev-password'),
    ('finance@ignition.example.com',    'Prakash',  'Thapa',    'finance',     true,  'ignition-dev-password'),
    ('frontdesk@ignition.example.com',  'Nisha',    'Gurung',   'frontdesk',   true,  'ignition-dev-password'),
    ('student@ignition.example.com',    'Sita',     'Rai',      'student',     false, 'ignition-dev-password'),
    ('student2@ignition.example.com',   'Bikash',   'Lama',     'student',     false, 'ignition-dev-password')
) AS v(email, first_name, last_name, role, must_change_password, password)
ON CONFLICT (email) DO NOTHING;
```

Confirm it worked — this returns `true` for every row if the password verifies
through the same code path the login endpoint uses:

```sql
SELECT email,
       role,
       must_change_password,
       password_hash = crypt(encode(digest('ignition-dev-password', 'sha256'), 'base64'), password_hash)
           AS password_ok
FROM users
WHERE email LIKE '%@ignition.example.com'
ORDER BY email;
```

## 5. Seed the catalog and reference data

Everything below is one block. You can paste it into the Neon SQL Editor in one
go — the editor wraps a multi-statement run in a single transaction, so either
all of it lands or none of it does. If you would rather go section by section,
each `INSERT` is independent apart from the ordering (countries before
universities, universities before programs, and so on).

```sql
-- ---------------------------------------------------------------- countries
INSERT INTO countries (name, iso2, iso3, phone_code, currency_code)
VALUES
    ('Australia',      'AU', 'AUS', '+61',  'AUD'),
    ('United Kingdom', 'GB', 'GBR', '+44',  'GBP'),
    ('Canada',         'CA', 'CAN', '+1',   'CAD'),
    ('United States',  'US', 'USA', '+1',   'USD'),
    ('Nepal',          'NP', 'NPL', '+977', 'NPR')
ON CONFLICT (iso2) DO NOTHING;

-- ------------------------------------------------------------- universities
-- `universities` has no unique constraint on name, so the guard is an explicit
-- NOT EXISTS rather than ON CONFLICT.
INSERT INTO universities (country_id, name, city, is_partner)
SELECT c.id, v.name, v.city, v.is_partner
FROM (VALUES
    ('AU', 'University of Melbourne',   'Melbourne',  true),
    ('AU', 'Monash University',         'Melbourne',  true),
    ('GB', 'University College London', 'London',     true),
    ('GB', 'University of Manchester',  'Manchester', false),
    ('CA', 'University of Toronto',     'Toronto',    true),
    ('US', 'Arizona State University',  'Tempe',      false)
) AS v(iso2, name, city, is_partner)
JOIN countries c ON c.iso2 = v.iso2
WHERE NOT EXISTS (SELECT 1 FROM universities u WHERE u.name = v.name);

-- ----------------------------------------------------------------- programs
INSERT INTO programs (university_id, name, degree_level, duration_months, tuition_fee, currency)
SELECT u.id, v.name, v.degree_level::degree_level, v.duration_months, v.tuition_fee, 'AUD'
FROM (VALUES
    ('University of Melbourne',   'Master of Information Technology', 'master',   24, 45000.0),
    ('University of Melbourne',   'Bachelor of Commerce',             'bachelor', 36, 42000.0),
    ('Monash University',         'Master of Data Science',           'master',   24, 44000.0),
    ('University College London', 'MSc Computer Science',             'master',   12, 38000.0),
    ('University of Toronto',     'Master of Engineering',            'master',   20, 52000.0)
) AS v(university, name, degree_level, duration_months, tuition_fee)
JOIN universities u ON u.name = v.university
WHERE NOT EXISTS (
    SELECT 1 FROM programs p WHERE p.name = v.name AND p.university_id = u.id
);

-- -------------------------------------------------------------- departments
INSERT INTO departments (name)
VALUES ('Admissions'), ('Counselling'), ('Finance'), ('Marketing'), ('Operations')
ON CONFLICT (name) DO NOTHING;

-- -------------------------------------------------------------- leave types
INSERT INTO leave_types (name, default_days_per_year, paid)
VALUES
    ('Annual Leave', 12, true),
    ('Sick Leave',   10, true),
    ('Unpaid Leave',  0, false)
ON CONFLICT (name) DO NOTHING;

-- -------------------------------------------------------- attendance policy
-- Singleton row. Without it `GET /attendance/policy` 404s and the attendance
-- page has nothing to measure check-ins against. Every other column defaults.
INSERT INTO attendance_policies (is_singleton)
VALUES (true)
ON CONFLICT (is_singleton) DO NOTHING;

-- ------------------------------------------------------ progress milestones
INSERT INTO progress_milestones (key, label, weight, "order")
VALUES
    ('profile',    'Profile complete',           10, 1),
    ('documents',  'Core documents uploaded',    15, 2),
    ('shortlist',  'Universities shortlisted',   10, 3),
    ('apply',      'Application submitted',      20, 4),
    ('interview',  'Interview completed',        10, 5),
    ('offer',      'Offer received',             15, 6),
    ('visa',       'Visa approved',              15, 7),
    ('departure',  'Ready to depart',             5, 8)
ON CONFLICT (key) DO NOTHING;

-- ------------------------------------------------------------- points rules
INSERT INTO points_rules (action, label, points, category, once_per_student)
VALUES
    ('profile.complete',    'Profile completed',          25, 'profile',      true),
    ('document.upload',     'Document approved',          15, 'documents',    false),
    ('task.complete',       'Checklist task completed',   20, 'tasks',        false),
    ('application.submit',  'Application submitted',      50, 'applications', false),
    ('interview.complete',  'Mock interview completed',   15, 'interviews',   false)
ON CONFLICT (action) DO NOTHING;

-- ------------------------------------------------- journey checklist template
-- Students receive their copy on first read of /student/me/checklist, so adding
-- a rung here reaches everyone without a backfill. `due_after_days` is an offset
-- from the day the student joined, not an absolute date.
INSERT INTO checklist_template_items (key, title, description, stage, "order", depends_on_key, due_after_days)
VALUES
    ('passport',  'Secure your passport',                'Apply for or renew a passport valid for at least six months beyond your intake.', 'Passport',  1, NULL,        30),
    ('ielts',     'Sit the IELTS exam',                  'Book and complete IELTS with a band score of 7.0 or higher.',                     'IELTS',     2, 'passport',  75),
    ('sop',       'Write your Statement of Purpose',     'Draft, review with your counsellor and finalise your SOP.',                       'SOP',       3, 'ielts',    105),
    ('lor',       'Collect Letters of Recommendation',   'Request two academic references and upload the signed letters.',                  'LOR',       4, 'sop',      120),
    ('apply',     'Submit your applications',            'Complete and submit applications to every shortlisted university.',               'Apply',     5, 'lor',      150),
    ('interview', 'Pass the admission interview',        'Practise with the AI interview module, then attend the university interview.',    'Interview', 6, 'apply',    180),
    ('offer',     'Accept your offer',                   'Review offer letters, compare conditions and confirm your place.',                'Offer',     7, 'interview',210),
    ('visa',      'Apply for your student visa',         'Assemble financial documents and lodge the student visa application.',            'Visa',      8, 'offer',    240),
    ('departure', 'Prepare for departure',               'Book flights, arrange accommodation and complete pre-departure briefing.',        'Departure', 9, 'visa',     270)
ON CONFLICT (key) DO NOTHING;

-- ---------------------------------------------------------- interview types
INSERT INTO interview_types (key, name, description, duration_minutes, passing_score)
VALUES
    ('academic', 'Academic Interview',    'Programme-fit questions asked by admissions faculty.',            20, 70),
    ('visa',     'Visa Interview',        'Consular questions about intent, funding and post-study plans.',  15, 75),
    ('merit',    'Merit Panel Interview', 'Competitive panel questions on achievements and leadership.',     25, 80)
ON CONFLICT (key) DO NOTHING;

-- ------------------------------------------------------ interview questions
-- Each type's max_score values sum to 100, so a session total is directly
-- comparable to passing_score and to the feedback bands' min_score.
INSERT INTO interview_questions (type_id, prompt, hint, max_score, "order")
SELECT t.id, v.prompt, v.hint, v.max_score, v."order"
FROM (VALUES
    ('academic', 'Why have you chosen this programme, and how does it connect to your undergraduate work?', 'Name two specific modules and link them to a project you have already built.', 25, 1),
    ('academic', 'Describe a technical project you led. What went wrong and how did you recover?',          'Use a situation → action → result structure and quantify the result.',        25, 2),
    ('academic', 'Which faculty member''s research would you want to work with, and why?',                  'Reference a paper or lab, not just a name.',                                  25, 3),
    ('academic', 'Where do you see yourself five years after graduating?',                                  'Tie the answer back to the skills the programme actually teaches.',           25, 4),
    ('visa',     'Why did you choose this country and this specific university?',                           'Compare against options at home to show a deliberate decision.',              25, 1),
    ('visa',     'Who is funding your studies and how will the funds be transferred?',                      'Name the sponsor, the relationship and the account documentation.',           25, 2),
    ('visa',     'What are your plans after you finish the programme?',                                     'Show clear ties to your home country.',                                       25, 3),
    ('visa',     'Do you have relatives in the country you are travelling to?',                             'Answer factually and briefly — do not volunteer extra detail.',               25, 4),
    ('merit',    'What is the achievement you are proudest of, and what did it cost you?',                  'Panels reward honesty about the trade-offs.',                                 34, 1),
    ('merit',    'Tell us about a time you changed someone''s mind.',                                       'Focus on how you listened before you argued.',                                33, 2),
    ('merit',    'What will you contribute to the cohort beyond your coursework?',                          'Be concrete — a club, a workshop, a mentoring commitment.',                   33, 3)
) AS v(type_key, prompt, hint, max_score, "order")
JOIN interview_types t ON t.key = v.type_key
WHERE NOT EXISTS (
    SELECT 1 FROM interview_questions q
    WHERE q.type_id = t.id AND q."order" = v."order"
);

-- ------------------------------------------------- interview feedback bands
-- Matched by score at completion: the band with the highest min_score the
-- session total meets or exceeds.
INSERT INTO interview_feedback_bands (key, min_score, band, tone, summary, strengths, improvements)
VALUES
    ('excellent', 85, 'Excellent',  'green',
     'Panel-ready. Your answers were specific, structured and well paced.',
     '["Every answer opened with a clear position","Concrete examples backed each claim","Confident, unhurried delivery"]'::jsonb,
     '["Trim the closing sentence on longer answers","Prepare one more research-specific reference"]'::jsonb),
    ('good', 70, 'Strong', 'blue',
     'A solid performance. Tighten your examples and you are interview ready.',
     '["Good programme knowledge","Clear motivation for studying abroad"]'::jsonb,
     '["Quantify results — numbers make projects memorable","Avoid repeating the question back before answering","Practise a 30-second version of your longest answer"]'::jsonb),
    ('average', 50, 'Developing', 'orange',
     'The substance is there but the structure is loose. Rehearse before the real thing.',
     '["Honest, natural tone","No factual contradictions"]'::jsonb,
     '["Use a situation → action → result structure","Name specific modules, labs or sponsors","Cut filler openings such as ''I think that maybe''","Rehearse the funding question until it is automatic"]'::jsonb),
    ('weak', 0, 'Needs work', 'red',
     'Answers were too short or too general to convince a panel. Try the set again after preparing notes.',
     '["You completed the full set — that is the first step"]'::jsonb,
     '["Write bullet notes for each question before retrying","Aim for at least three sentences per answer","Book a counsellor session to review your talking points"]'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- ---------------------------------------------------- country cost of living
INSERT INTO country_cost_of_living (country_id, proof_of_funds_name, note, tuition_usd, living_cost_usd, insurance_usd, visa_fee_usd, biometrics_usd)
SELECT c.id, v.proof_of_funds_name, v.note, v.tuition_usd, v.living_cost_usd, v.insurance_usd, v.visa_fee_usd, v.biometrics_usd
FROM (VALUES
    ('Canada',
     'Guaranteed Investment Certificate (GIC)',
     'IRCC requires proof of first-year tuition plus a GIC covering living costs.',
     42000, 15000, 900, 175, 63),
    ('Australia',
     'Genuine Student financial capacity evidence',
     'Home Affairs requires 12 months of living costs plus tuition and travel.',
     36000, 20000, 2100, 1030, 0),
    ('United Kingdom',
     '28-day maintenance funds statement',
     'Funds must sit in the account for 28 consecutive days before applying.',
     33000, 14000, 1400, 620, 25)
) AS v(country, proof_of_funds_name, note, tuition_usd, living_cost_usd, insurance_usd, visa_fee_usd, biometrics_usd)
JOIN countries c ON c.name = v.country
ON CONFLICT (country_id) DO NOTHING;

-- ----------------------------------------------- cost of living categories
INSERT INTO cost_of_living_categories (cost_of_living_id, category, amount_usd, essential, phase, "order")
SELECT col.id, v.category, v.amount_usd, v.essential, v.phase, v."order"
FROM (VALUES
    ('Canada', 'Tuition',                        42000, true,  'pre-arrival',  1),
    ('Canada', 'Living Expenses',                15000, true,  'ongoing',      2),
    ('Canada', 'Health Insurance',                 900, true,  'pre-arrival',  3),
    ('Canada', 'Visa Fee',                         175, true,  'pre-arrival',  4),
    ('Canada', 'Biometrics',                        63, true,  'pre-arrival',  5),
    ('Canada', 'Flight Tickets',                  1100, true,  'pre-arrival',  6),
    ('Canada', 'Accommodation Deposit',           1600, true,  'arrival',      7),
    ('Canada', 'Application Fees',                 480, true,  'pre-arrival',  8),
    ('Canada', 'IELTS / PTE',                      260, true,  'pre-arrival',  9),
    ('Canada', 'Initial Settlement Cost',         1800, false, 'arrival',     10),
    ('Canada', 'Miscellaneous Buffer',            2000, false, 'ongoing',     11),
    ('Australia', 'Tuition',                     36000, true,  'pre-arrival',  1),
    ('Australia', 'Living Expenses',             20000, true,  'ongoing',      2),
    ('Australia', 'Overseas Student Health Cover',2100, true,  'pre-arrival',  3),
    ('Australia', 'Visa Fee',                     1030, true,  'pre-arrival',  4),
    ('Australia', 'Flight Tickets',               1250, true,  'pre-arrival',  5),
    ('Australia', 'Accommodation Deposit',        2100, true,  'arrival',      6),
    ('Australia', 'Application Fees',              330, true,  'pre-arrival',  7),
    ('Australia', 'IELTS / PTE',                   260, true,  'pre-arrival',  8),
    ('Australia', 'Initial Settlement Cost',      2200, false, 'arrival',      9),
    ('Australia', 'Miscellaneous Buffer',         2400, false, 'ongoing',     10),
    ('United Kingdom', 'Tuition',                33000, true,  'pre-arrival',  1),
    ('United Kingdom', 'Living Expenses',        14000, true,  'ongoing',      2),
    ('United Kingdom', 'Immigration Health Surcharge', 1400, true, 'pre-arrival', 3),
    ('United Kingdom', 'Visa Fee',                 620, true,  'pre-arrival',  4),
    ('United Kingdom', 'Biometrics',                25, true,  'pre-arrival',  5),
    ('United Kingdom', 'Flight Tickets',           850, true,  'pre-arrival',  6),
    ('United Kingdom', 'Accommodation Deposit',   1400, true,  'arrival',      7),
    ('United Kingdom', 'Application Fees',         300, true,  'pre-arrival',  8),
    ('United Kingdom', 'IELTS / PTE',              260, true,  'pre-arrival',  9),
    ('United Kingdom', 'Initial Settlement Cost', 1500, false, 'arrival',     10),
    ('United Kingdom', 'Miscellaneous Buffer',    1800, false, 'ongoing',     11)
) AS v(country, category, amount_usd, essential, phase, "order")
JOIN countries c ON c.name = v.country
JOIN country_cost_of_living col ON col.country_id = c.id
WHERE NOT EXISTS (
    SELECT 1 FROM cost_of_living_categories x
    WHERE x.cost_of_living_id = col.id AND x.category = v.category
);

-- ----------------------------------------------------------- currency rates
-- A static snapshot, refreshed by re-running the UPDATE below rather than by a
-- live feed.
INSERT INTO currency_rates (code, name, symbol, per_usd, change_pct)
VALUES
    ('USD', 'US Dollar',        '$',   1,     0),
    ('NPR', 'Nepalese Rupee',   'Rs',  137.4, 1.8),
    ('CAD', 'Canadian Dollar',  'C$',  1.36, -0.4),
    ('AUD', 'Australian Dollar','A$',  1.52,  0.9),
    ('GBP', 'Pound Sterling',   '£',   0.78, -0.2),
    ('EUR', 'Euro',             '€',   0.92,  0.3),
    ('JPY', 'Japanese Yen',     '¥',   151.2, 2.4)
ON CONFLICT (code) DO NOTHING;

-- -------------------------------------------------------- default workflow
INSERT INTO workflow_templates (name, slug, description, is_default)
VALUES ('Standard Application', 'standard-application',
        'Default end-to-end pipeline from profile to departure.', true)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO workflow_stages (template_id, key, name, "order")
SELECT t.id, v.key, v.name, v."order"
FROM (VALUES
    ('profile',      'Profile & Documents',   0),
    ('shortlist',    'Course Shortlisting',   1),
    ('apply',        'University Application',2),
    ('offer',        'Offer & Acceptance',    3),
    ('visa',         'Visa Application',      4),
    ('predeparture', 'Pre-departure',         5)
) AS v(key, name, "order")
CROSS JOIN (SELECT id FROM workflow_templates WHERE slug = 'standard-application') t
ON CONFLICT (template_id, key) DO NOTHING;
```

## 6. Verify

Re-run the count query from [step 2](#2-check-what-is-already-seeded). A freshly
seeded database should show at least:

| table | rows |
| --- | --- |
| users | 9 |
| countries | 5 |
| universities | 6 |
| programs | 5 |
| departments | 5 |
| leave_types | 3 |
| attendance_policies | 1 |
| progress_milestones | 8 |
| points_rules | 5 |
| checklist_template_items | 9 |
| interview_types | 3 |
| interview_questions | 11 |
| interview_feedback_bands | 4 |
| country_cost_of_living | 3 |
| cost_of_living_categories | 32 |
| currency_rates | 7 |
| workflow_templates | 1 |
| workflow_stages | 6 |

Then hit the live API to confirm the app agrees:

```bash
curl -s -X POST https://<your-render-service>.onrender.com/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"owner@ignition.example.com","password":"ignition-dev-password"}'
```

A 200 with a token means the hash, the enum values and the role all line up. A
401 means the password hash did not verify — re-check that `pgcrypto` was
enabled *before* the user insert ran, and re-run the `password_ok` query in
step 4.

## 7. After seeding

These are documentation-domain addresses with a password that is written down in
a public repo, sitting on a reachable deployment. Once you have confirmed login:

- Sign in as each staff account and complete the forced password change.
- Change the two student passwords too — they are **not** flagged
  `must_change_password`. Either through the app, or in SQL:

  ```sql
  UPDATE users
  SET password_hash = crypt(encode(digest('a-real-password-here', 'sha256'), 'base64'), gen_salt('bf', 12)),
      must_change_password = true
  WHERE email IN ('student@ignition.example.com', 'student2@ignition.example.com');
  ```

- Or, if you only wanted the reference data and not the logins, disable the
  accounts outright:

  ```sql
  UPDATE users SET status = 'inactive' WHERE email LIKE '%@ignition.example.com';
  ```

## 8. Undoing it

The equivalent of `python -m scripts.seed --reset`. **Deleting a user cascades**
to their applications, documents, appointments, tasks, payments and notifications
— on a database with real activity, prefer the `status = 'inactive'` update
above.

```sql
BEGIN;

DELETE FROM users
WHERE email LIKE '%@ignition.example.com'
   OR email LIKE '%@ignition.test';

DELETE FROM workflow_templates WHERE slug = 'standard-application';   -- stages cascade

DELETE FROM programs WHERE name IN (
    'Master of Information Technology', 'Bachelor of Commerce',
    'Master of Data Science', 'MSc Computer Science', 'Master of Engineering');

DELETE FROM universities WHERE name IN (
    'University of Melbourne', 'Monash University', 'University College London',
    'University of Manchester', 'University of Toronto', 'Arizona State University');

DELETE FROM countries WHERE iso2 IN ('AU', 'GB', 'CA', 'US', 'NP');  -- cost-of-living cascades

DELETE FROM departments WHERE name IN
    ('Admissions', 'Counselling', 'Finance', 'Marketing', 'Operations');

DELETE FROM leave_types WHERE name IN ('Annual Leave', 'Sick Leave', 'Unpaid Leave');

-- Not touched by scripts/seed.py --reset; delete only if you want a clean slate.
-- DELETE FROM interview_types;          -- questions cascade
-- DELETE FROM interview_feedback_bands;
-- DELETE FROM checklist_template_items;
-- DELETE FROM progress_milestones;
-- DELETE FROM points_rules;
-- DELETE FROM currency_rates;
-- DELETE FROM attendance_policies;

COMMIT;
```

Check the result before you `COMMIT` — the Neon editor lets you run `ROLLBACK;`
instead if the row counts look wrong.

## 9. Keeping this in sync with `scripts/seed.py`

This file is a hand-maintained transcription. If you change the data in
`scripts/seed.py`, change it here too. The mapping is one-to-one:

| `seed.py` constant | table | idempotency key |
| --- | --- | --- |
| `STAFF`, `STUDENTS` | `users` | `email` |
| `COUNTRIES` | `countries` | `iso2` |
| `UNIVERSITIES` | `universities` | `name` (no DB constraint — `NOT EXISTS`) |
| `PROGRAMS` | `programs` | `(name, university_id)` (no DB constraint — `NOT EXISTS`) |
| `DEPARTMENTS` | `departments` | `name` |
| `LEAVE_TYPES` | `leave_types` | `name` |
| — | `attendance_policies` | `is_singleton` |
| `MILESTONES` | `progress_milestones` | `key` |
| `POINTS_RULES` | `points_rules` | `action` |
| `CHECKLIST_TEMPLATE` | `checklist_template_items` | `key` |
| `INTERVIEW_TYPES` | `interview_types` | `key` |
| `INTERVIEW_QUESTIONS` | `interview_questions` | `(type_id, order)` (no DB constraint — `NOT EXISTS`) |
| `INTERVIEW_FEEDBACK_BANDS` | `interview_feedback_bands` | `key` |
| `COUNTRY_COST_OF_LIVING` | `country_cost_of_living` | `country_id` |
| `COUNTRY_COST_OF_LIVING[*]["categories"]` | `cost_of_living_categories` | `(cost_of_living_id, category)` |
| `CURRENCY_RATES` | `currency_rates` | `code` |
| `WORKFLOW_STAGES` | `workflow_templates` / `workflow_stages` | `slug` / `(template_id, key)` |

Primary keys and timestamps are omitted everywhere on purpose — `id` defaults to
`gen_random_uuid()` and `created_at` / `updated_at` default to `now()`, exactly
as they do when SQLAlchemy inserts the same rows.
