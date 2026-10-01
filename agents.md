# ForgeSaaS — Agent Build Plan

> **Source of truth:** `forgesaas-plan.html`. Every task in this file maps directly to a section of that document. When in doubt, re-read the HTML section named in the task header.
>
> **How to use:** Read §0 once per session. It is binding on all work in this repo.
>
> Phases 1 to 4 are built. Their task specs, with the notes on what each one found,
> are archived in `docs/plan-archive/` (index below) so this file stays short
> enough to read every session. Open work lives in the dated plans in `docs/`
> (`production-readiness-plan-*.md`, `code-review-*.md`).

---

## §0 Laws — read once, apply everywhere

These are not guidelines. Every task is checked against them before marking done. A violation is a defect regardless of whether the specific task mentioned the rule.

### §0.1 Architecture — four-layer rule

```
HTTP Request
  → Router      (HTTP only: validate input, call service, return schema — no DB, no logic)
  → Service     (business logic only: calls repos + other services — no HTTP, no DB queries)
  → Repository  (DB only: always org-scoped — no logic, no HTTP)
  → Model       (data shape only: SQLModel table — no methods beyond simple validators)
```

- Imports flow **downward only**. A service must never import a router. A repository must never import a service. Enforced in CI with `import-linter`.
- Cross-module communication goes through services only. Never reach into another module's repository.

### §0.2 Multi-tenancy — org_id is sacred

- Every user-data table has `org_id` as an indexed FK. No exceptions.
- `BaseRepository.__init__(session, org_id)` — `org_id` is required, has no default.
- Every query in every repository includes `WHERE org_id = self.org_id`. This makes cross-tenant data access structurally impossible.
- Only `User`, `Session`, `OAuthAccount`, `TOTPSecret`, `ConsentRecord` are scoped by `user_id` instead (they belong to the user, not the org).

### §0.3 Data integrity

- **Cursor pagination only.** `OFFSET` is forbidden. Cursor: `(created_at DESC, id DESC)`.
- **No raw SQL** without `sqlalchemy.text()` + bound params + a comment explaining why.
- **`selectinload` for collections**, not `joinedload` (cartesian product hazard). Document any exception.
- **Every FK and filter column has an index** in the same migration that adds it.
- **Every migration has a working `downgrade()`**, tested in CI: up → down → up.
- **No business logic in migrations.** Backfills are separate idempotent jobs.
- **Timestamps + soft-delete on every table** via a shared mixin: `created_at`, `updated_at`, `deleted_at`.

### §0.4 API contract

- All routes under `/api/v1/`. Versioned from the first endpoint.
- All responses use `{ data, error, meta }` envelope. Never raw model dumps.
- Request schemas are **separate Pydantic classes** from model classes. No mass-assignment (`model.update(**request.dict())` is forbidden — enforced by Semgrep rule).
- Idempotency keys on every mutating external call: Stripe events, outbound webhooks, email sends.

### §0.5 Error handling

- Services raise **typed `DomainError` subclasses** (`NotFoundError`, `PermissionDeniedError`, `PlanLimitError`, etc.) — never `HTTPException`.
- One global handler translates `DomainError` → HTTP envelope. Router owns translation, not service.
- `except Exception` is forbidden except in the single top-level handler.
- Error messages are user-actionable. Internal detail never leaks to the response body.

### §0.6 Security — non-negotiable

