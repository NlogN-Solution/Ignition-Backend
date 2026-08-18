# syntax=docker/dockerfile:1

# ── builder ───────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
COPY app ./app

RUN pip install --upgrade pip && pip install .

# ── runtime ───────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system --gid 1001 ignition \
 && useradd --system --uid 1001 --gid ignition --create-home ignition

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=ignition:ignition . .

USER ignition

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", 8000)}/api/v1/health').status==200 else 1)"

# Render (and some other hosts) assign the listen port via $PORT at runtime
# rather than honoring EXPOSE; default to 8000 for docker-compose/local use.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
