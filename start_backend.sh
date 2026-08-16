#!/usr/bin/env bash
# Starts the Ignition backend for local development:
#   1. brings up Postgres + Redis (Docker, if not already running)
#   2. creates/activates the venv and installs deps
#   3. applies pending Alembic migrations
#   4. runs uvicorn with --reload on :8001
#
# Datastores run in Docker; the API itself runs on the host so --reload works
# without a bind mount. See ../README.md and README.md for the two options
# this mirrors ("Option B").

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -f .env ]; then
  echo "No .env found — copying .env.example. Review it before running against anything but local dev." >&2
  cp .env.example .env
fi

echo "==> Starting Postgres + Redis (db, redis)"
docker compose up -d db redis

if [ ! -d .venv ]; then
  echo "==> Creating virtualenv"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies"
pip install -q -e ".[dev]"

echo "==> Applying migrations"
alembic upgrade head

echo "==> Starting API on http://localhost:8001 (docs at /docs)"
exec uvicorn app.main:app --reload --port 8001
