# ForgeSaaS

A multi-tenant SaaS starter: auth, organisations, billing, file uploads, notifications and GDPR tooling, with a web app, a mobile app and a marketing site on top. It runs in production at [mycalendar.party](https://mycalendar.party).

## Architecture

- **Backend:** FastAPI, SQLModel, PostgreSQL 16, Redis 7, ARQ background jobs.
- **Frontend:** React 18 with TypeScript, Vite and TanStack Router/Query (`apps/web`); Expo for mobile (`apps/mobile`); Astro for the marketing site (`apps/marketing`).
- **Infrastructure:** Docker Compose behind Caddy (automatic TLS) on a Hetzner server, with pgbouncer, MinIO for uploads and ClamAV for scanning them.

The backend has four layers, router → service → repository → model, and imports may only flow downward. That rule is checked in CI, not left to review. Every repository is constructed with an `org_id` and adds it to every query, so one tenant reading another's data has to be written on purpose rather than forgotten by accident.

## What's in it

- **Auth:** argon2 passwords and RS256 JWTs, with rotating refresh tokens. Reusing an old refresh token is treated as theft and revokes the session. Also included: TOTP and WebAuthn second factors, step-up auth for admin actions, OAuth (Google, GitHub) and per-org OIDC SSO.
- **Billing:** Stripe Checkout and the customer portal. Webhooks are idempotent, and out-of-order events are detected and ignored.
- **GDPR:** data export as a background job; account deletion with a grace period and a purge that covers every table and stored file.
- **Public API:** org-scoped, hashed API keys with per-key rate limits, and signed outbound webhooks with retries and replay.
- **Quality gates in CI:**
  - about 2,000 backend tests against real Postgres and Redis, with an 85% coverage floor;
  - Playwright end-to-end, accessibility and visual-regression tests;
  - contract tests generated from the OpenAPI schema;
  - query-count budgets that fail the build on an N+1;
  - security scans: secrets, dependencies, container images, Semgrep and OWASP ZAP;
  - Lighthouse budgets;
  - k6 load tests with asserted latency and error budgets.
- **Operations:** Prometheus metrics, Grafana dashboards and alert rules, Sentry, encrypted nightly backups with a scripted restore, and runbooks in `docs/runbooks/`.

## A short tour

| File | Why it's worth reading |
|---|---|
| [`backend/app/core/repository.py`](backend/app/core/repository.py) | `BaseRepository`: tenant scoping that every query inherits |
| [`backend/.importlinter`](backend/.importlinter) | The four-layer rule as an enforced contract |
| [`backend/app/modules/auth/service.py`](backend/app/modules/auth/service.py) | `refresh()`: token rotation, theft detection and periodic re-authentication |
| [`backend/app/modules/billing/webhooks.py`](backend/app/modules/billing/webhooks.py) | Idempotent Stripe webhook handling that survives retries and crashes |
| [`backend/tests/test_query_budgets.py`](backend/tests/test_query_budgets.py) | Every list endpoint has a query budget; an N+1 fails the build |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | The full pipeline, from lint to deploy with automatic rollback |
| [`docs/adr/0001-security-baseline.md`](docs/adr/0001-security-baseline.md) | OWASP ASVS L2 mapped to the code and tests that satisfy it |

## How it was built

I designed the architecture and the rules, wrote the specs, and reviewed and tested everything; AI agents (Claude) wrote much of the code. [`AGENTS.md`](AGENTS.md) is the rulebook every change is held to, and the CI gates above are what enforce it. The co-authored commits in the history are that process, not something to hide.

## Run it locally

Requires Docker, Node 22.13+ with pnpm, and Python 3.12 with uv.

```bash
pnpm install
make dev     # starts the stack and waits for the backend
make seed    # demo org, users and plans
make test
```
