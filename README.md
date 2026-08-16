# Ignition Backend

Single-tenant education platform API — serves both the **staff dashboard**
(CRM, admissions, HR) and the **student portal**.

Built by porting ED360's FastAPI backend with all multi-tenancy removed, then
extending it with an advanced student portal. See `../../IGNITION_PLATFORM_PLAN.md`
for the full plan and the strip rules.

**Status: Phase 0 complete** — tooling, container stack, and test harness are in
place. No domain models yet; Phase 1 ports them.

---

## Quickstart

```bash
cp .env.example .env
docker compose up -d          # postgres :5433, redis :6380, api :8001
curl http://localhost:8001/api/v1/health
```

Interactive docs: <http://localhost:8001/docs> (disabled in production).

### Local development without Docker for the app

```bash
docker compose up -d db redis   # keep the datastores in containers
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8001
```

## Ports

Offset from the defaults because ED360's stack already occupies the usual ones
on the dev machine. Inside the compose network, services use standard ports.

| Service | Host | ED360 uses |
|---|---|---|
| API | **8001** | 8000 |
| PostgreSQL | **5433** | 5432 |
| Redis | **6380** | 6379 |
| Admin dashboard (Vite) | **5174** | 5173 |
| Student portal (CRA) | **3000** | — |

## Commands

```bash
pytest                          # test suite
pytest --cov=app                # with coverage
ruff check . && ruff format .   # lint + format
mypy app                        # type check

alembic revision --autogenerate -m "message"
alembic upgrade head
alembic downgrade -1
```

CI runs lint, format check, mypy, and pytest on every push and PR.

---

## Layout

```
app/
├── main.py           app assembly, CORS, error handling
├── core/             config, logging, middleware  (+ security, rbac, events in later phases)
├── db/               declarative base, session, enum helper, mixins
├── api/              shared dependencies, exception types, router composition
├── models/           SQLAlchemy models          — Phase 1
├── routes/           HTTP handlers              — Phase 1
├── schemas/          Pydantic request/response  — Phase 1
└── services/         business logic             — Phase 1
migrations/           Alembic
tests/                pytest
```

Everything is served under **`/api/v1`**. ED360 mounts at the root and must keep
that for its existing frontend; Ignition has no legacy clients, so it starts
versioned.

---

## Conventions worth knowing

**Single tenant, enforced by test.** There is no `Organization`, no
`organization_id`, no `TenantMixin`. `tests/test_no_multitenancy.py` fails the
build if any tenancy reference reappears — the Phase 1 port touches 74 files and
this is the guard that catches a missed one.

**Relative imports are fine.** ED360 uses `from ..models import User` throughout
and Phase 1 ports it mechanically, so ruff's `TID` rules are deliberately not
enabled. Don't turn them on; it would convert a copy into a rewrite.

**Two database URLs.** `settings.database_url` is sync (`psycopg`) for Alembic;
`settings.async_database_url` is async (`asyncpg`) for the app. They are not
interchangeable.

**Secrets fail closed.** `JWT_SECRET_KEY` is generated per-process in
development, and in production a missing, short, or known-placeholder value
refuses to boot. Do not copy ED360's `.env` across — sharing a JWT secret
between two systems means compromising one forges tokens for the other.

### The enum trap: drop them on downgrade

An earlier draft of this section had it backwards, so to be precise about what
was actually measured: the `create_type=False` that `db/types.py` passes is
**inert** — it belongs to `postgresql.ENUM`, but the helper builds a generic
`sa.Enum`, which silently absorbs it. Table DDL therefore *does* emit
`CREATE TYPE`, for `create_all` and `op.create_table` alike.

The consequence runs the other way. `op.drop_table` does **not** drop enum
types, so a downgrade leaves all 37 behind and the next upgrade dies with:

```
DuplicateObject: type "user_role" already exists
```

**Every migration that introduces an enum must drop it in `downgrade()`.**
`0001_initial` does this with its `ENUM_TYPES` tuple. Do not hand-add a
`CREATE TYPE` block to `upgrade()` — the types already exist by then.

Verify any new migration round-trips, rather than only testing upgrade:

```bash
alembic upgrade head && alembic downgrade base && alembic upgrade head
```

### Testing

`tests/conftest.py` gives every test a session bound to a transaction that is
always rolled back, so tests share one schema build and never leak state. Two
non-obvious settings hold it together:

- `asyncio_default_fixture_loop_scope` **and** `asyncio_default_test_loop_scope`
  are both `session`. asyncpg binds a connection to the loop that opened it; with
  tests on per-function loops, any database access raises "attached to a
  different loop". This is why `pytest-asyncio>=1.0` is required.
- The test engine uses `NullPool` so no connection outlives its checkout.

---

## Deferred decisions

Recorded here so they are chosen deliberately rather than by drift.

| Decision | Current state | When to revisit |
|---|---|---|
| **Dependency versions** | Pinned to match ED360 exactly, so the Phase 1 port is a copy and not a copy-plus-upgrade | After Phase 1 lands and the suite is green |
| **passlib + bcrypt** | Kept because ED360 uses it, keeping the port mechanical. passlib is unmaintained and warns on bcrypt ≥ 4.1 | Phase 2. Ignition has no existing password hashes, so switching to `pwdlib` or bcrypt directly is free — a luxury ED360 doesn't have |
| **Repository layer** | Not present; services talk to the session directly, as in ED360 | Phase 5, where `StudentScopedRepository` makes student data ownership structural |
| **Celery worker** | Defined in compose behind the `worker` profile, no tasks yet | Phase 5, when the event bus gains handlers that must not block a request |
| **Git strategy** | `backend/`, `admin-frontend/`, `student-frontend/` — `student-frontend` is its own repo; `backend` is not yet initialised | Before the first commit: one monorepo, or three repos? CI config assumes `backend/` is a repo root |
