# syntax=docker/dockerfile:1
# Multi-stage build: the builder stage has compilers/headers for any wheel
# that needs a native build (e.g. cryptography); the runtime stage ships
# only the installed packages + app code, never pip/build tooling.
#
# Build context is the repo root (see .github/workflows/ci.yml's `docker
# build -f backend/Dockerfile .`), not ./backend -- app/core/i18n.py reads
# translation catalogs from packages/i18n/locales/, which lives outside
# backend/'s own directory (T-068). Every COPY below is therefore prefixed
# with backend/ except the one that reaches across to packages/i18n.

FROM python:3.12-slim AS builder

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY backend/pyproject.toml ./
COPY backend/app ./app

RUN pip install --no-cache-dir --prefix=/install .

# Builds packages/email-templates' cli.js (T-106) -- send_email shells out to
# it at runtime, but the runtime stage below only ever runs a bare `node`
# binary, never pnpm/node_modules, so the build has to happen in its own
# stage. `COPY . .` (the full repo, not just the package) because pnpm's
# workspace install otherwise expects every workspace member on disk to
# match the lockfile -- simplest to satisfy that than to fight a partial
# checkout. None of this stage's image survives past the COPY --from below.
FROM node:22-slim AS email-templates-builder

WORKDIR /repo

RUN corepack enable && corepack prepare pnpm@11.8.0 --activate

COPY . .

# turbo (not a plain `pnpm --filter` build): @forge/email-templates depends
# on @forge/i18n's *compiled* dist/, which a fresh checkout doesn't have --
# turbo.json's build task is `dependsOn: ["gen-client", "^build"]`, so this
# builds @forge/i18n first automatically. (`gen-client`, which needs a live
# backend, only applies to @forge/api-client -- not in this filter's graph.)
RUN pnpm install --frozen-lockfile \
    && pnpm exec turbo run build --filter=@forge/email-templates

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/install/bin:$PATH \
    PYTHONPATH=/install/lib/python3.12/site-packages

# `node` only -- no npm/pnpm, no node_modules; send_email (T-106) shells out
# to the dependency-free cli.js copied in below, nothing else needs Node.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# No shell, no home dir, no login -- this user can run the app and nothing else.
RUN groupadd --system app \
    && useradd --system --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /install /install
COPY backend/app ./app
COPY backend/product ./product
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY packages/i18n/locales ./locales
COPY --from=email-templates-builder /repo/packages/email-templates/dist ./email-templates

RUN chown -R app:app /app
USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