- Passwords: **argon2-cffi** only.
- JWTs: **RS256** (asymmetric). Private key in env. Access TTL: 15 min. Refresh TTL: 30 days, rotating, stored as SHA-256 hash.
- Web refresh tokens: `httpOnly; Secure; SameSite=Strict` cookie. Mobile: `expo-secure-store` (Keychain/Keystore). Never `AsyncStorage` for auth tokens.
- CORS `allow_origins` from settings. `["*"]` in any env that has real credentials → startup error.
- Security headers always on: HSTS, CSP (`default-src 'self'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`.
- Rate limits: 5/min on login/register/forgot/magic-link; 3/min on password-reset confirm; 100/min per user general, shared across every endpoint; 5000/hr per org (raised from 1000/hr on 2026-09-29, pending measurement; charged only after membership is verified). Sliding window.
- All secrets via `pydantic-settings` from env. `os.environ` direct access is banned (Semgrep rule).
- No secrets, tokens, or PII in log lines. Log scrubber strips known keys.
- **HTTPS is mandatory in staging and prod.** Caddy terminates TLS automatically in front of every service. The backend never serves plain HTTP outside dev. The config startup check (T-003) raises `RuntimeError` if `ENVIRONMENT != "dev"` and `FORCE_HTTPS != "true"`. The Caddyfile (T-004.5) redirects all HTTP → HTTPS with a 301.
- **Sensitive DB columns use Fernet encryption at rest** via the `EncryptedString` SQLAlchemy TypeDecorator built in T-009.5. Fernet provides AES-128-CBC + HMAC-SHA256 authentication — a tampered ciphertext raises, it does not silently return garbage. Columns that **must** use `EncryptedString`: `TOTPSecret.secret_encrypted`, any column holding an OAuth access/refresh token if stored, any future column holding a private key or secret credential. Rule of thumb: if the value is a secret and it lives in the DB, it uses `EncryptedString`. Key rotation is zero-downtime: set `ENCRYPTION_KEY_OLD` alongside `ENCRYPTION_KEY`, deploy, re-encrypt old rows via a background job, then remove the old key.

### §0.7 Typing & style — enforced, not suggested

- Backend: `mypy --strict`. No `Any`. No untyped functions.
- Frontend: `tsc --strict`. No `any`. No `@ts-ignore` without a justification comment.
- Formatter: `ruff format` (backend), `prettier` (frontend). Not configurable per developer.
- Linter: `ruff` (backend), `eslint` (frontend). Both fail CI on any violation.
- All of the above run as pre-commit hooks and CI gates.

### §0.8 Testing — required, not optional

- A task is not done until its tests pass locally.
- Coverage floors: **85% overall**, **100% on `auth/`, `billing/`, `tenancy/`, `gdpr/`**.
- Integration tests use **real Postgres + Redis** via `testcontainers`. No SQLite.
- Every test runs in a **rolled-back transaction**. DB is pristine after every test.
- Non-negotiable test subjects: cross-tenant isolation, every RBAC gate, refresh token rotation + theft detection, Stripe webhook idempotency, cursor pagination correctness, GDPR purge completeness, rate-limit enforcement.

### §0.9 Performance

- All DB sessions and endpoints are **async**. Sync DB call = defect.
- DB pool: `min=5, max=20`. Never a per-request connection.
- Slow operations (email, file processing, 3rd-party calls, report generation) go to **ARQ jobs**. Never in the request path.
- Redis cache on hot reads (user profile, org settings, plan features): 60 s TTL, explicit invalidation on write.

### §0.10 Definition of done (every task)

Before marking any task complete:

1. `ruff check . && ruff format --check .` clean
2. `mypy --strict app/` clean
3. `pnpm lint && pnpm type-check` clean (frontend tasks)
4. Task's listed tests pass
5. `pytest --cov` thresholds not regressed
6. No new `# type: ignore` / `any` without a justification comment
7. Pre-commit hooks pass

### §0.11 Internationalisation — every user-facing string

This law applies from T-067 onwards. Before T-067, English literals are tolerated; after T-067, they are a defect.

- **No hardcoded user-facing strings.** Every label, button, error, email subject, notification body, validation message, and toast goes through the `t()` helper (backend) or `useTranslation()` hook (frontend). System logs, exception messages for developers, and audit `action` strings stay in English — those are not user-facing.
- **Translation keys are namespaced by surface:** `auth.login.email_label`, `billing.error.payment_failed`, `emails.invitation.subject`. Never bare keys like `submit` or `error`.
- **English is the source of truth.** All keys exist in `packages/i18n/locales/en/` first. Other locales are translations; missing keys fall back to English, never to the raw key string.
- **The user's locale is resolved server-side, in this order:** `User.locale` if set → `Accept-Language` header → `en`. The resolved locale is attached to `request.state.locale` by middleware and read by every translation call.
- **Emails are localised at send time**, not at template definition time. The ARQ email job reads the recipient's `User.locale` and selects the matching template/key set.
- **Date, number, and currency formatting** uses the user's locale — never hardcoded `toLocaleString('en-US')` or `formatDate(date)` without an explicit locale argument.
- **CI guard:** a lint rule flags string literals inside JSX/TSX `<Text>` or as children of UI components in `apps/web/`, `apps/mobile/`, and `packages/ui/`. Exceptions require a `// i18n-ok: <reason>` comment.

### §0.12 Active supply-chain advisories — read before any `pnpm install`

This section is **dated** — new advisories are appended at the top; old ones stay until their mitigation has been verified on `main` for at least 30 days. Every install command in every task implicitly carries the mitigations listed here.

#### ADV-2026-001 — TanStack npm compromise (May 2026)

> CVE-2026-45321 · GHSA-g7cv-rxg3-hmpx · CVSS 9.6 · Critical
> Published: 2026-05-11 19:20–19:26 UTC · Status: malicious versions deprecated, mitigation required

On May 11 2026 a CI compromise published 84 malicious versions across 42 `@tanstack/*` packages with valid SLSA provenance. The malware exfiltrated cloud credentials, GitHub/npm tokens, Kubernetes/Vault tokens, and SSH keys from install hosts. All malicious versions are now deprecated, but stale lockfiles, mirrors, and dependency caches can still pull them.

**Mandatory before every install of a `@tanstack/*` package in this plan:**

- **Affected family — pin to a post-incident clean version:**
  - `@tanstack/react-router` — `>= 1.170.4`
  - `@tanstack/react-router-devtools`, `@tanstack/router-plugin`, `@tanstack/router-cli`, `@tanstack/router-vite-plugin`, `@tanstack/vue-router`, `@tanstack/solid-router`, `@tanstack/start-*` (the framework packages, not the bare `@tanstack/start` meta) — use the latest published version of each, never any version published in the May 11 2026 window.
- **Confirmed-clean families** (unaffected by ADV-2026-001, still pin latest as a habit): `@tanstack/react-query`, `@tanstack/table`, `@tanstack/form`, `@tanstack/virtual`, `@tanstack/store`, the bare `@tanstack/start` meta-package.
- **Lockfile hygiene:** if scaffolding this repo against an older fork, **delete `pnpm-lock.yaml`** and regenerate. Do not carry over any lockfile dated before 2026-05-13.
- **Pin via root overrides** so every transitive dep resolves to a safe version. In root `package.json`:
  ```json
  {
    "pnpm": {
      "overrides": {
        "@tanstack/react-router": ">=1.170.4",
        "@tanstack/react-router-devtools": ">=1.167.0",
        "@tanstack/router-plugin": ">=1.170.0"
      }
    }
  }
  ```
- **Audit on every install:** the `Makefile install` target must run `pnpm audit --audit-level=high` after `pnpm install`; non-zero exit fails the task. The CI `security` job (T-054) already enforces this — verify the GHSA is flagged by deliberately downgrading on a throwaway branch before merging.
- **If `pnpm audit` reports GHSA-g7cv-rxg3-hmpx** during scaffolding: treat the install environment as potentially compromised. Rotate every credential the host had access to (cloud, GitHub, npm, Kubernetes, Vault, SSH), wipe `node_modules` and the pnpm store (`pnpm store prune`), and rebuild from a clean checkout.

---

## §1 Stack reference

Load this section if a task asks "which library do I use for X?"

| Concern           | Choice                                             | Why                                                             |
| ----------------- | -------------------------------------------------- | --------------------------------------------------------------- |
| API               | FastAPI                                            | Async-native, auto OpenAPI, Pydantic v2                         |
| ORM               | SQLModel                                           | One class = Pydantic schema + SA table                          |
| Migrations        | Alembic                                            | autogenerate works well with SQLModel                           |
| DB                | PostgreSQL 16                                      | JSONB, proven at scale                                          |
| Cache/Queue       | Redis 7                                            | Sessions, rate limits, ARQ — one dep                            |
| Background jobs   | ARQ                                                | Async-native, zero broker, Redis-backed                         |
| Password hashing  | argon2-cffi                                        | OWASP recommendation                                            |
| Column encryption | cryptography (Fernet)                              | AES-128-CBC + HMAC-SHA256; authenticated; key rotation built in |
| Social auth       | authlib                                            | Clean OAuth2, no magic                                          |
| Email             | Resend                                             | Modern API, React Email templates                               |
| Storage           | Abstract → S3/R2/MinIO                             | Interface pattern, swap without code change                     |
| Billing           | Stripe                                             | Market standard                                                 |
| Web               | React 18 + Vite + TanStack Router + TanStack Query | Stable, type-safe, no Redux                                     |
| UI components     | shadcn/ui + Tailwind                               | Primitives you own                                              |
| Forms             | React Hook Form + Zod                              | Zod shared with mobile                                          |
| Mobile            | Expo SDK + Expo Router                             | File-based routing, OTA updates                                 |
| Mobile storage    | expo-secure-store                                  | Keychain/Keystore                                               |
| Monorepo          | Turborepo + pnpm workspaces                        | Cacheable builds                                                |
| Logging           | Loguru → JSON                                      | Structured from day one                                         |
| Tracing           | OpenTelemetry                                      | Vendor-neutral                                                  |
| Metrics           | Prometheus + Grafana                               | Standard, self-hosted                                           |
| Error tracking    | Sentry                                             | Works on FastAPI + React + RN                                   |
| Reverse proxy     | Caddy                                              | Auto TLS, simple config                                         |

---

## §2 Repo layout — must match exactly

```
forge-saas/
├── apps/
│   ├── web/            # React 18 + Vite + TanStack
│   └── mobile/         # Expo + React Native + Expo Router
├── packages/
│   ├── ui/             # shadcn/ui + Tailwind — owned primitives
│   ├── api-client/     # auto-generated from OpenAPI (openapi-ts)
│   ├── types/          # shared TS enums: Permission, MemberRole, etc.
│   ├── hooks/          # shared React hooks — NO JSX imports
│   ├── validation/     # Zod schemas shared by web + mobile
│   └── utils/          # pure TS: formatDate, formatBytes, cn, slugify
├── backend/
│   ├── app/
│   │   ├── core/           # config, db session, exceptions, envelope
│   │   ├── middleware/      # request_id, security_headers, rate_limit, tenancy
│   │   ├── models/          # SQLModel table classes — single source of truth
│   │   ├── schemas/         # Pydantic request/response — separate from models
│   │   ├── repositories/    # base repository
│   │   ├── services/        # (cross-module shared services only)
│   │   ├── api/v1/          # router registry — thin, no logic
│   │   ├── workers/         # ARQ worker + APScheduler
│   │   └── modules/
│   │       ├── auth/        # models, schemas, repo, service, router, utils
│   │       ├── tenancy/     # org, membership, invitation
│   │       ├── billing/     # plan, subscription, webhooks
│   │       ├── notifications/
│   │       ├── storage/
│   │       ├── gdpr/
│   │       └── _template/   # cookiecutter for make new-module
│   ├── alembic/             # migrations root — NOT inside app/
│   └── tests/
├── docs/
│   ├── adr/                 # architecture decision records
│   └── runbooks/
├── .github/workflows/
├── docker-compose.yml
├── docker-compose.prod.yml
├── Makefile
├── turbo.json
├── pnpm-workspace.yaml
└── .env.example
```

### Module anatomy (every feature module is identical in shape)

```
modules/<name>/
├── models.py       # SQLModel table definitions
├── schemas.py      # Pydantic request/response schemas
├── repository.py   # extends BaseRepository — DB access only
├── service.py      # business logic — no DB calls, no HTTP
├── router.py       # routes — calls service, returns envelope
└── README.md       # public interface + events emitted
```

---

## §3 Task format

Every task below follows this template. Read **only the current task** — each is fully self-contained.

```
### T-NNN Title
> HTML ref: §"Section name" in forgesaas-plan.html
> needs: T-XXX, T-YYY

**Goal** one sentence.

**Build**
- what to create

**Tests** (write these — task is not done without them)
- what to verify

**Done when**
- acceptance criteria checklist
```

---

# Task index

Every task by number, with where its full spec lives. Code comments cite tasks as `T-NNN`; look them up here.

**Phase 1 — Launchable skeleton** — [`docs/plan-archive/phase-1.md`](docs/plan-archive/phase-1.md)

- T-001 Monorepo skeleton
- T-002 Backend project + FastAPI app
- T-003 Configuration module
- T-004 Docker Compose — local dev
- T-004.5 Caddy — HTTPS termination (dev + prod)
- T-005 DB session + async engine
- T-006 Domain exceptions + response envelope
- T-007 Middleware — request ID, security headers, CORS
- T-008 Rate limiting
- T-009 SQLModel base mixins + BaseRepository
- T-009.5 EncryptedString TypeDecorator + RedactedReprMixin
- T-010 Alembic setup
- T-011 Structured logging
- T-012 Pre-commit hooks + import-linter
- T-013 Auth models + migration
- T-014 Auth cryptography utilities
- T-015 Auth schemas
- T-016 Auth repository
- T-017 Auth service — register + verify email
- T-018 Auth service — login + lockout
- T-019 Auth service — token refresh + theft detection
- T-020 Auth service — password reset + magic link
- T-021 RBAC — permissions + dependency
- T-022 Auth router
- T-023 OAuth — Google + GitHub
- T-024 TOTP 2FA
- T-025 Tenancy models + migration
- T-026 Tenancy repository + service
- T-027 Tenancy router
- T-028 Audit model + service + migration
- T-029 Billing models + migration
- T-030 Stripe webhook handler
- T-031 Billing service + checkout endpoints
- T-032 GDPR models + migration
- T-033 GDPR service + endpoints
- T-034 ARQ worker setup + APScheduler
- T-035 Email job (Resend + templates)
- T-036 GDPR purge job
- T-037 Export job
- T-038 Shared packages scaffold
- T-039 API client generation
- T-040 Vite + React skeleton
- T-041 UI component library (shadcn)
- T-042 TanStack Router + route structure
- T-043 Auth feature — all auth pages
- T-044 App shell + settings pages
- T-045 Expo app skeleton
- T-046 Mobile auth — secure token storage + auto-refresh
- T-047 Mobile auth screens + deep links
- T-048 Backend test infrastructure
- T-049 Cross-tenant isolation sweep
- T-050 Schemathesis contract test
- T-051 Frontend test setup
- T-052 CI — lint + type-check job
- T-053 CI — test job
- T-054 CI — security scans job
- T-055 CI — build + deploy job
- T-056 Seed script + make targets
- T-057 End-to-end smoke runbook

**Phase 1.5 — Template infrastructure** — [`docs/plan-archive/phase-1.5.md`](docs/plan-archive/phase-1.5.md)

- T-058 Upstream remote conventions + TEMPLATE.md
- T-059 Product config files
- T-060 Backend module registry + loader
- T-061 Conditional router registration
- T-062 Conditional Alembic migrations
- T-063 Public config endpoint + frontend module awareness
- T-064 Conditional frontend routes + nav
- T-065 Billing-disabled graceful fallback
- T-066 Module toggle smoke test + updated runbook
- T-067 Shared i18n package + key conventions
- T-068 Backend i18n — locale resolution + retrofit of Phase 1 strings
- T-069 Web i18n — react-i18next + language switcher + retrofit
- T-070 Mobile i18n — expo-localization + retrofit

**Phase 2 — Platform features** — [`docs/plan-archive/phase-2.md`](docs/plan-archive/phase-2.md)

- T-101 Notification + PushToken models + migration
- T-101.5 Notification preferences on User
- T-102 NotificationService + email/in-app/push fan-out
- T-103 SSE in-app notification stream
- T-104 Web notification center
- T-105 Mobile push token registration + handler
- T-106 Email templates package (React Email)
- T-110 StoredFile model + migration
- T-111 StorageBackend protocol + S3 / R2 / MinIO implementations
- T-112 Presign + confirm endpoints (plan-gated)
- T-113 Virus scan job
- T-114 Web file upload component
- T-115 Mobile file picker
- T-120 FeatureFlagService with Redis cache
- T-121 useFeatureFlag hook
- T-122 Admin endpoint to toggle flags
- T-130 Dunning email sequence
- T-131 Trial expiry warning emails
- T-132 UsageRecord model + report_usage job skeleton
- T-133 Invoice history endpoint + UI
- T-140 is_superadmin guard
- T-141 Admin endpoints — list orgs, list users, manual plan override
- T-142 User impersonation
- T-143 Admin web UI
- T-150 Storybook for packages/ui
- T-160 Phase 2 acceptance runbook

**Phase 3 — Hardening** — [`docs/plan-archive/phase-3.md`](docs/plan-archive/phase-3.md)

- T-201 Sentry SDK wired on backend, web, and mobile
- T-202 Prometheus metrics — `/metrics` on internal network only
- T-203 OpenTelemetry auto-instrumentation — FastAPI + SQLAlchemy + ARQ + Redis
- T-204 Grafana dashboards exported as JSON
- T-205 Postgres slow-query observability — `pg_stat_statements`
- T-206 Circuit breakers on all external calls
- T-210 OWASP ZAP baseline scan in CI against an ephemeral deploy
- T-211 Nightly ZAP full active scan against staging
- T-212 OWASP Top 10 test mapping — verified in CI
- T-213 ASVS L2 checklist — `docs/adr/0001-security-baseline.md`
- T-214 Pentest readiness runbook
- T-215 Secure-SDLC & architecture documentation
- T-216 Auth/session UX gaps
- T-217 WebAuthn/FIDO2 second factor
- T-218 Step-up auth & mandatory admin 2FA
- T-219 Data classification & PII handling
- T-225 Build/infra hardening batch
- T-226 Production-ready change-email flow
- T-220 Playwright E2E — signup → verify → invite → upgrade (Chromium + WebKit)
- T-221 axe-core accessibility gate in Playwright
- T-222 Maestro mobile E2E — login, deep link, logout
- T-223 k6 load scripts with asserted budgets
- T-224 Visual regression — Playwright snapshots on dashboard + settings
- T-230 Three more locales — de, es, fr
- T-231 RTL support — add `ar`, verify layout flips
- T-232 Translator workflow — export, import, source-of-truth checks
- T-233 Locale-aware URL routing on web
- T-234 Locale-aware push notifications — verified end-to-end
- T-240 Redis cache layer — invalidation proven by tests
- T-241 TanStack Virtual on member list + notification list
- T-242 Per-route bundle budgets in CI
- T-243 pytest query-count fixture — N+1 detection on every list endpoint
- T-244 PgBouncer in transaction mode (prod)
- T-245 Production-ready encryption key rotation job
- T-246 Read-replica plumbing (optional — scale-triggered)
- T-250 `_template` module — all five files, working example
- T-251 `make new-module NAME=foo` — generation, not just copying
- T-252 ADR + runbook templates
- T-260 Impersonation destructive-action blocklist (server-side)
- T-261 Mobile impersonation via deep-link handoff
- T-262 Support integration hook (SupportContext)
- T-263 ProductSettings singleton — global runtime toggles

**Phase 4 — Growth layer** — [`docs/plan-archive/phase-4.md`](docs/plan-archive/phase-4.md)

- T-290 Three new module toggles: `marketing`, `public_api`, `enterprise_sso`
- T-291 Extend the module matrix to the build, not just the process
- T-300 `apps/marketing` — Astro, sharing `@forge/ui`
- T-301 Pages: landing, pricing, features, blog (MDX), docs (MDX), legal
- T-302 `<Seo>` — typed meta, Open Graph, Twitter card
- T-303 JSON-LD structured data
- T-304 `sitemap.xml` + `robots.txt`
- T-305 `hreflang` per locale, locale in path
- T-306 Lighthouse CI in the pipeline
- T-307 Serving marketing: Caddy, compose, headers
- T-308 Analytics and cookie consent
- T-310 `ApiKey` model — org-scoped, hashed, scoped, expiring
- T-311 API key auth — a second kind of principal
- T-312 Per-key rate limiting
- T-313 API key management UI
- T-314 The public surface itself — scopes and published docs
- T-320 `WebhookEndpoint` + `WebhookDelivery`, HMAC signing
- T-321 Delivery job with retry, backoff, and a dead letter
- T-322 Webhook UI: endpoints, delivery log, replay
- T-330 ADR: SAML vs OIDC priority, and the library
- T-331 OIDC SP via authlib, per-org IdP config
- T-332 SAML 2.0 SP via `python3-saml`
- T-340 ADR: row-level (current) vs schema-per-tenant
- T-341 Schema-per-tenant tooling
- T-350 Phase 4 acceptance runbook

---

# §7 Cross-cutting checklist — run before marking any task done

Copy this into your working context and tick each item:

```
- [ ] ruff check && ruff format --check — clean
- [ ] mypy --strict app/ — clean
- [ ] pnpm lint && pnpm type-check — clean (frontend)
- [ ] Task's listed tests pass
- [ ] pytest --cov thresholds not regressed (85% overall, 100% on auth/billing/tenancy/gdpr)
- [ ] No new org_id-less user-data table
- [ ] No OFFSET pagination introduced
- [ ] No raw SQL without text() + bound params + justification comment
- [ ] No HTTPException raised from a service (use DomainError subclass)
- [ ] No os.environ direct access (use get_settings())
- [ ] No secret/token/password in any log line
- [ ] No model.update(**request_data) (mass-assignment)
- [ ] No sync DB call in any async endpoint
- [ ] No new dependency without a justification comment (and ADR if significant)
- [ ] Migration has working downgrade()
- [ ] Every new FK column has an index in the same migration
- [ ] Import-linter passes (no layer violations)
- [ ] Pre-commit hooks pass
- [ ] No Column(Text/String) field whose name ends in _secret, _key, _token, _encrypted — use EncryptedString (Semgrep rule must pass)
- [ ] Any new model with an EncryptedString column declares RedactedReprMixin and __redacted_fields__
- [ ] FORCE_HTTPS=true confirmed in staging/prod .env — Caddyfile HTTP→HTTPS redirect present
```

---

_End of plan. Start with T-001. Do not skip ahead. The checklist in §7 is the last thing you read before marking a task done._
