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

COPY backend/pyproject.toml backend/requirements.lock ./
COPY backend/app ./app

# Repo finding 16: this used to be `pip install .`, which re-resolves every
# open-ended minimum in pyproject.toml at build time -- so the same commit
# produced different software on different days, and "it passed locally"
# and "it runs in production" were claims about two different dependency
# trees. The repo's own comments already record two resolver-driven
# incompatibilities discovered that way.
#
# Two steps, and the order matters. First the locked dependency set, with
# --require-hashes: every wheel is pinned by digest, so a compromised or
# re-uploaded artifact fails the build instead of shipping. Then the app
# itself with --no-deps, because its dependencies are already installed and
# letting pip resolve them again is the thing this replaces.
#
# Regenerate with `make lock` after changing pyproject.toml. CI's pip-audit
# runs against the same file, so a vulnerable pin fails there rather than
# on a box.
RUN pip install --no-cache-dir --prefix=/install --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --prefix=/install --no-deps .

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
# `libmagic1` backs python-magic (T-225's content-sniffing on upload confirm).
RUN apt-get update && apt-get install -y --no-install-recommends nodejs libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# No shell, no home dir, no login -- this user can run the app and nothing else.
RUN groupadd --system app \
    && useradd --system --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Both assets are resolved relative to the *imported* `app` package, and the
# image has two copies of it: this pip-installed one, and the ./app COPY
# below. Which wins depends on the working directory, because a console
# script (`uvicorn`, `arq`) does not put the cwd on sys.path -- so production
# imports from site-packages while a shell imports from /app. These two
# variables end the guessing by naming the paths the COPYs below actually
# use. Without them the image passes every test and cannot render an email or
# resolve a translation once deployed.
ENV EMAIL_RENDERER_CLI=/app/email-templates/cli.js     LOCALES_DIR=/app/locales

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
