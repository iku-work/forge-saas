# ForgeSaaS — Agent Build Plan

> **Source of truth:** `forgesaas-plan.html`. Every task in this file maps directly to a section of that document. When in doubt, re-read the HTML section named in the task header.
>
> **How to use:** Read §0 once per session. It is binding on every task. Pick the lowest-numbered incomplete task whose `needs:` list is fully done. Execute it. Verify it. Move on.

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
- Rate limits: 5/min on login/register/forgot/magic-link; 3/min on password-reset confirm; 100/min per user general; 1000/hr per org.
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

# Phase 1 — Launchable skeleton

> **Phase 1 acceptance criterion:** A new user can sign up → verify email → invite a teammate → upgrade to a paid plan. End to end. With tests. Nothing else defines "done".

---

## A · Foundation

### T-001 Monorepo skeleton

> HTML ref: §"Monorepo structure"
> needs: —

**Goal** Create the empty monorepo with Turborepo, pnpm workspaces, and the exact directory layout from §2.

**Build**

- Root `package.json` declaring `"workspaces": ["apps/*", "packages/*"]`
- `pnpm-workspace.yaml`
- `turbo.json` with pipelines: `gen-client` (outputs `packages/api-client/src/**`), `build` (dependsOn: `["gen-client", "^build"]`), `test`, `lint`, `type-check`
- `.gitignore`: Python (`__pycache__`, `*.pyc`, `.venv`, `*.egg-info`), Node (`node_modules`, `dist`, `.turbo`, `.next`), env (`.env`, `.env.local`, `.env.*.local`), OS (`.DS_Store`, `Thumbs.db`)
- Empty dirs per §2 with `.gitkeep`
- Root `Makefile` with targets: `dev`, `test`, `lint`, `migrate`, `gen-client`, `seed`, `new-module`

**Tests**

- `pnpm install` runs at root without error
- `ls` confirms every directory in §2 exists

**Done when**

- [ ] `pnpm install` clean
- [ ] All directories from §2 exist
- [ ] `turbo.json` pipelines reference `gen-client` as a dependency of `build`

---

### T-002 Backend project + FastAPI app

> HTML ref: §"Architecture layers"
> needs: T-001

**Goal** Python project with pinned deps, a minimal FastAPI app, and tooling config.

**Build**

- `backend/pyproject.toml` with dependency groups:
  - runtime: `fastapi>=0.115`, `uvicorn[standard]>=0.32`, `sqlmodel>=0.0.22`, `sqlalchemy[asyncio]>=2.0`, `asyncpg>=0.30`, `alembic>=1.13`, `pydantic>=2.9`, `pydantic-settings>=2.5`, `argon2-cffi>=23.1`, `cryptography>=42.0`, `python-jose[cryptography]>=3.3`, `authlib>=1.3`, `python-multipart>=0.0.12`, `arq>=0.26`, `apscheduler>=3.10`, `redis>=5.0`, `stripe>=11.0`, `resend>=2.4`, `slowapi>=0.1.9`, `loguru>=0.7`, `sentry-sdk[fastapi]>=2.15`, `opentelemetry-distro>=0.48b0`, `prometheus-fastapi-instrumentator>=7.0`, `boto3>=1.35`, `itsdangerous>=2.2`
  - dev: `ruff>=0.7`, `mypy>=1.13`, `pytest>=8.3`, `pytest-asyncio>=0.24`, `pytest-cov>=5.0`, `httpx>=0.27`, `testcontainers[postgres,redis]>=4.8`, `polyfactory>=2.18`, `schemathesis>=3.36`, `freezegun>=1.5`, `bandit>=1.7`, `import-linter>=2.1`
- `backend/ruff.toml`: line-length 100, target py312, `select = ["ALL"]`, documented `ignore` list
- `backend/mypy.ini`: `strict = true`, `python_version = 3.12`
- `backend/app/__init__.py`, `backend/app/main.py`:
  - `app = FastAPI(title="ForgeSaaS", version="0.1.0")`
  - lifespan context: startup creates async engine + Redis pool; shutdown disposes both
  - `GET /health` → `{"status": "ok", "version": "0.1.0"}`
  - `GET /health/ready` → checks DB connectivity + Redis ping → 200 or 503
  - `GET /health/live` → always 200

**Tests**

- `tests/test_health.py`: GET `/health` returns 200 with correct body; GET `/health/ready` returns 200 when DB+Redis reachable

**Done when**

- [ ] `uvicorn app.main:app` starts without error
- [ ] `mypy --strict app/` clean
- [ ] `ruff check .` clean
- [ ] Health tests pass

---

### T-003 Configuration module

> HTML ref: §"Local dev & config" → "Environment configuration"
> needs: T-002

**Goal** Type-safe settings via `pydantic-settings`. No `os.environ` anywhere.

**Build**

- `backend/app/core/config.py`:

  ```python
  class Settings(BaseSettings):
      # DB
      DATABASE_URL: PostgresDsn
      REDIS_URL: RedisDsn
      # Auth
      JWT_PRIVATE_KEY: str        # RS256 PEM
      JWT_PUBLIC_KEY: str
      ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
      REFRESH_TOKEN_EXPIRE_DAYS: int = 30
      # App
      ENVIRONMENT: Literal["dev", "staging", "prod"] = "dev"
      ALLOWED_ORIGINS: list[str] = []
      LOG_LEVEL: str = "INFO"
      # Integrations
      STRIPE_SECRET_KEY: str
      STRIPE_WEBHOOK_SECRET: str
      RESEND_API_KEY: str
      SENTRY_DSN: str | None = None
      # Storage
      STORAGE_BACKEND: Literal["s3", "r2", "minio"] = "minio"
      AWS_BUCKET_NAME: str = ""
      AWS_REGION: str = "us-east-1"
      AWS_ACCESS_KEY_ID: str = ""
      AWS_SECRET_ACCESS_KEY: str = ""
      AWS_ENDPOINT_URL: str | None = None   # MinIO local
      # Encryption (column-level Fernet)
      ENCRYPTION_KEY: str               # current primary key — generate with Fernet.generate_key()
      ENCRYPTION_KEY_OLD: str = ""      # comma-separated legacy keys for rotation
      # TLS enforcement
      FORCE_HTTPS: bool = False         # must be True in staging + prod
      model_config = SettingsConfigDict(env_file=".env", extra="forbid")

  @lru_cache
  def get_settings() -> Settings: ...
  ```

- Startup validation:
  - if `ENVIRONMENT != "dev"` and `"*"` in `ALLOWED_ORIGINS` → raise `RuntimeError`
  - if `ENVIRONMENT != "dev"` and `FORCE_HTTPS != True` → raise `RuntimeError("FORCE_HTTPS must be true in non-dev environments")`
  - validate `ENCRYPTION_KEY` is a valid Fernet key at startup (attempt `Fernet(key)`) → raise `EnvironmentError` with generation instructions if invalid
- `.env.example` documenting every field with a comment
- Semgrep rule `no-os-environ.yaml`: flags any `os.environ` usage outside `config.py`

**Tests**

- `tests/test_config.py`: missing required field raises `ValidationError`; `ALLOWED_ORIGINS=["*"]` + `ENVIRONMENT="prod"` raises at startup; `FORCE_HTTPS=False` + `ENVIRONMENT="prod"` raises; invalid `ENCRYPTION_KEY` raises `EnvironmentError`

**Done when**

- [ ] `get_settings()` loads from `.env.test` fixture
- [ ] Startup guard test passes
- [ ] `mypy --strict` clean on `config.py`

---

### T-004 Docker Compose — local dev

> HTML ref: §"Local dev & config" → "Local development"
> needs: T-003

**Goal** `make dev` boots the full local stack in under 30 s.

**Build**

- `docker-compose.yml` services (same versions as production):
  - `postgres:16-alpine` — persistent volume, healthcheck (`pg_isready`), port 5432
  - `redis:7-alpine` — persistent volume, healthcheck (`redis-cli ping`), port 6379
  - `minio/minio` — persistent volume, ports 9000 (API) + 9001 (console), command: `server /data --console-address ":9001"`
  - `mailhog/mailhog` — port 1025 (SMTP) + 8025 (web UI)
  - `backend` — `Dockerfile.dev`, mounts `./backend`, command `uvicorn app.main:app --reload --host 0.0.0.0`, depends_on postgres+redis healthy
  - `worker` — same image as backend, command `arq app.workers.main.WorkerSettings`
- All on a named bridge network `forge-net`
- `Makefile dev` target: `docker compose up -d`

**Tests**

- `make dev && sleep 10 && curl -f http://localhost:8000/health` exits 0
- `docker compose down -v` exits 0

**Done when**

- [ ] All services healthy within 30 s
- [ ] `/health/ready` returns 200 from host

---

### T-004.5 Caddy — HTTPS termination (dev + prod)

> HTML ref: §"Stack reference" → "Reverse proxy: Caddy"
> needs: T-004

**Goal** Caddy terminates TLS in front of every service. HTTP is never exposed outside dev without a redirect. Local dev gets self-signed HTTPS via Caddy's automatic cert; staging/prod gets a real cert via ACME.

**Build**

- `Caddyfile` (dev — used by docker-compose):
  ```caddyfile
  localhost {
      reverse_proxy backend:8000
      tls internal          # self-signed, trusted by Caddy's CA
  }
  ```
- `Caddyfile.prod` (staging/prod — used by docker-compose.prod.yml):

  ```caddyfile
  {$DOMAIN} {
      reverse_proxy backend:8000
      # Caddy auto-obtains and renews Let's Encrypt cert — no config needed
      encode gzip zstd
      header {
          Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
      }
  }

  http://{$DOMAIN} {
      redir https://{$DOMAIN}{uri} 301    # hard redirect — no plain HTTP
  }
  ```

- Add `caddy:2-alpine` service to `docker-compose.yml`:
  - Mounts `./Caddyfile`
  - Depends on `backend` healthy
  - Ports: `80:80`, `443:443`, `443:443/udp` (HTTP/3)
  - Named volume for Caddy's cert storage (`caddy_data`)
- Add `caddy:2-alpine` service to `docker-compose.prod.yml` using `Caddyfile.prod`
- Add `DOMAIN` to `Settings` and `.env.example`
- `Makefile dev` target updated to open `https://localhost` instead of `http://localhost:8000`

**Tests**

- `curl -k https://localhost/health` exits 0 (dev, self-signed cert)
- `curl http://localhost/health` returns 301 redirect to `https://` (even in dev — redirect is always on)
- CI: assert `Caddyfile.prod` contains `redir https://` rule (grep check — prevent accidental deletion)

**Done when**

- [ ] `https://localhost/health` returns `{"status": "ok"}` with self-signed cert
- [ ] HTTP → HTTPS redirect test passes
- [ ] Caddyfile.prod redirect grep passes in CI

---

### T-005 DB session + async engine

> HTML ref: §"Architecture layers" → "Base classes"
> needs: T-003

**Goal** Async SQLAlchemy engine and session factory wired to config.

**Build**

- `backend/app/core/db.py`:
  - `engine = create_async_engine(DATABASE_URL, pool_size=5, max_overflow=15, pool_pre_ping=True, echo=ENVIRONMENT=="dev")`
  - `async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)`
  - `async def get_session() -> AsyncGenerator[AsyncSession, None]`: yields session, rolls back on exception, closes in finally
  - `async def dispose_engine()`: called from app lifespan shutdown

**Tests**

- `tests/test_db.py`: fixture session commits a row; exception causes rollback; row absent after rollback

**Done when**

- [ ] `mypy --strict` clean
- [ ] Rollback-on-exception test passes

---

### T-006 Domain exceptions + response envelope

> HTML ref: §"Architecture layers" → "Standard API response envelope" | §"Best practices" → "Error handling"
> needs: T-002

**Goal** Typed exceptions and a single response shape for every endpoint.

**Build**

- `backend/app/core/exceptions.py`:

  ```python
  class DomainError(Exception):
      status_code: int = 400
      code: str = "domain_error"
      def __init__(self, message: str, details: dict | None = None): ...

  class NotFoundError(DomainError):       status_code=404; code="not_found"
  class PermissionDeniedError(DomainError): status_code=403; code="permission_denied"
  class AuthenticationError(DomainError):  status_code=401; code="authentication_failed"
  class ConflictError(DomainError):        status_code=409; code="conflict"
  class PlanLimitError(DomainError):       status_code=402; code="plan_limit_reached"
  class RateLimitError(DomainError):       status_code=429; code="rate_limited"
  ```

- `backend/app/core/envelope.py`:

  ```python
  class Meta(BaseModel):
      request_id: str
      next_cursor: str | None = None
      version: str = "1.0.0"

  class ErrorBody(BaseModel):
      code: str
      message: str
      details: dict | None = None

  class Envelope(BaseModel, Generic[T]):
      data: T | None = None
      error: ErrorBody | None = None
      meta: Meta
  ```

- `backend/app/core/handlers.py`: register on `app`:
  - `DomainError` → envelope with `error` set, `data=None`, correct status code
  - `RequestValidationError` → 422 envelope
  - bare `Exception` → 500 envelope with generic message, Sentry capture, **no traceback in body**

**Tests**

- Endpoint raising `NotFoundError` → 404 `error.code == "not_found"`
- Endpoint raising bare `RuntimeError` → 500, body has no stack trace

**Done when**

- [ ] Both tests pass
- [ ] `mypy --strict` clean on both files

---

### T-007 Middleware — request ID, security headers, CORS

> HTML ref: §"Security" → "Transport & headers" | §"Observability" → "Request ID middleware"
> needs: T-003, T-006

**Goal** Every request has a traced ID; every response has defensive headers; CORS is strict.

**Build**

- `backend/app/middleware/request_id.py` (`BaseHTTPMiddleware`):
  - Read `X-Request-ID` header or generate `str(uuid4())`
  - Store on `request.state.request_id`
  - Set on outgoing response header
  - Bind to Loguru context via `logger.contextualize(request_id=...)`
  - Attach to Sentry scope via `sentry_sdk.set_tag`

- `backend/app/middleware/security_headers.py` (`BaseHTTPMiddleware`):
  - Set headers: `Strict-Transport-Security: max-age=31536000; includeSubDomains`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `Content-Security-Policy: default-src 'self'`

- In `app/main.py` add `CORSMiddleware`:
  - `allow_origins=settings.ALLOWED_ORIGINS` (from config)
  - `allow_credentials=True`
  - `allow_methods=["GET","POST","PUT","PATCH","DELETE"]`
  - startup check: `ENVIRONMENT != "dev"` and `"*"` in `ALLOWED_ORIGINS` → `RuntimeError`

**Tests**

- Every required security header present in a sample response
- `X-Request-ID` echoed when provided; a UUID generated when absent
- Startup raises with wildcard CORS in prod

**Done when**

- [ ] All header tests pass
- [ ] CORS startup guard test passes

---

### T-008 Rate limiting

> HTML ref: §"Security" → "Rate limiting"
> needs: T-003

**Goal** Redis-backed sliding window rate limiter available as a decorator.

**Build**

- `backend/app/middleware/rate_limit.py`:
  - `limiter = Limiter(key_func=_key_func, storage_uri=settings.REDIS_URL)` using `slowapi`
  - `_key_func`: returns `f"user:{user_id}"` if authenticated (pulled from `request.state`), else remote IP
  - Register `SlowAPIMiddleware` and `_rate_limit_exceeded_handler` (returns 429 envelope, not slowapi default)
  - Export named limit decorators: `auth_limit = limiter.limit("5/minute")`, `sensitive_limit = limiter.limit("3/minute")`, `api_limit = limiter.limit("100/minute")`

**Tests**

- Calling a `@auth_limit` endpoint 6 times returns 429 on the 6th
- 429 response uses the standard envelope format

**Done when**

- [ ] Limiter and decorators importable
- [ ] Rate-limit enforcement test passes

---

### T-009 SQLModel base mixins + BaseRepository

> HTML ref: §"Architecture layers" → "Base classes" | §"Core data models"
> needs: T-005, T-006

**Goal** Reusable mixins and the generic org-scoped repository every module extends.

**Build**

- `backend/app/models/_mixins.py`:

  ```python
  def utcnow() -> datetime: return datetime.now(UTC)

  class UUIDPKMixin:
      id: UUID = Field(default_factory=uuid4, primary_key=True)

  class TimestampMixin:
      created_at: datetime = Field(default_factory=utcnow)
      updated_at: datetime = Field(default_factory=utcnow,
                                   sa_column_kwargs={"onupdate": utcnow})

  class SoftDeleteMixin:
      deleted_at: datetime | None = None

  class OrgScopedMixin:
      org_id: UUID = Field(foreign_key="organization.id", index=True)
  ```

- `backend/app/core/repository.py` — `BaseRepository[T]`:

  ```python
  class BaseRepository(Generic[T]):
      model: ClassVar[type[T]]

      def __init__(self, session: AsyncSession, org_id: UUID) -> None:
          self.session = session
          self.org_id = org_id   # required, no default — law §0.2

      async def get(self, id: UUID) -> T | None
          # WHERE id=id AND org_id=self.org_id AND deleted_at IS NULL

      async def get_or_raise(self, id: UUID) -> T
          # raises NotFoundError

      async def list_cursor(
          self, after: UUID | None = None, limit: int = 20
      ) -> tuple[list[T], UUID | None]
          # cursor on (created_at DESC, id DESC), limit capped at 100
          # returns (items, next_cursor | None)

      async def create(self, **kwargs: Any) -> T
          # auto-injects org_id; rejects if caller passes different org_id

      async def update(self, id: UUID, **kwargs: Any) -> T
          # org-scoped, raises NotFoundError

      async def soft_delete(self, id: UUID) -> None
          # sets deleted_at, org-scoped
  ```

**Tests**

- `tests/test_base_repository.py` using a throwaway `TestEntity` model:
  - `get()` for entity in org B by org A → `None`
  - `list_cursor` page 1 + page 2 → no duplicates, correct order
  - `create` auto-injects `org_id`; manual override of wrong `org_id` raises

**Done when**

- [ ] Cross-tenant isolation test passes
- [ ] Cursor pagination test passes
- [ ] 100% coverage on `repository.py`

---

### T-009.5 EncryptedString TypeDecorator + RedactedReprMixin

> HTML ref: §"Security" (column-level encryption, §0.6)
> needs: T-003, T-005

**Goal** A transparent Fernet column type that encrypts on write, decrypts on read, and supports zero-downtime key rotation. A `RedactedReprMixin` prevents encrypted values appearing in logs or Sentry via `__repr__`.

**Build**

- Copy `backend/app/core/encrypted_type.py` from the provided `encryption_type.py` reference implementation verbatim, then make the following adjustments:

  1. **Config integration** — remove `os.environ.get("ENCRYPTION_KEY")` calls. Instead, `_load_fernet()` must call `get_settings()` from `app.core.config`:

     ```python
     from app.core.config import get_settings

     def _load_fernet() -> MultiFernet:
         settings = get_settings()
         if not settings.ENCRYPTION_KEY:
             raise EnvironmentError("ENCRYPTION_KEY is not set ...")
         keys = [Fernet(settings.ENCRYPTION_KEY.encode("ascii"))]
         for raw in filter(None, settings.ENCRYPTION_KEY_OLD.split(",")):
             keys.append(Fernet(raw.strip().encode("ascii")))
         return MultiFernet(keys)
     ```

     This keeps all config access through `pydantic-settings` per §0.6.

  2. **Startup validation** — `_load_fernet()` is called once during the FastAPI lifespan startup (in `app/main.py`) so a bad key fails loudly at boot, not on the first DB write.

  3. **Key rotation job** — add a stub ARQ job `reencrypt_column_job(ctx, *, table: str, column: str, batch_size: int = 500)` in `app/workers/jobs/encryption.py` that:
     - Selects rows where the column value does NOT start with the current key's token prefix (i.e. old-key ciphertext)
     - Re-encrypts each with the primary key and saves
     - Runs in batches; logs progress; designed to be idempotent and restartable

- `RedactedReprMixin` is used on **every model that has an `EncryptedString` column**. Declare `__redacted_fields__` listing all encrypted field names.

- Apply immediately to `TOTPSecret`: change `secret_encrypted: str` column type to `Column(EncryptedString)` and add `RedactedReprMixin`.

- Semgrep rule `no-plaintext-secret-column.yaml`: flags any `Column(Text)` or `Column(String)` whose field name ends in `_secret`, `_key`, `_token`, or `_encrypted` — forces use of `EncryptedString`.

**Tests** (100% coverage on `encrypted_type.py`)

- Round-trip: `EncryptedString.process_bind_param("hello", ...)` → ciphertext starts with `"gAAAAA"`; `process_result_value(ciphertext, ...)` → `"hello"`
- `None` in → `None` out (both directions)
- Tampered ciphertext: flip a byte in the token → `process_result_value` raises `ValueError` (not silently returns garbage)
- Key rotation: encrypt with key A; switch to key B + key A as `ENCRYPTION_KEY_OLD`; decrypt succeeds; re-encrypt via job → new ciphertext decrypts with key B alone
- Legacy plaintext passthrough: a value without the Fernet prefix (`"gAAAAA"`) passes through unchanged (migration path for existing rows)
- `RedactedReprMixin.__repr__` contains `"[REDACTED]"` for declared fields and the real value for non-declared fields
- Semgrep rule flags a `Column(Text)` field named `api_secret`

**Done when**

- [ ] All encryption tests pass, 100% coverage
- [ ] `TOTPSecret` model uses `EncryptedString` + `RedactedReprMixin`
- [ ] `_load_fernet()` uses `get_settings()`, not `os.environ`
- [ ] Startup validation calls `_load_fernet()` in lifespan
- [ ] Semgrep rule flags forbidden column pattern
- [ ] `mypy --strict` clean

---

### T-010 Alembic setup

> HTML ref: §"Best practices" → "Database best practices"
> needs: T-009

**Goal** Async-aware Alembic configured for SQLModel autogenerate.

**Build**

- `backend/alembic.ini` — `script_location = alembic`, `sqlalchemy.url = (loaded from env)`
- `backend/alembic/env.py`:
  - Import all models via `app.models.__init__` so autogenerate sees every table
  - Use `DATABASE_URL` from `get_settings()`, not `alembic.ini`
  - Async migration runner (`run_async_migrations`)
- `backend/alembic/script.py.mako` — template that enforces non-empty `downgrade()` body (adds a comment block to remind)
- `Makefile migrate` target: `alembic upgrade head`

**Tests**

- `alembic upgrade head && alembic downgrade base && alembic upgrade head` exits 0 against the test DB

**Done when**

- [ ] Round-trip migration test passes
- [ ] Autogenerate produces valid SQL for all mixin columns

---

### T-011 Structured logging

> HTML ref: §"Observability" → "Structured logs" | §"Best practices" → "Observability discipline"
> needs: T-003, T-007

**Goal** JSON-structured logs with PII scrubbing; sensitive keys never appear in output.

**Build**

- `backend/app/core/logging.py`:
  - Configure Loguru with JSON serialization in non-dev envs; pretty in dev
  - Every record carries: `timestamp`, `level`, `message`, `request_id` (from contextvars), `user_id` (when bound), `org_id` (when bound)
  - Scrubber: post-processor that replaces values where key contains `password`, `token`, `secret`, `api_key`, `authorization`, `cookie` with `"***"`
  - Log levels follow §0.6: `ERROR` pages someone, `WARN` reviewed, `INFO` audit trail, `DEBUG` dev-only

**Tests**

- `tests/test_logging.py`: logging a dict with key `"password"` → output JSON contains `"password": "***"`
- `"token"` key scrubbed; `"user_id"` key not scrubbed

**Done when**

- [ ] Scrubber tests pass
- [ ] `mypy --strict` clean

---

### T-012 Pre-commit hooks + import-linter

> HTML ref: §"Best practices" → "Code quality & consistency" | §"Architecture discipline"
> needs: T-002

**Goal** Local enforcement of all §0.7 style rules + §0.1 layer discipline.

**Build**

- `.pre-commit-config.yaml`: `ruff check --fix`, `ruff format`, `mypy --strict`, `prettier --write`, `detect-secrets`, `check-yaml`, `end-of-file-fixer`, `trailing-whitespace`
- `.importlinter` config:

  ```ini
  [importlinter]
  root_package = app

  [importlinter:contract:layers]
  name = Four-layer rule
  type = layers
  layers =
      app.modules.*.router
      app.modules.*.service
      app.modules.*.repository
      app.models
  ```

- `Makefile install-hooks`: `pre-commit install`
- `backend/semgrep/no-os-environ.yaml`: flags `os.environ` outside `core/config.py`
- `backend/semgrep/no-mass-assign.yaml`: flags `model.update(**` pattern

**Tests**

- `pre-commit run --all-files` exits 0 on current clean state
- `import-linter` check: a mock file that imports a router from a service → fails

**Done when**

- [ ] `pre-commit run --all-files` clean
- [ ] `lint-imports` catches the layer violation in the test

---

## B · Auth module

> HTML ref: §"Auth & sessions" (entire section)

### T-013 Auth models + migration

> needs: T-009.5, T-010

**Goal** All auth-related tables in one migration.

**Build**
All models use `UUIDPKMixin + TimestampMixin + SoftDeleteMixin` unless noted.

- `backend/app/modules/auth/models.py`:
  - `User`: `email: str (unique, index)`, `email_verified_at: datetime | None`, `hashed_password: str | None` (null for OAuth users), `full_name: str | None`, `avatar_url: str | None`, `is_active: bool = True`, `is_superadmin: bool = False`, `totp_enabled: bool = False`, `locale: str = "en"`, `failed_login_count: int = 0`, `locked_until: datetime | None`, `notification_preferences: dict = Field(sa_column=Column(JSONB, default=dict))`
  - `Session` (no SoftDelete): `user_id: UUID (FK, index)`, `refresh_token_hash: str (unique)`, `device_label: str | None`, `ip_address: str`, `user_agent: str`, `last_seen_at: datetime`, `expires_at: datetime`, `revoked_at: datetime | None`
  - `OAuthAccount` (no SoftDelete): `user_id (FK, index)`, `provider: str`, `provider_user_id: str`, unique constraint `(provider, provider_user_id)`
  - `TOTPSecret` (PK = user_id, uses `RedactedReprMixin`): `secret_encrypted: str = Field(sa_column=Column(EncryptedString))` — **never plaintext**, `enabled_at: datetime | None`, `backup_codes_hash: str` (JSON list of argon2-hashed codes)
  - `EmailVerificationToken`: `user_id (FK, index)`, `token_hash: str (unique)`, `expires_at: datetime`, `used_at: datetime | None`
  - `PasswordResetToken`: same shape as EmailVerificationToken
  - `MagicLinkToken`: same shape + `redirect_to: str | None`
  - **Tokens are never stored plaintext — only SHA-256 hash**

- Generate migration: `alembic revision --autogenerate -m "auth_tables"`

**Tests**

- Unique constraint on `(provider, provider_user_id)` enforced
- Migration round-trip: up → down → up

**Done when**

- [ ] Migration applies cleanly in both directions
- [ ] `mypy --strict` clean on `models.py`

---

### T-014 Auth cryptography utilities

> needs: T-003, T-006

**Goal** Every cryptographic primitive in one tested module. Nothing else does crypto.

**Build**

- `backend/app/modules/auth/utils.py`:
  - `hash_password(plain: str) -> str` — argon2 `PasswordHasher().hash(plain)`
  - `verify_password(plain: str, hashed: str) -> bool` — returns False on `VerifyMismatchError`; only raises on corrupt format
  - `create_access_token(user_id, org_id, permissions) -> str` — RS256 JWT, claims: `sub`, `org`, `perms`, `iat`, `exp`, `jti` (uuid4)
  - `verify_access_token(token) -> AccessTokenPayload` — raises `AuthenticationError` on invalid/expired/wrong-algorithm
  - `generate_refresh_token() -> tuple[str, str]` — `(plain_32_bytes_urlsafe, sha256_hex(plain))`
  - `hash_token(plain: str) -> str` — SHA-256 hex, for single-use tokens
  - `sign_url_token(payload: dict, ttl: int) -> str` — `itsdangerous.URLSafeTimedSerializer`
  - `verify_url_token(token: str, max_age: int) -> dict` — raises `AuthenticationError` on bad/expired

**Tests** (100% coverage required)

- Round-trip JWT: encode → decode → payload matches
- Expired JWT raises `AuthenticationError`
- Wrong algorithm in header raises `AuthenticationError`
- `verify_password` returns False on mismatch, doesn't raise
- Refresh token: plain ≠ hash; `hash_token(plain) == stored_hash`
- URL token: round-trip; expired raises

**Done when**

- [ ] All utils tests pass, 100% coverage

---

### T-015 Auth schemas

> needs: T-013

**Goal** Request/response schemas — never the model class itself (§0.4 mass-assignment rule).

**Build**

- `backend/app/modules/auth/schemas.py`:
  - `RegisterRequest`: `email: EmailStr`, `password: str (min_length=12, validator: upper+lower+digit)`; reject top-1000 common passwords (embedded list)
  - `LoginRequest`: `email: EmailStr`, `password: str`
  - `UserPublic`: `id, email, full_name, avatar_url, locale, email_verified_at, totp_enabled` — **must not include `hashed_password`, `failed_login_count`, `locked_until`**
  - `LoginResponse`: `access_token: str`, `user: UserPublic` (refresh token set via cookie — not in body)
  - `RefreshResponse`: `access_token: str`
  - `PasswordResetRequest`: `email: EmailStr`
  - `PasswordResetConfirmRequest`: `token: str`, `new_password: str (same validators as register)`
  - `MagicLinkRequest`: `email: EmailStr`
  - `VerifyEmailRequest`: `token: str`
  - `Enable2FAResponse`: `provisioning_uri: str`, `secret: str` (shown once)
  - `Verify2FARequest`: `code: str (length=6, digits only)`

**Tests**

- `UserPublic.model_fields` does not contain `hashed_password`
- Password too short → `ValidationError`
- Common password `"Password123!"` → `ValidationError`

**Done when**

- [ ] All schema tests pass

---

### T-016 Auth repository

> needs: T-009, T-013

**Goal** DB access for User, Session, and token tables. Not org-scoped (user-scoped).

**Build**

- `backend/app/modules/auth/repository.py`:
  - `UserRepository(session)` (no org_id — users are global):
    - `get_by_id(id) -> User | None`
    - `get_by_email(email) -> User | None`
    - `create(**kwargs) -> User`
    - `update(id, **kwargs) -> User`
    - `increment_failed_login(id) -> None`
    - `reset_failed_login(id) -> None`
    - `set_lockout(id, until: datetime) -> None`
    - `mark_email_verified(id) -> None`
  - `SessionRepository(session)`:
    - `create(user_id, token_hash, device_label, ip, user_agent, expires_at) -> Session`
    - `get_by_token_hash(hash) -> Session | None`
    - `revoke(id) -> None` — sets `revoked_at`
    - `revoke_all_for_user(user_id) -> None`
    - `list_active_for_user(user_id) -> list[Session]`
    - `prune_expired() -> int` — deletes sessions past `expires_at`
  - Token repositories (`EmailVerificationTokenRepository`, `PasswordResetTokenRepository`, `MagicLinkTokenRepository`): `create`, `get_by_hash` (excludes used/expired), `mark_used`

**Tests** (100% coverage)

- `get_by_email` for unknown email → `None`
- `increment_failed_login` increments correctly
- `revoke_all_for_user` revokes all active sessions
- Token: `get_by_hash` after `mark_used` → `None`

**Done when**

- [ ] All repository tests pass, 100% coverage

---

### T-017 Auth service — register + verify email

> needs: T-014, T-015, T-016, T-011

**Goal** User registration and email verification, with no email enumeration.

**Build**

- `backend/app/modules/auth/service.py`:
  - `AuthService.register(req: RegisterRequest) -> None`:
    - Look up email — if found: **do not raise** (prevents enumeration); instead log a warning and return the same outcome. Caller always responds "check your inbox."
    - Hash password
    - Create user with `is_active=False, email_verified_at=None`
    - Create `EmailVerificationToken` (24 h TTL), store hash
    - Log to structured logger (no email or password in log body)
    - Emit `AuditEvent` via stub (wired properly in T-026)
    - Enqueue email job (wired in T-029)
  - `AuthService.verify_email(token: str) -> User`:
    - Hash token, look up `EmailVerificationToken`
    - Raise `AuthenticationError` if not found, expired, or already used
    - Mark token used; set `email_verified_at`, `is_active=True`

**Tests**

- Register happy path: user created, `is_active=False`
- Duplicate email: no exception raised; same return shape
- Verify with expired token → `AuthenticationError`
- Verify same token twice → `AuthenticationError` on second call

**Done when**

- [ ] All tests pass, 100% coverage on these methods

---

### T-018 Auth service — login + lockout

> needs: T-017

**Goal** Login with constant-time protection and brute-force lockout.

**Build**

- `AuthService.login(req: LoginRequest, ip: str, user_agent: str) -> tuple[str, str, User]`:
  - Look up user by email
  - **Always hash a dummy password** if user not found (prevents timing oracle)
  - If `locked_until` in future → `AuthenticationError("account_locked")`
  - If `email_verified_at is None` → `AuthenticationError("email_not_verified")`
  - On mismatch: `increment_failed_login`; at 10 failures → `set_lockout(utcnow() + 15 min)`, reset count
  - On success: `reset_failed_login`, create `Session` with hashed refresh token (30d expiry)
  - Return `(access_token, refresh_token_plain, user)`

**Tests**

- Happy path: tokens returned, session created
- Wrong password 10×: 10th attempt triggers lockout
- Locked account: correct password still rejected
- Unverified email: login rejected

**Done when**

- [ ] All lockout tests pass, 100% coverage

---

### T-019 Auth service — token refresh + theft detection

> needs: T-018

**Goal** Refresh token rotation; reuse of a revoked token revokes all sessions.

**Build**

- `AuthService.refresh(refresh_token_plain: str, ip: str, user_agent: str) -> tuple[str, str]`:
  - Hash the plain token; look up `Session`
  - If not found, `revoked_at` set, or `expires_at` past → **revoke all sessions for this user** (theft detected) → raise `AuthenticationError("session_invalid")`
  - Revoke current session; create new session with new refresh token
  - Return `(new_access_token, new_refresh_token_plain)`

**Tests**

- Happy path: old session revoked, new session created
- Reuse of revoked token: all user sessions revoked → `AuthenticationError`
- Expired refresh: `AuthenticationError`

**Done when**

- [ ] Theft-detection test passes, 100% coverage

---

### T-020 Auth service — password reset + magic link

> needs: T-019

**Goal** Password reset and magic link flows; both protected against email enumeration.

**Build**

- `AuthService.request_password_reset(email: str) -> None`:
  - Look up user. If not found: return silently.
  - Create `PasswordResetToken` (1 h TTL). Enqueue reset email.
- `AuthService.confirm_password_reset(token: str, new_password: str) -> None`:
  - Look up by hash. Reject if used/expired.
  - Mark used. Update `hashed_password`. `revoke_all_for_user`.
- `AuthService.request_magic_link(email: str) -> None`:
  - Same enumeration protection.
  - Create `MagicLinkToken` (15 min TTL). Enqueue email.
- `AuthService.consume_magic_link(token: str, ip: str, ua: str) -> tuple[str, str, User]`:
  - Look up by hash. Reject if used/expired.
  - Mark used. Create session → return tokens same as login.

**Tests**

- Enumeration: reset request for unknown email returns same shape as known email
- Single-use: consuming twice raises on second call
- Reset revokes all sessions

**Done when**

- [ ] All tests pass, 100% coverage

---

### T-021 RBAC — permissions + dependency

> HTML ref: §"Auth & sessions" → "RBAC model"
> needs: T-016, T-006

**Goal** Permission enum + `require_permission` FastAPI dependency.

**Build**

- `backend/app/modules/auth/permissions.py`:

  ```python
  class Permission(str, Enum):
      MEMBERS_READ    = "members:read"
      MEMBERS_MANAGE  = "members:manage"
      BILLING_READ    = "billing:read"
      BILLING_MANAGE  = "billing:manage"
      FILES_READ      = "files:read"
      FILES_MANAGE    = "files:manage"
      AUDIT_READ      = "audit:read"
      SETTINGS_MANAGE = "settings:manage"

  class MemberRole(str, Enum):
      OWNER  = "owner"
      ADMIN  = "admin"
      MEMBER = "member"

  ROLE_PERMISSIONS: dict[MemberRole, frozenset[Permission]] = {
      MemberRole.OWNER:  frozenset(Permission),
      MemberRole.ADMIN:  frozenset({MEMBERS_READ, MEMBERS_MANAGE,
                                    BILLING_READ, FILES_READ, FILES_MANAGE,
                                    AUDIT_READ, SETTINGS_MANAGE}),
      MemberRole.MEMBER: frozenset({MEMBERS_READ, FILES_READ}),
  }
  ```

- `backend/app/core/dependencies.py`:
  - `get_current_user(token: str = Depends(oauth2_scheme), session = Depends(get_session)) -> User`: verify JWT, load user with 60 s Redis cache, reject if `is_active=False` or `deleted_at` set
  - `get_current_org(user = Depends(get_current_user), x_org_id: UUID = Header(...)) -> tuple[Organization, Membership]`: verify membership, raise `PermissionDeniedError` on mismatch
  - `require_permission(p: Permission) -> Callable`: returns dependency that checks `p in ROLE_PERMISSIONS[membership.role]`

**Tests**

- MEMBER calling a BILLING_MANAGE-gated endpoint → 403
- OWNER calling same → 200
- Missing `X-Org-ID` header → 400

**Done when**

- [ ] RBAC matrix test covers every role × every permission
- [ ] 100% coverage on `permissions.py` and the three dependencies

---

### T-022 Auth router

> HTML ref: §"Auth & sessions" → "Auth flows supported"
> needs: T-017, T-018, T-019, T-020, T-021, T-007, T-008

**Goal** HTTP layer for all auth flows; rate-limited; cookies set correctly.

**Build**

- `backend/app/modules/auth/router.py` — all under `/api/v1/auth/`:

  | Method | Path                  | Rate limit        | Notes                          |
  | ------ | --------------------- | ----------------- | ------------------------------ |
  | POST   | `/register`           | `auth_limit`      | sets no cookie                 |
  | POST   | `/login`              | `auth_limit`      | sets `refresh_token` cookie    |
  | POST   | `/refresh`            | `api_limit`       | reads + rotates cookie         |
  | POST   | `/logout`             | `api_limit`       | revokes session, clears cookie |
  | POST   | `/logout-all`         | `api_limit`       | revokes all sessions           |
  | POST   | `/verify-email`       | `auth_limit`      |                                |
  | POST   | `/forgot-password`    | `sensitive_limit` |                                |
  | POST   | `/reset-password`     | `sensitive_limit` |                                |
  | POST   | `/magic-link`         | `sensitive_limit` |                                |
  | POST   | `/magic-link/consume` | `auth_limit`      |                                |
  | GET    | `/me`                 | `api_limit`       |                                |
  | GET    | `/sessions`           | `api_limit`       |                                |
  | DELETE | `/sessions/{id}`      | `api_limit`       |                                |

- Cookie on `/login`: `httponly=True, secure=True, samesite="strict", path="/api/v1/auth"`
- Register router in `backend/app/api/v1/__init__.py`

**Tests**

- `/login` response sets cookie with correct attributes (`httponly`, `secure`, `samesite=strict`)
- 6th call to `/login` from same IP returns 429
- `/refresh` with no cookie → 401
- `/logout` clears cookie

**Done when**

- [ ] All endpoint integration tests pass
- [ ] Cookie attribute test passes

---

### T-023 OAuth — Google + GitHub

> HTML ref: §"Auth & sessions" → "Social OAuth"
> needs: T-022

**Goal** Social sign-in via `authlib`.

**Build**

- `backend/app/modules/auth/oauth.py`:
  - `GET /api/v1/auth/oauth/{provider}`: generate state (signed, 10 min), store in a short-lived httpOnly cookie, redirect to provider
  - `GET /api/v1/auth/oauth/{provider}/callback`: verify state cookie; exchange code; fetch profile
  - Link to existing account by verified email; or create new user (`email_verified_at = now()`, `hashed_password = None`)
  - Newly OAuth-created users cannot use password login until they add a password via settings
  - Provider config in settings: `GOOGLE_CLIENT_ID/SECRET`, `GITHUB_CLIENT_ID/SECRET`

**Tests**

- Mock provider responses; state mismatch → 400
- Existing email links account; new email creates user
- OAuth user has `hashed_password=None`

**Done when**

- [ ] Tests pass with mocked provider

---

### T-024 TOTP 2FA

> HTML ref: §"Auth & sessions" → "TOTP 2FA"
> needs: T-022

**Goal** Optional time-based 2FA with backup codes.

**Build**

- `backend/app/modules/auth/totp.py`:
  - `POST /api/v1/auth/2fa/enable`: generate TOTP secret (encrypt with `TOTP_ENCRYPTION_KEY` from settings before storing), return `{ provisioning_uri }` — not yet active
  - `POST /api/v1/auth/2fa/verify`: verify 6-digit code using `pyotp`; if valid, set `enabled_at`; generate 10 backup codes (store argon2-hashed); return backup codes **once**
  - `POST /api/v1/auth/2fa/disable`: requires current password + valid TOTP
  - Modified login flow: if `totp_enabled`, `/login` returns `{ pending_2fa: true, temp_token }` (short-lived signed token, not the full access token); `POST /api/v1/auth/2fa/login` takes `{ temp_token, code }` → returns full access + refresh

**Tests**

- 2FA round-trip: enable → verify code → disable requires re-auth
- Backup code single-use: using same backup code twice → second fails
- Invalid code → `AuthenticationError`

**Done when**

- [ ] All 2FA tests pass

---

## C · Tenancy module

> HTML ref: §"Multi-tenancy" (entire section)

### T-025 Tenancy models + migration

> needs: T-009, T-010

**Goal** Organization, Membership, Invitation, OrgFeatureFlag tables.

**Build**

- `backend/app/modules/tenancy/models.py`:
  - `Organization` (UUID + Timestamps + SoftDelete): `name: str`, `slug: str (unique, index)`, `logo_url: str | None`, `settings: dict (JSONB, default={})`, `plan_id: UUID | None (FK plan.id, index)`, `stripe_customer_id: str | None`
  - `Membership` (UUID + Timestamps — no SoftDelete): `user_id (FK, index)`, `org_id (FK, index)`, `role: MemberRole`, `invited_by: UUID | None`; unique constraint `(user_id, org_id)`
  - `Invitation` (UUID + Timestamps): `org_id (FK, index)`, `email: str (index)`, `role: MemberRole`, `token_hash: str (unique)`, `invited_by: UUID (FK)`, `expires_at: datetime`, `accepted_at: datetime | None`
  - `OrgFeatureFlag` (UUID + Timestamps): `org_id (FK, index)`, `flag_name: str`, `enabled: bool = True`; unique constraint `(org_id, flag_name)`

- Migration: `alembic revision --autogenerate -m "tenancy_tables"`

**Tests**

- Migration round-trip
- Unique constraint `(user_id, org_id)` on Membership enforced

**Done when**

- [ ] Migration applies cleanly both directions

---

### T-026 Tenancy repository + service

> needs: T-025, T-009, T-006

**Goal** Org creation, membership management, invitation flow — including "cannot demote last owner".

**Build**

- `backend/app/modules/tenancy/repository.py`:
  - `OrganizationRepository(session, org_id)` — extends BaseRepository; add `get_by_slug(slug) -> Org | None` (not org-scoped — justified by comment: public lookup)
  - `MembershipRepository(session, org_id)`:
    - `list_for_org(cursor, limit) -> (list[Membership], cursor)`
    - `get_by_user_and_org(user_id, org_id) -> Membership | None`
    - `set_role(membership_id, role) -> Membership`
    - `remove(membership_id) -> None`
  - `InvitationRepository(session, org_id)`: standard CRUD; `get_by_token_hash(hash) -> Invitation | None`

- `backend/app/modules/tenancy/service.py`:
  - `TenancyService.create_org(user, name: str) -> Organization`:
    - Create org + OWNER membership in one transaction
    - Slug auto-generated from name with collision handling
  - `TenancyService.invite(org_id, inviter, email, role) -> None`:
    - Create `Invitation` (72 h TTL), sign token with `itsdangerous`, store hash
    - **No user lookup** — invitation email may be for someone without an account
    - Enqueue invitation email
  - `TenancyService.accept_invitation(token: str, user: User) -> Membership`:
    - Verify signature + TTL
    - Check not already accepted
    - Create `Membership`
    - Mark `Invitation.accepted_at`
  - `TenancyService.change_role(actor_membership, target_membership_id, new_role) -> Membership`:
    - Actor must have MEMBERS_MANAGE permission
    - Reject if target is the only OWNER and new_role ≠ OWNER

**Tests**

- Invite + accept round-trip
- Double-accept rejected
- Expired invitation rejected
- Demote last owner raises `ConflictError`
- Slug collision: second org with same name gets `-2` suffix

**Done when**

- [ ] All tenancy service tests pass, 100% coverage

---

### T-027 Tenancy router

> needs: T-026, T-021

**Goal** HTTP endpoints for orgs and memberships; cross-tenant access impossible.

**Build**

- `backend/app/modules/tenancy/router.py` — under `/api/v1/`:

  | Method | Path                          | Permission required               |
  | ------ | ----------------------------- | --------------------------------- |
  | POST   | `/orgs`                       | authenticated (any)               |
  | GET    | `/orgs`                       | authenticated (lists user's orgs) |
  | GET    | `/orgs/{id}`                  | membership                        |
  | PATCH  | `/orgs/{id}`                  | SETTINGS_MANAGE                   |
  | GET    | `/orgs/{id}/members`          | MEMBERS_READ                      |
  | PATCH  | `/orgs/{id}/members/{mid}`    | MEMBERS_MANAGE                    |
  | DELETE | `/orgs/{id}/members/{mid}`    | MEMBERS_MANAGE                    |
  | POST   | `/orgs/{id}/invitations`      | MEMBERS_MANAGE                    |
  | GET    | `/invitations/{token}`        | public (preview)                  |
  | POST   | `/invitations/{token}/accept` | authenticated                     |

**Tests**

- User A (org A) attempts GET `/orgs/{org_b_id}/members` → 403
- MEMBER attempts PATCH on member role → 403
- Full invitation round-trip via HTTP

**Done when**

- [ ] Cross-tenant 403 test passes
- [ ] All endpoint tests pass

---

## D · Audit log

> HTML ref: §"Core data models" — `AuditEvent` | §"Best practices" → "Immutability where it matters"

### T-028 Audit model + service + migration

> needs: T-025, T-005, T-010

**Goal** Append-only audit log written on every mutating action.

**Build**

- `backend/app/modules/audit/models.py`:

  ```python
  class AuditEvent(SQLModel, table=True):
      id: UUID = Field(default_factory=uuid4, primary_key=True)
      ts: datetime = Field(default_factory=utcnow, index=True)
      org_id: UUID = Field(index=True)        # not FK — preserved after org deletion
      actor_id: UUID | None = None             # null for system events
      action: str                              # "user.registered", "plan.upgraded"
      resource_type: str                       # "user", "subscription"
      resource_id: str | None = None
      diff: dict | None = Field(sa_column=Column(JSONB))
      ip_address: str | None = None
      user_agent: str | None = None
  ```
  - **No `updated_at`. No `deleted_at`. No `update` or `delete` method on its repository.**

- `backend/app/modules/audit/repository.py`: `create`, `list_cursor` (org-scoped, read-only)
- `backend/app/modules/audit/service.py`:
  - `AuditService.log(action, resource_type, resource_id, diff, org_id, actor_id, ip, ua) -> None`
  - Writes in same transaction as the triggering action
  - On failure: logs a `WARN` + Sentry event — **never raises** (audit failure must not roll back a business transaction)

- Migration: `alembic revision --autogenerate -m "audit_events"`

- Wire `AuditService.log(...)` calls into auth and tenancy services, replacing stubs from T-017/T-026.

**Tests**

- `AuditRepository` has no `update` or `soft_delete` method (assert API surface)
- Login action produces an `AuditEvent` with `action="user.login_success"`
- Role change produces `AuditEvent` with `diff` containing old and new role

**Done when**

- [ ] Immutability test passes (no update/delete methods)
- [ ] Audit row written for login and role-change actions

---

## E · Billing module

> HTML ref: §"Billing" (entire section)

### T-029 Billing models + migration

> needs: T-025, T-010

**Goal** Plan, Subscription, WebhookEvent tables — everything billing needs.

**Build**

- `backend/app/modules/billing/models.py`:

  ```python
  class Plan(SQLModel, table=True):      # UUIDPKMixin + TimestampMixin + SoftDeleteMixin
      name: str
      slug: str = Field(unique=True, index=True)
      stripe_price_id_monthly: str
      stripe_price_id_yearly: str
      price_monthly: int          # cents
      price_yearly: int           # cents
      features: dict = Field(sa_column=Column(JSONB))
      # features: { "seats": 5, "storage_gb": 10, "api_calls": 10000 }
      is_active: bool = True

  class Subscription(SQLModel, table=True):  # UUIDPKMixin + TimestampMixin
      org_id: UUID = Field(unique=True, index=True)  # one per org
      plan_id: UUID = Field(foreign_key="plan.id", index=True)
      stripe_subscription_id: str = Field(unique=True)
      status: str           # "trialing" | "active" | "past_due" | "canceled" | "incomplete"
      current_period_start: datetime
      current_period_end: datetime
      cancel_at_period_end: bool = False
      trial_end: datetime | None = None

  class WebhookEvent(SQLModel, table=True):  # primary key = Stripe event ID
      id: str = Field(primary_key=True)      # evt_...
      type: str = Field(index=True)
      payload: dict = Field(sa_column=Column(JSONB))
      received_at: datetime = Field(default_factory=utcnow)
      processed_at: datetime | None = None
      error: str | None = None
      attempt_count: int = 0
  ```

- Migration: `alembic revision --autogenerate -m "billing_tables"`

**Tests**

- Migration round-trip
- `org_id` unique constraint on `Subscription`

**Done when**

- [ ] Migration applies cleanly both directions

---

### T-030 Stripe webhook handler

> HTML ref: §"Billing" → "Webhook handler pattern"
> needs: T-029, T-028, T-003

**Goal** Idempotent Stripe webhook — stores every event, processes exactly once.

**Build**

- `backend/app/modules/billing/webhooks.py`:
  - `POST /api/v1/billing/webhooks/stripe` (no auth — Stripe signature validates):
    1. `stripe.Webhook.construct_event(body, sig, STRIPE_WEBHOOK_SECRET)` — bad signature → 400, log warning
    2. Look up `WebhookEvent` by `id`. If `processed_at` set → return 200 immediately (idempotent)
    3. Insert `WebhookEvent(id, type, payload, received_at)` — commit
    4. Dispatch to `BillingService.handle_event(event)`:
       - `customer.subscription.created/updated` → upsert `Subscription`
       - `customer.subscription.deleted` → set `status="canceled"`
       - `invoice.payment_succeeded` → set `status="active"`
       - `invoice.payment_failed` → set `status="past_due"`; emit audit event
    5. On handler exception: store `error`, increment `attempt_count`, return 500 (Stripe retries)
    6. On success: set `processed_at`, commit

**Tests**

- Same event sent twice → processed exactly once (`attempt_count` never exceeds 1 on success)
- Bad signature → 400, `WebhookEvent` not created
- Handler exception → `error` set, `processed_at` null, returns 500

**Done when**

- [ ] Idempotency test passes, 100% coverage on this file

---

### T-031 Billing service + checkout endpoints

> HTML ref: §"Billing" → "Plan-gating dependency" | "What's included"
> needs: T-030, T-021

**Goal** Checkout + portal redirects; `require_feature` dependency.

**Build**

- `backend/app/modules/billing/service.py`:
  - `BillingService.get_or_create_stripe_customer(org) -> str` (returns `stripe_customer_id`)
  - `BillingService.create_checkout_session(org, plan_slug, interval) -> str` (URL)
  - `BillingService.create_portal_session(org) -> str` (URL)
  - `BillingService.handle_event(event) -> None` — dispatches webhook events

- `backend/app/modules/billing/dependencies.py`:

  ```python
  def require_feature(feature: str, minimum: int | None = None):
      async def checker(org_sub = Depends(get_current_org_subscription)):
          val = org_sub.plan.features.get(feature)
          if not val:
              raise PlanLimitError(f"Feature '{feature}' not available on your plan")
          if minimum and val < minimum:
              raise PlanLimitError(f"Plan limit reached for '{feature}'")
      return checker
  ```
  - Grace period: `PAST_DUE` within 7 days of `current_period_end` → still allowed

- `backend/app/modules/billing/router.py`:
  - `POST /api/v1/billing/checkout-session` (requires `BILLING_MANAGE`)
  - `POST /api/v1/billing/portal-session` (requires `BILLING_MANAGE`)
  - `GET /api/v1/billing/subscription` (requires `BILLING_READ`)
  - `GET /api/v1/billing/plans` (public)

**Tests**

- `require_feature` with free plan denied pro feature → 402
- Trial subscription → feature allowed
- PAST_DUE within grace → allowed; beyond → denied
- Mock Stripe: checkout returns URL with correct metadata `{ org_id }`

**Done when**

- [ ] Plan-gating tests pass, 100% coverage

---

## F · GDPR module

> HTML ref: §"GDPR & compliance" (entire section)

### T-032 GDPR models + migration

> needs: T-013, T-010

**Goal** ConsentRecord (append-only) and DataExportJob tables.

**Build**

- `backend/app/modules/gdpr/models.py`:

  ```python
  class ConsentRecord(SQLModel, table=True):   # no timestamps mixin — uses ts field
      id: UUID = Field(default_factory=uuid4, primary_key=True)
      user_id: UUID = Field(foreign_key="user.id", index=True)
      consent_type: str       # "marketing" | "analytics" | "cookies"
      granted: bool
      version: str            # policy version accepted
      ip_address: str
      user_agent: str
      ts: datetime = Field(default_factory=utcnow)
      # append-only: no update, no delete

  class DataExportJob(SQLModel, table=True):   # UUIDPKMixin + TimestampMixin
      user_id: UUID = Field(foreign_key="user.id", index=True)
      status: str = "pending"   # "pending" | "ready" | "failed" | "expired"
      download_url: str | None = None
      expires_at: datetime | None = None
  ```

- Migration: `alembic revision --autogenerate -m "gdpr_tables"`

**Tests**

- Migration round-trip
- `ConsentRecord` repository has no `update` or `delete` method

**Done when**

- [ ] Migration clean; immutability assertion passes

---

### T-033 GDPR service + endpoints

> HTML ref: §"GDPR & compliance" → "Endpoints" + "Data purge job"
> needs: T-032, T-021, T-028

**Goal** Data export, delete-me, and consent endpoints.

**Build**

- `backend/app/modules/gdpr/service.py`:
  - `GDPRService.request_export(user_id) -> DataExportJob`: create job, enqueue `export_user_data` ARQ job
  - `GDPRService.delete_me(user) -> None`: soft-delete user; enqueue `purge_user_data` ARQ job with 30-day delay (env-configurable)
  - `GDPRService.record_consent(user_id, consent_type, granted, version, ip, ua) -> ConsentRecord`: always inserts

- `backend/app/modules/gdpr/router.py`:
  - `GET /api/v1/me/export` — 202, returns `{ job_id }`
  - `GET /api/v1/me/export/{job_id}` — returns job status + `download_url` when ready
  - `DELETE /api/v1/me` — body: `{ confirm: "DELETE" }`; returns 202
  - `GET /api/v1/me/consents`
  - `POST /api/v1/me/consents`

**Tests**

- Export creates job in pending state
- DELETE /me soft-deletes user
- Consent POST twice creates two rows (append-only)
- Missing `confirm: "DELETE"` body → 422

**Done when**

- [ ] All GDPR endpoint tests pass, 100% coverage

---

## G · Background workers

> HTML ref: §"Background jobs" (entire section)

### T-034 ARQ worker setup + APScheduler

> needs: T-005, T-003

**Goal** ARQ worker that shares the FastAPI DB engine; APScheduler for cron jobs.

**Build**

- `backend/app/workers/main.py`:
  ```python
  class WorkerSettings:
      functions = [...]           # populated by T-035 through T-037
      redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
      max_jobs = 20
      job_timeout = 300
      on_startup = worker_startup   # creates async engine, Redis pool, starts scheduler
      on_shutdown = worker_shutdown # disposes all
  ```
- `backend/app/workers/scheduler.py` — APScheduler with Redis distributed lock (so multiple replicas don't double-fire):
  - `cleanup_expired_sessions` — every 6 h
  - `cleanup_expired_tokens` — every 1 h
  - `sync_expiring_trials` — daily 08:00 UTC
  - `expire_data_export_urls` — every 1 h

- `backend/app/workers/jobs/records.py` — `JobRecord` SQLModel:
  ```python
  class JobRecord(SQLModel, table=True):
      id: UUID; org_id: UUID | None; job_type: str
      status: str   # "queued" | "running" | "done" | "failed"
      arq_job_id: str | None; payload: dict; result: dict | None
      error: str | None; attempts: int = 0
      created_at: datetime; started_at: datetime | None; completed_at: datetime | None
  ```

**Tests**

- In-process ARQ test: enqueue a no-op job, assert it executes
- APScheduler: each scheduled function is callable with mocked context

**Done when**

- [ ] No-op job round-trips
- [ ] Scheduler functions callable in isolation

---

### T-035 Email job (Resend + templates)

> needs: T-034, T-003

**Goal** Async email delivery with Jinja2 templates; retry on transient errors.

**Build**

- `backend/app/workers/jobs/email.py`:
  - `send_email(ctx, *, to: str, template: str, context: dict) -> None`:
    - Render `backend/app/templates/email/{template}.html.j2`
    - Send via Resend API
    - Retry up to 3× with exponential backoff on 5xx / network errors
    - On final failure: log `ERROR` + Sentry — do **not** re-raise (prevents head-of-line blocking)

- Templates to create (HTML + Jinja2):
  - `verify_email.html.j2` — verification link
  - `password_reset.html.j2` — reset link
  - `magic_link.html.j2` — login link
  - `invitation.html.j2` — invite link + org name
  - `welcome.html.j2` — post-verify welcome

**Tests**

- Mock Resend: verify template renders with context; verify retry on simulated 503

**Done when**

- [ ] Retry test passes with mock

---

### T-036 GDPR purge job

> HTML ref: §"GDPR & compliance" → "Data purge job"
> needs: T-034, T-033

**Goal** Hard-delete all user data in dependency order after grace period.

**Build**

- `backend/app/workers/jobs/gdpr.py`:
  - `purge_user_data(ctx, *, user_id: UUID) -> None`:
    - Verify user still soft-deleted (abort if not — allows cancellation during grace)
    - Delete in dependency order: `DataExportJob`, `ConsentRecord`, `AuditEvent`, `Notification`, `StoredFile` (also call `storage.delete(key)` for each), `MagicLinkToken`, `PasswordResetToken`, `EmailVerificationToken`, `TOTPSecret`, `OAuthAccount`, `Session`, `Membership`, `User`
    - All deletes in one transaction; commit only if all succeed
    - After commit: detach Stripe customer (update `org` if org has no other members, or just remove `stripe_customer_id`)

**Tests**

- After `purge_user_data`: parameterized sweep of all 14 tables confirms zero rows referencing the `user_id`

**Done when**

- [ ] Purge completeness test green (all 14 tables checked)

---

### T-037 Export job

> needs: T-034, T-032

**Goal** Generate a JSON zip of all user data and email a download link.

**Build**

- `backend/app/workers/jobs/gdpr.py` (extend):
  - `export_user_data(ctx, *, user_id: UUID, job_id: UUID) -> None`:
    - Collect: User record, Sessions, OAuthAccounts, ConsentRecords, AuditEvents (where actor_id = user_id), Notifications, Memberships, StoredFile metadata
    - Write as `data.json` inside a zip; sign a 24 h download URL
    - Update `DataExportJob(status="ready", download_url=..., expires_at=now+24h)`
    - Enqueue `send_email` with `download_link` template

**Tests**

- Export produces a zip containing all expected keys
- `DataExportJob.status` transitions to `"ready"`

**Done when**

- [ ] Export completeness test green

---

## H · Frontend — shared packages

> HTML ref: §"Shared packages"

### T-038 Shared packages scaffold

> needs: T-001

**Goal** Six shared packages buildable and importable from apps.

**Build**
For each of `packages/{ui,api-client,types,hooks,validation,utils}`:

- `package.json`: name `@forge/<name>`, `"type": "module"`, build with `tsup`, export from `src/index.ts`
- `tsconfig.json`: extends root, `strict: true`, `noUncheckedIndexedAccess: true`

- `packages/types/src/index.ts` — initial exports:

  ```typescript
  export enum Permission { MEMBERS_READ="members:read", ... }  // matches backend
  export enum MemberRole { OWNER="owner", ADMIN="admin", MEMBER="member" }
  export type SubscriptionStatus = "trialing"|"active"|"past_due"|"canceled"|"incomplete"
  ```

- `packages/utils/src/index.ts`:
  - `cn(...classes)` — `clsx` + `tailwind-merge`
  - `formatDate(iso: string, locale?: string) -> string`
  - `formatBytes(bytes: number) -> string`
  - `slugify(str: string) -> string`

- `packages/validation/src/index.ts` — initial Zod schemas:
  - `loginSchema`, `registerSchema`, `passwordResetSchema`, `profileSchema`

- `packages/hooks/src/index.ts` — stubs (populated by T-047):
  - Must have zero JSX imports (enforced by `eslint-plugin-no-restricted-imports`)

**Tests**

- `pnpm -r build` succeeds
- `apps/web` can `import { cn } from "@forge/utils"` without error

**Done when**

- [ ] `pnpm -r build` clean
- [ ] Import smoke test passes

---

### T-039 API client generation

> HTML ref: §"Shared packages" → "API client generation"
> needs: T-038, T-002

**Goal** Auto-generated typed API client; `build` never proceeds with stale types.

**Build**

- `packages/api-client/openapi-ts.config.ts`:
  ```typescript
  export default defineConfig({
    input: "http://localhost:8000/openapi.json",
    output: { path: "src/generated", format: "esm" },
    client: "fetch",
    plugins: ["@hey-api/sdk", "@tanstack/react-query"],
  });
  ```
- `packages/api-client/src/client.ts`: configure base URL from `VITE_API_URL`; include `credentials: "include"` for cookie-based refresh
- `packages/api-client/package.json` script: `"generate": "openapi-ts"`
- `Makefile gen-client`: spins up backend, runs generate, shuts down
- `turbo.json`: `build` pipeline `dependsOn: ["gen-client", "^build"]`

**Tests**

- `make gen-client` succeeds with local backend running
- Generated `src/generated/` contains typed functions matching the OpenAPI routes

**Done when**

- [ ] Client regenerates cleanly
- [ ] `apps/web` imports a generated function with correct TypeScript types

---

## I · Frontend — React web app

> HTML ref: §"React web app" (entire section)

### T-040 Vite + React skeleton

> needs: T-038

**Goal** Strict TypeScript Vite app with Tailwind and ESLint configured.

**Build**

- `apps/web/package.json`: React 18, Vite 5, TanStack Router, TanStack Query v5, React Hook Form, Zod, shadcn/ui, lucide-react, sonner (toasts)
- `apps/web/tsconfig.json`: `strict: true`, `noUncheckedIndexedAccess: true`, `exactOptionalPropertyTypes: true`
- `apps/web/eslint.config.ts`: `@typescript-eslint/strict-type-checked`, custom rule banning `dangerouslySetInnerHTML`
- `apps/web/vite.config.ts`: proxy `/api → http://localhost:8000` in dev
- Tailwind configured; `apps/web/src/main.tsx` renders a styled placeholder

**Tests**

- `pnpm --filter web type-check` clean
- `pnpm --filter web lint` clean

**Done when**

- [ ] `pnpm --filter web dev` serves without error
- [ ] Type-check and lint clean

---

### T-041 UI component library (shadcn)

> needs: T-040, T-038

**Goal** Shared component library in `packages/ui`, re-exported for the web app.

**Build**

- Initialize shadcn in `packages/ui`
- Install components: `Button`, `Input`, `Label`, `Form`, `Card`, `Dialog`, `Tabs`, `Table`, `Badge`, `Avatar`, `DropdownMenu`, `Skeleton`, `Alert`, `Separator`, `Tooltip`
- Toast via `sonner` (not shadcn's built-in — simpler API)
- `packages/ui/src/index.ts` re-exports all
- `apps/web` adds `@forge/ui` as workspace dep

**Tests**

- `pnpm --filter web` can render `<Button>` without TS errors
- `pnpm -r build` clean

**Done when**

- [ ] Button renders in app; build clean

---

### T-042 TanStack Router + route structure

> HTML ref: §"React web app" → "Route guards" | "Core pages built in Phase 1"
> needs: T-040, T-039

**Goal** Type-safe routing with auth guards and lazy-loaded feature routes.

**Build**

- `apps/web/src/router.tsx` + file-based routes:
  ```
  /login, /register, /verify-email, /forgot-password,
  /reset-password, /magic-link, /accept-invitation/$token
  /_app/              ← auth-required layout route
    /                 ← dashboard (empty state)
    /settings/profile
    /settings/security
    /settings/org
    /settings/members
    /settings/billing
    /settings/notifications
  ```
- Router context: `{ auth: { user: UserPublic | null }, queryClient }`
- `_app` `beforeLoad`: if no user → `throw redirect({ to: "/login" })`; if no `email_verified_at` → `throw redirect({ to: "/verify-email" })`
- All routes lazy-imported (code splitting)
- `apps/web/src/main.tsx`: `QueryClientProvider` wrapping `RouterProvider`

**Tests**

- Navigate to `/_app/` without auth cookie → redirected to `/login`
- Navigate with valid auth context → renders dashboard

**Done when**

- [ ] Auth guard redirect test passes
- [ ] `pnpm type-check` clean (TanStack Router generates types)

---

### T-043 Auth feature — all auth pages

> HTML ref: §"React web app" → "Auth flows"
> needs: T-042, T-041, T-038

**Goal** All Phase-1 auth UI flows.

**Build**

- `apps/web/src/features/auth/`:
  - `LoginPage`: email + password form + Google/GitHub buttons; on success sets query cache + navigates to `/_app/`
  - `RegisterPage`: email + password; on success shows "check your inbox"
  - `VerifyEmailPage`: reads `?token=` from URL, calls verify endpoint, shows result
  - `ForgotPasswordPage`: email form; always shows same confirmation copy
  - `ResetPasswordPage`: reads `?token=`, new password form
  - `MagicLinkPage`: email form
  - `AcceptInvitationPage`: shows org name from public invitation endpoint; "Accept" button
- All forms use `React Hook Form` + shared Zod schemas from `@forge/validation`
- `useAuth()` hook in `@forge/hooks`: `{ user, login, logout, register }` — pure TS, no JSX
- Loading / error / empty / success states for every async action
- A11y: every input has a `<label>`, errors announced via `aria-live`, focus managed on navigation

**Tests** (Vitest + React Testing Library)

- Login: invalid email → validation error shown; success → API called
- Register: short password → blocked by Zod
- A11y: `axe-core` scan on each page returns no violations

**Done when**

- [ ] All form tests pass
- [ ] axe-core clean on all auth pages

---

### T-044 App shell + settings pages

> HTML ref: §"React web app" → "App shell" | §"Core pages built in Phase 1"
> needs: T-043, T-027, T-031

**Goal** Authenticated app shell with org switcher and all Phase-1 settings tabs.

**Build**

- `apps/web/src/layouts/AppLayout.tsx`: sidebar (logo, nav, org switcher, user menu), topbar (breadcrumbs, notification bell stub)
- Org switcher: lists user's orgs from `/api/v1/orgs`, sets `X-Org-ID` header on all subsequent requests
- Settings tabs:
  - **Profile**: name, avatar upload stub
  - **Security**: active sessions list with "revoke" button; 2FA enable/disable
  - **Organization**: org name, logo upload stub
  - **Members**: member list with roles; invite form; role change dropdown (gated by `usePermission(MEMBERS_MANAGE)`)
  - **Billing**: current plan badge; "Upgrade" button → checkout redirect; invoice list stub
- `usePermission(p: Permission)` and `useOrg()` hooks from `@forge/hooks`
- `useFeatureFlag(flag: string)` hook: reads `org.plan.features[flag]`

**Tests**

- Members tab: `MEMBER` role → "Invite" button hidden; `OWNER` → visible
- Settings tabs render correct loading + empty + error states

**Done when**

- [ ] Permission-gated UI test passes
- [ ] All settings tabs render without errors

---

## J · Frontend — React Native

> HTML ref: §"React Native (Expo)" (entire section)

### T-045 Expo app skeleton

> needs: T-038

**Goal** Expo + Expo Router app with NativeWind and TanStack Query.

**Build**

- `apps/mobile/package.json`: Expo SDK 52, Expo Router 4, NativeWind, TanStack Query, `expo-secure-store`, `expo-local-authentication`, `expo-notifications`
- `apps/mobile/app/_layout.tsx`: `QueryClientProvider` + auth check
- NativeWind configured; `tsconfig.json` strict
- A11y default: `eslint-plugin-react-native-a11y` — every `Pressable` requires `accessibilityLabel`

**Tests**

- `pnpm --filter mobile type-check` clean

**Done when**

- [ ] Expo Go shows a "Hello ForgeSaaS" screen
- [ ] Type-check clean

---

### T-046 Mobile auth — secure token storage + auto-refresh

> HTML ref: §"React Native" → "Auth token handling on mobile"
> needs: T-045, T-039

**Goal** Tokens stored in Keychain/Keystore; 401 automatically triggers refresh.

**Build**

- `apps/mobile/src/lib/auth.ts`:
  - `login(email, password)`: call API, store `access_token` + `refresh_token` in `expo-secure-store`
  - `logout()`: clear secure store entries
  - `getAccessToken()`: optionally gates behind biometric prompt (configurable)
- `apps/mobile/src/lib/api.ts`:
  - Base URL from `expo-constants` / env
  - Request interceptor: inject `Authorization: Bearer <token>`
  - Response interceptor: on 401 → call refresh → retry original request → on refresh failure → `logout()` + navigate to login
  - **Never** `AsyncStorage` for tokens

**Tests**

- Mock `expo-secure-store`; simulate 401 → verify refresh called → verify retry

**Done when**

- [ ] Interceptor refresh test passes

---

### T-047 Mobile auth screens + deep links

> HTML ref: §"React Native" → "Native-specific"
> needs: T-046

**Goal** Auth screens at parity with web Phase 1; deep links for magic links and OAuth.

**Build**

- `apps/mobile/app/(auth)/`: login, register, verify-email, forgot-password, magic-link
- `apps/mobile/app/(app)/`: dashboard placeholder, settings placeholder
- Deep links via Expo Router:
  - `forge://verify-email/[token]`
  - `forge://reset-password/[token]`
  - `forge://invitations/[token]`
  - `forge://oauth/callback`
- Shared Zod schemas from `@forge/validation` for all forms

**Tests**

- Deep link `forge://verify-email/abc` navigates to verify screen with token `"abc"`

**Done when**

- [ ] Deep link routing test passes
- [ ] Auth flows work on iOS Simulator + Android emulator

---

## K · Testing infrastructure

> HTML ref: §"Testing strategy" (entire section)

### T-048 Backend test infrastructure

> needs: T-005, T-009

**Goal** testcontainers-based real DB/Redis fixtures usable by every subsequent test file.

**Build**

- `backend/tests/conftest.py`:

  ```python
  @pytest.fixture(scope="session")
  async def engine():
      # start PostgresContainer("postgres:16-alpine") and RedisContainer("redis:7-alpine")
      # run alembic upgrade head
      # yield engine
      # stop containers

  @pytest.fixture
  async def session(engine):
      async with engine.connect() as conn:
          txn = await conn.begin()
          async with AsyncSession(bind=conn) as s:
              yield s
          await txn.rollback()   # DB pristine after every test

  @pytest.fixture
  async def client(session):
      app.dependency_overrides[get_session] = lambda: session
      async with AsyncClient(app=app, base_url="http://test") as c:
          yield c

  @pytest.fixture
  async def authed_client(client, session):
      # factory: creates user + org + membership, returns client with Bearer token
      ...
  ```

- `backend/pytest.ini`: `asyncio_mode = "auto"`, `testpaths = ["tests"]`

**Tests**

- Smoke: `authed_client` GETs `/api/v1/auth/me` → 200

**Done when**

- [ ] Smoke test passes against real Postgres + Redis containers

---

### T-049 Cross-tenant isolation sweep

> HTML ref: §"Testing strategy" → "What must have tests" | §"OWASP" → A01
> needs: T-048, T-027, T-031

**Goal** Parameterized test proving every org-scoped endpoint returns 403/404 cross-tenant.

**Build**

- `backend/tests/security/test_tenant_isolation.py`:
  - Create `org_a` + `user_a`; create `org_b` + `user_b`
  - Parameterize over every endpoint that takes an org-scoped resource ID
  - For each: `user_a` with `X-Org-ID: org_a` attempts to access `org_b`'s resource
  - Assert response is **403 or 404, never 200 or the resource body**

**Tests** (this file IS the test)

**Done when**

- [ ] Every org-scoped endpoint covered by the sweep
- [ ] All assertions pass

---

### T-050 Schemathesis contract test

> HTML ref: §"Testing strategy" → "Contract — auto-generated"
> needs: T-048

**Goal** Fuzz every OpenAPI endpoint for schema conformance and unexpected 500s.

**Build**

- `backend/tests/contract/test_schemathesis.py`:
  - `schemathesis.from_asgi(app, base_url="http://test")`
  - All endpoints: response matches declared schema; no 500 on valid inputs
  - Auth-required endpoints get a bearer token via a `schemathesis` hook

**Done when**

- [ ] `schemathesis` run is clean with no failures

---

### T-051 Frontend test setup

> HTML ref: §"Testing strategy" → "Frontend & mobile testing"
> needs: T-043

**Goal** Vitest + RTL + MSW + axe-core for the web app.

**Build**

- `apps/web/vitest.config.ts`: jsdom, `@testing-library/jest-dom`
- `apps/web/src/test/setup.ts`: MSW server setup; `jest-axe` matcher `toHaveNoViolations`
- `apps/web/src/test/render.tsx`: custom render with `QueryClientProvider` + `RouterProvider`
- MSW handlers stub all generated API client endpoints

**Tests**

- Login form: invalid email → error; success → API stub called

**Done when**

- [ ] Login form test passes
- [ ] axe-core passes on login page

---

## L · CI/CD pipeline

> HTML ref: §"CI/CD pipeline" (entire section)

### T-052 CI — lint + type-check job

> needs: T-012

**Goal** Fast first CI gate; fails before tests even start if style is broken.

**Build**

- `.github/workflows/ci.yml` — job `lint`:
  ```yaml
  on: [push, pull_request]
  jobs:
    lint:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-python@v5 with python-version: "3.12"
        - run: pip install -e ".[dev]"
        - run: ruff check . && ruff format --check .
        - run: mypy --strict app/
        - uses: pnpm/action-setup@v4
        - run: pnpm install --frozen-lockfile
        - run: pnpm lint && pnpm type-check
  ```
- Cache: `~/.cache/pip` and pnpm store

**Done when**

- [ ] Workflow green on clean commit
- [ ] Workflow red when a lint violation is committed

---

### T-053 CI — test job

> needs: T-048, T-051, T-052

**Goal** Run backend + frontend tests in CI with real dependencies.

**Build**

- `.github/workflows/ci.yml` — job `test` (runs after `lint`):
  ```yaml
  test:
    needs: lint
    services:
      postgres:
        image: postgres:16-alpine
        env: { POSTGRES_PASSWORD: test }
        options: --health-cmd pg_isready
      redis:
        image: redis:7-alpine
        options: --health-cmd "redis-cli ping"
    steps:
      - run: pytest --cov=app --cov-fail-under=85
          --cov-branch tests/ -x
      - run: pnpm --filter web gen-client
      - run: pnpm --filter web test --run
      - run: schemathesis run http://localhost:8000/openapi.json
          --validate-schema=true --checks=all
  ```

**Done when**

- [ ] Workflow green on clean repo

---

### T-054 CI — security scans job

> HTML ref: §"OWASP & security testing" → "Security tooling in the pipeline"
> needs: T-052

**Goal** Automated security gates per OWASP tooling requirements.

**Build**

- `.github/workflows/ci.yml` — job `security` (parallel with `test`):
  ```yaml
  security:
    needs: lint
    steps:
      - run: bandit -r app/ -ll # only medium+ severity
      - run: semgrep --config auto --error
      - run: pip-audit --strict
      - run: npm audit --audit-level=high
      - run: trufflehog git file://. --fail
      - run: gitleaks detect --no-git
      - run: trivy fs . --exit-code 1 --severity HIGH,CRITICAL
  ```
- Cache: Trivy DB, Semgrep rules

**Done when**

- [ ] Security job green on clean repo
- [ ] Security job red when a known-bad pattern is introduced

---

### T-055 CI — build + deploy job

> HTML ref: §"CI/CD pipeline" → "Pipeline stages" 4 + 5
> needs: T-053, T-054

**Goal** Docker image build + rolling deploy with health gate; rollback on failure.

**Build**

- `backend/Dockerfile`: multi-stage, non-root user, pinned base image `python:3.12-slim`
- `.github/workflows/ci.yml` — job `build` (needs: test + security):
  ```yaml
  build:
    needs: [test, security]
    steps:
      - run: turbo build # gen-client runs first
      - run: docker build -t $IMAGE --label "git-sha=$GITHUB_SHA" .
      - run: trivy image $IMAGE --exit-code 1 --severity HIGH,CRITICAL
      - run: docker push $IMAGE
  ```
- Job `deploy` (only on `main`, needs: build):
  ```yaml
  deploy:
    if: github.ref == 'refs/heads/main'
    needs: build
    steps:
      - run: alembic upgrade head # fail = abort, no deploy
      - run: rolling-deploy --image $IMAGE --health /health/ready
      - run: smoke-test || (rollback && exit 1)
      - run: playwright test --project=staging
  ```
- Branch protection: `main` requires green CI + 1 approval, linear history, no direct push

**Done when**

- [ ] Build job produces a pushed image
- [ ] Deploy aborts if migrations fail
- [ ] Rollback executes if smoke test fails

---

## M · Local dev + seed

> HTML ref: §"Local dev & config" → "Onboarding a new developer"

### T-056 Seed script + make targets

> needs: T-029, T-025

**Goal** `make seed` produces a working local environment: superadmin + demo plans + demo org.

**Build**

- `backend/app/core/seed.py`:
  - Creates `Plan(slug="free", features={seats:3, storage_gb:1, api_calls:1000})` and `Plan(slug="pro", features={seats:25, storage_gb:50, api_calls:100000})`
  - Creates superadmin user `admin@forge.local` / `DevPassword123!`
  - Creates demo org `Forge Demo`, membership OWNER
  - Idempotent: if records exist, skip creation

- `Makefile` targets:
  - `seed`: `python -m app.core.seed`
  - `dev`: `docker compose up -d && make migrate && make seed`
  - `new-module`: copies `backend/app/modules/_template/` to target, renames placeholders

**Tests**

- `make seed` run twice → no duplicate rows
- Second run exits 0

**Done when**

- [ ] Idempotent seed test passes
- [ ] Full `make dev` → `curl /health/ready` → 200 chain works

---

## N · Phase 1 verification

### T-057 End-to-end smoke runbook

> HTML ref: §"Launch checklist" (entire section)
> needs: all Phase 1 tasks

**Goal** Document and execute the Phase 1 acceptance flow. This is the gate to Phase 2.

**Build**

- `docs/runbooks/phase1-verification.md` documenting exact steps:
  1. `git clone && make dev` — all services healthy
  2. `make seed` — superadmin and plans created
  3. Register new user via `POST /api/v1/auth/register`
  4. Retrieve verification link from MailHog (`http://localhost:8025`)
  5. `POST /api/v1/auth/verify-email` with token
  6. `POST /api/v1/auth/login` — receive access + refresh tokens
  7. `POST /api/v1/orgs` — create org
  8. `POST /api/v1/orgs/{id}/invitations` — invite second user
  9. Register second user; accept invitation
  10. `POST /api/v1/billing/checkout-session` — receive Stripe test checkout URL
  11. Complete checkout with Stripe test card `4242 4242 4242 4242`
  12. `GET /api/v1/billing/subscription` — confirm `status: "active"`
  13. `GET /api/v1/me/export` — confirm `{"status":"pending"}`
  14. `DELETE /api/v1/me` with `{"confirm":"DELETE"}` — confirm 202

- Execute the runbook manually; document any failures

**Phase 1 complete when ALL of the following are true:**

- [ ] Runbook executes without intervention
- [ ] `pytest --cov` reports ≥85% overall and **100% on `auth/`, `billing/`, `tenancy/`, `gdpr/`**
- [ ] CI jobs `lint`, `test`, `security`, `build` all green on `main`
- [ ] Cross-tenant isolation sweep (T-049) passes
- [ ] Schemathesis (T-050) passes
- [ ] `pre-commit run --all-files` clean
- [ ] `import-linter` passes (no layer violations)
- [ ] Launch checklist categories "Security", "Data", "Reliability", "Testing & QA", "Billing" from `forgesaas-plan.html` all checked

---

# Phase 1.5 — Template infrastructure

> **Prerequisite:** T-057 (Phase 1 end-to-end verification) complete and green.
>
> **Why this phase exists:** Phase 1 built a working product. This phase restructures it so it can become a _template_ — the root from which every future product is forked. It must happen before Phase 2, because Phase 2 is the first phase that adds optional modules. Retrofitting the module toggle system onto a codebase that wasn't designed for it is significantly harder than adding it now.
>
> **Phase 1.5 acceptance criterion:** You can fork the repo, disable billing and file storage in one config file, run `make dev`, and get a fully working product — correct routes, correct schema, correct UI — with no Stripe keys and no file-storage tables anywhere.

---

## O · Template-fork conventions

> Ref: conversation §"The core mechanic" and §"Making conflicts rare"

### T-058 Upstream remote conventions + TEMPLATE.md

> needs: T-057

**Goal** Establish the Git conventions that keep template code and product code cleanly separated so that `git merge upstream/main` is low-friction forever.

**Build**

- `TEMPLATE.md` at repo root — the contract every product must read before forking:

  ```markdown
  # ForgeSaaS Template Contract

  ## Files the template owns (never edit in a product)

  Every file not listed below. Template updates will overwrite them.
  Resolve merge conflicts by taking the template version unless you have
  a documented reason not to.

  ## Files the product owns (template never touches)

  - product.config.py — module toggles + branding
  - product.config.ts — frontend module flags + branding
  - .env.product.example — product-specific env vars
  - apps/web/src/routes/\_product/ — product-specific routes
  - backend/app/modules/ — product-specific modules (0100+ migration prefix)
  - CHANGELOG.product.md — product-level changelog
  - README.md — overwrite freely

  ## Migration prefix convention

  Template: 0001 – 0099
  Products: assign yourself a prefix block in this file and document it here:
  | Product | Prefix range |
  |--------------|-------------|
  | (your name) | 0100 – 0199 |

  ## Pulling template updates

  git fetch upstream
  git merge upstream/vX.Y.Z # always merge a tag, never upstream/main directly

  # Resolve conflicts; take template version unless product.config.py says otherwise

  make dev && make test # verify before pushing
  ```

- `CHANGELOG.md` at repo root — template-level changelog, one entry per tag:

  ```markdown
  ## [Unreleased]

  ## [1.0.0] — Phase 1 complete

  ### Added

  - Auth, tenancy, billing, GDPR, background jobs, web + mobile shells, CI/CD

  ### Conflict risk for products

  - Low — first release, no products exist yet
  ```

- `Makefile` targets:
  - `update-from-template` — `git fetch upstream && git merge upstream/$(TEMPLATE_VERSION)`
  - `check-template-drift` — `git log upstream/main ^HEAD --oneline` (shows unmerged template commits)

- `.github/CODEOWNERS`:

  ```
  # Template core — requires template-maintainer approval in product PRs
  /backend/app/core/          @your-org/template-maintainers
  /backend/app/modules/auth/  @your-org/template-maintainers
  /packages/                  @your-org/template-maintainers
  /docker-compose.yml         @your-org/template-maintainers

  # Product-owned — no template-maintainer review needed
  /product.config.py          @your-org/product-owners
  /product.config.ts          @your-org/product-owners
  ```

**Tests**

- `make check-template-drift` exits 0 on a fresh clone (no unmerged upstream commits)

**Done when**

- [ ] `TEMPLATE.md` documents the full contract
- [ ] `CHANGELOG.md` has the v1.0.0 entry
- [ ] Both Makefile targets work
- [ ] `.github/CODEOWNERS` in place

---

### T-059 Product config files

> needs: T-058

**Goal** Create the two product-owned config files (Python + TypeScript) that every product customises. The template ships working defaults; products override only what they need.

**Build**

- `backend/product.config.py`:

  ```python
  """
  Product configuration — this file is owned by the product, not the template.
  The template never modifies it. Edit freely.
  """
  from app.core.modules import EnabledModules, ModuleConfig

  # ── Module toggles ──────────────────────────────────────────────────────────
  # Set enabled=False for any module you do not need.
  # The module's routes, DB tables, and UI will all be excluded automatically.
  # See TEMPLATE.md for dependency rules (e.g. notifications requires billing).
  MODULES = EnabledModules(
      billing=ModuleConfig(enabled=True),
      notifications=ModuleConfig(enabled=True),
      file_storage=ModuleConfig(enabled=True),
      feature_flags=ModuleConfig(enabled=True),
      gdpr=ModuleConfig(enabled=True),
      admin=ModuleConfig(enabled=True),
      oauth=ModuleConfig(enabled=True),
      totp_2fa=ModuleConfig(enabled=True),
      multi_org=ModuleConfig(enabled=True),
      usage_metering=ModuleConfig(enabled=False),
      outbound_webhooks=ModuleConfig(enabled=False),
  )

  # ── Branding ────────────────────────────────────────────────────────────────
  APP_NAME = "ForgeSaaS"
  PRIMARY_COLOR = "#7c6ff7"
  LOGO_URL = "/logo.svg"
  SUPPORT_EMAIL = "support@example.com"
  ```

- `product.config.ts`:

  ```typescript
  /**
   * Product configuration — owned by the product, not the template.
   * Keep in sync with backend/product.config.py.
   * The template never modifies this file.
   */
  import type { ProductConfig } from "@forge/types";

  export const config = {
    name: "ForgeSaaS",
    primaryColor: "#7c6ff7",
    logoUrl: "/logo.svg",
    supportEmail: "support@example.com",
    modules: {
      billing: true,
      notifications: true,
      fileStorage: true,
      featureFlags: true,
      gdpr: true,
      admin: true,
      oauth: true,
      totp2fa: true,
      multiOrg: true,
      usageMetering: false,
      outboundWebhooks: false,
    },
  } satisfies ProductConfig;
  ```

- Add `ProductConfig` type to `packages/types/src/index.ts`
- Add `.env.product.example` (empty file with a comment block explaining it is for product-specific vars)

**Tests**

- `product.config.py` imports cleanly: `python -c "from product.config import MODULES"`
- `product.config.ts` satisfies the `ProductConfig` type: `pnpm type-check` clean

**Done when**

- [ ] Both config files present and importable
- [ ] `ProductConfig` type exported from `@forge/types`

---

## P · Module toggle system

> Ref: conversation §"How it works at each layer"

### T-060 Backend module registry + loader

> needs: T-059, T-003

**Goal** The single source of truth for which modules are active. Validates dependency rules at startup. Every subsequent module registers here.

**Build**

- `backend/app/core/modules.py`:

  ```python
  from __future__ import annotations
  from dataclasses import dataclass, field
  from typing import Literal

  ModuleName = Literal[
      "billing", "notifications", "file_storage", "feature_flags",
      "gdpr", "admin", "oauth", "totp_2fa", "multi_org",
      "usage_metering", "outbound_webhooks",
  ]

  @dataclass(frozen=True)
  class ModuleConfig:
      enabled: bool = True
      requires: list[ModuleName] = field(default_factory=list)

  @dataclass(frozen=True)
  class EnabledModules:
      billing:           ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      notifications:     ModuleConfig = field(default_factory=lambda: ModuleConfig(True,  ["billing"]))
      file_storage:      ModuleConfig = field(default_factory=lambda: ModuleConfig(True,  ["billing"]))
      feature_flags:     ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      gdpr:              ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      admin:             ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      oauth:             ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      totp_2fa:          ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      multi_org:         ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      usage_metering:    ModuleConfig = field(default_factory=lambda: ModuleConfig(False, ["billing"]))
      outbound_webhooks: ModuleConfig = field(default_factory=lambda: ModuleConfig(False, ["billing"]))
  ```

- `backend/app/core/module_loader.py`:

  ```python
  from functools import lru_cache
  from app.core.modules import EnabledModules, ModuleName

  def validate_modules(modules: EnabledModules) -> None:
      """
      Called at startup. Raises RuntimeError with a clear message if any
      enabled module declares a `requires` dependency that is disabled.
      Fast-fail: developers learn about misconfiguration at boot, not at runtime.
      """
      for name, config in vars(modules).items():
          if not isinstance(config, ModuleConfig): continue
          if config.enabled:
              for dep in config.requires:
                  dep_cfg = getattr(modules, dep)
                  if not dep_cfg.enabled:
                      raise RuntimeError(
                          f"Module '{name}' requires '{dep}' which is disabled.\n"
                          f"Fix: either set {dep}=ModuleConfig(enabled=True) "
                          f"or set {name}=ModuleConfig(enabled=False) "
                          f"in backend/product.config.py"
                      )

  @lru_cache(maxsize=1)
  def get_modules() -> EnabledModules:
      from product.config import MODULES   # imported lazily — survives test overrides
      validate_modules(MODULES)
      return MODULES

  def is_enabled(module: ModuleName) -> bool:
      return getattr(get_modules(), module).enabled
  ```

- Call `get_modules()` in `app/main.py` lifespan startup (before any router is registered) so misconfiguration fails loudly at boot

**Tests** (100% coverage on `module_loader.py`)

- `validate_modules` with `notifications.enabled=True, billing.enabled=False` → `RuntimeError` with clear message naming both modules
- `validate_modules` with valid config → no exception
- `get_modules()` is cached: calling twice returns same object, product.config only imported once
- Override via `pytest` monkeypatch: disable billing → `is_enabled("billing")` returns False

**Done when**

- [ ] Dependency-violation test passes with exact error message
- [ ] Startup wires `get_modules()` call
- [ ] 100% coverage

---

### T-061 Conditional router registration

> needs: T-060, T-022, T-027, T-031

**Goal** Routers for optional modules are only registered when their module is enabled. Disabled modules return 404 on every path — they do not exist.

**Build**

- Rewrite `backend/app/api/v1/__init__.py` to use `build_router()`:

  ```python
  from app.core.module_loader import get_modules
  from fastapi import APIRouter

  def build_router() -> APIRouter:
      m = get_modules()
      router = APIRouter()

      # ── Always-on (never optional) ───────────────────────────────────────
      from app.modules.auth.router     import router as auth_router
      from app.modules.tenancy.router  import router as tenancy_router
      from app.modules.audit.router    import router as audit_router
      router.include_router(auth_router,    prefix="/auth",   tags=["auth"])
      router.include_router(tenancy_router, prefix="/orgs",   tags=["tenancy"])
      router.include_router(audit_router,   prefix="/audit",  tags=["audit"])

      # ── Optional ─────────────────────────────────────────────────────────
      if m.billing.enabled:
          from app.modules.billing.router import router as billing_router
          router.include_router(billing_router, prefix="/billing", tags=["billing"])

      if m.notifications.enabled:
          from app.modules.notifications.router import router as notif_router
          router.include_router(notif_router, prefix="/notifications", tags=["notifications"])

      if m.file_storage.enabled:
          from app.modules.storage.router import router as storage_router
          router.include_router(storage_router, prefix="/files", tags=["files"])

      if m.feature_flags.enabled:
          from app.modules.feature_flags.router import router as ff_router
          router.include_router(ff_router, prefix="/feature-flags", tags=["feature-flags"])

      if m.gdpr.enabled:
          from app.modules.gdpr.router import router as gdpr_router
          router.include_router(gdpr_router, prefix="/me", tags=["gdpr"])

      if m.admin.enabled:
          from app.modules.admin.router import router as admin_router
          router.include_router(admin_router, prefix="/admin", tags=["admin"])

      return router
  ```

**Tests**

- `billing=False` → `GET /api/v1/billing/plans` returns 404
- `billing=True` → `GET /api/v1/billing/plans` returns 200
- Disabled module routes are absent from `GET /openapi.json`
- All always-on routes remain reachable regardless of module config

**Done when**

- [ ] All routing toggle tests pass
- [ ] OpenAPI schema test passes

---

### T-062 Conditional Alembic migrations

> needs: T-060, T-010

**Goal** `alembic upgrade head` only creates tables for enabled modules. A product with billing disabled has no billing tables — ever.

**Build**

- Reorganise `alembic/versions/` into sub-directories by module:

  ```
  alembic/versions/
  ├── core/                    ← always runs
  │   ├── 0001_auth_tables.py
  │   ├── 0002_tenancy_tables.py
  │   └── 0003_audit_events.py
  ├── billing/                 ← only if billing.enabled
  │   ├── 0010_billing_tables.py
  │   └── 0011_dunning_tables.py   (added in Phase 2)
  ├── notifications/           ← only if notifications.enabled
  │   └── 0020_notification_tables.py
  ├── file_storage/            ← only if file_storage.enabled
  │   └── 0030_stored_files.py
  ├── gdpr/                    ← only if gdpr.enabled
  │   └── 0040_gdpr_tables.py
  └── product/                 ← product-specific migrations, 0100+
       └── .gitkeep
  ```

- Update `alembic/env.py` to conditionally import models:

  ```python
  from app.core.module_loader import get_modules

  def load_models_for_migration() -> None:
      """Import only models whose module is enabled so autogenerate
      only produces migrations for active tables."""
      # core — always
      import app.modules.auth.models     # noqa: F401
      import app.modules.tenancy.models  # noqa: F401
      import app.modules.audit.models    # noqa: F401

      m = get_modules()
      if m.billing.enabled:
          import app.modules.billing.models       # noqa: F401
      if m.notifications.enabled:
          import app.modules.notifications.models # noqa: F401
      if m.file_storage.enabled:
          import app.modules.storage.models       # noqa: F401
      if m.gdpr.enabled:
          import app.modules.gdpr.models          # noqa: F401

  load_models_for_migration()
  target_metadata = SQLModel.metadata
  ```

- Alembic's `include_object` hook filters migration objects by module:

  ```python
  # Tables that belong to each optional module
  MODULE_TABLES: dict[str, set[str]] = {
      "billing":       {"plan", "subscription", "webhookevent", "dunningcampaign"},
      "notifications": {"notification", "pushtoken"},
      "file_storage":  {"storedfile"},
      "gdpr":          {"consentrecord", "dataexportjob"},
  }

  def include_object(obj, name, type_, reflected, compare_to):
      if type_ != "table":
          return True
      m = get_modules()
      for module_name, tables in MODULE_TABLES.items():
          if name in tables:
              return getattr(m, module_name).enabled
      return True   # core tables always included
  ```

- `Makefile migrate-plan` target: prints a summary of which module migrations will run before executing

**Tests**

- `billing=False` → autogenerate produces no `plan`, `subscription`, or `webhookevent` tables
- `billing=True` → those tables are present
- Round-trip (up → down → up) with billing disabled → clean
- Enabling billing on an existing disabled-billing DB (run upgrade head) → billing tables created from scratch correctly

**Done when**

- [ ] All migration toggle tests pass
- [ ] `make migrate-plan` shows a human-readable module summary
- [ ] Migration directory structure matches the layout above

---

### T-063 Public config endpoint + frontend module awareness

> needs: T-060, T-038, T-040

**Goal** The backend exposes one unauthenticated endpoint that tells every frontend surface exactly which modules are active. Frontend routes, nav items, and UI sections are driven entirely by this response — no hardcoded flags.

**Build**

- `backend/app/api/v1/config.py`:

  ```python
  from app.core.module_loader import get_modules
  from app.core.config import get_settings

  @router.get("/config", include_in_schema=True, tags=["config"])
  async def get_config() -> ConfigResponse:
      m = get_modules()
      s = get_settings()
      return ConfigResponse(
          app_name=s.APP_NAME,
          primary_color=s.PRIMARY_COLOR,
          logo_url=s.LOGO_URL,
          support_email=s.SUPPORT_EMAIL,
          modules=ModulesResponse(
              billing=m.billing.enabled,
              notifications=m.notifications.enabled,
              file_storage=m.file_storage.enabled,
              feature_flags=m.feature_flags.enabled,
              gdpr=m.gdpr.enabled,
              admin=m.admin.enabled,
              oauth=m.oauth.enabled,
              totp_2fa=m.totp_2fa.enabled,
              multi_org=m.multi_org.enabled,
              usage_metering=m.usage_metering.enabled,
              outbound_webhooks=m.outbound_webhooks.enabled,
          ),
      )
  ```
  - Register this router as always-on (not conditional) in `build_router()`
  - Response is public — no auth — it contains no sensitive data
  - Cache-Control: `public, max-age=300` (5 min) — safe to CDN-cache

- `packages/hooks/src/useModules.ts`:

  ```typescript
  import { useQuery } from "@tanstack/react-query";
  import { apiClient } from "@forge/api-client";

  export interface ActiveModules {
    billing: boolean;
    notifications: boolean;
    fileStorage: boolean;
    featureFlags: boolean;
    gdpr: boolean;
    admin: boolean;
    oauth: boolean;
    totp2fa: boolean;
    multiOrg: boolean;
    usageMetering: boolean;
    outboundWebhooks: boolean;
  }

  export function useModules(): ActiveModules {
    const { data } = useQuery({
      queryKey: ["config"],
      queryFn: () => apiClient.config.getConfig(),
      staleTime: Infinity, // never changes at runtime — requires redeploy
      gcTime: Infinity,
    });
    // deny-by-default: if the config hasn't loaded yet, all optional modules are off
    return {
      billing: data?.modules.billing ?? false,
      notifications: data?.modules.notifications ?? false,
      fileStorage: data?.modules.file_storage ?? false,
      featureFlags: data?.modules.feature_flags ?? false,
      gdpr: data?.modules.gdpr ?? false,
      admin: data?.modules.admin ?? false,
      oauth: data?.modules.oauth ?? false,
      totp2fa: data?.modules.totp_2fa ?? false,
      multiOrg: data?.modules.multi_org ?? false,
      usageMetering: data?.modules.usage_metering ?? false,
      outboundWebhooks: data?.modules.outbound_webhooks ?? false,
    };
  }
  ```
  - No JSX imports (lint rule enforced per §0)
  - Export from `packages/hooks/src/index.ts`

- Mobile: identical query in `apps/mobile/src/lib/config.ts` — same endpoint, same staleTime

**Tests**

- `GET /api/v1/config` reflects the current module config accurately
- `billing=False` → response has `modules.billing === false`
- Response is accessible without an auth token
- Cache-Control header present
- `useModules()` returns `false` for all optional modules before the query resolves (deny-by-default)
- `useModules()` returns the correct values after mock response

**Done when**

- [ ] Endpoint tests pass
- [ ] Hook deny-by-default test passes
- [ ] `pnpm type-check` clean

---

### T-064 Conditional frontend routes + nav

> needs: T-063, T-042, T-044

**Goal** Disabled module routes do not exist in the router (404, not hidden). Nav items, settings tabs, and login options are derived from `useModules()` — zero hardcoded conditionals scattered through the codebase.

**Build**

- `apps/web/src/router.tsx` — replace hardcoded route list with module-aware builder:

  ```typescript
  import { useModules } from "@forge/hooks";

  // Called once at app startup after config is fetched
  export function buildRoutes(modules: ActiveModules) {
    return [
      // Always-on routes
      loginRoute,
      registerRoute,
      verifyEmailRoute,
      forgotPasswordRoute,
      resetPasswordRoute,
      magicLinkRoute,
      acceptInvitationRoute,
      dashboardRoute,
      settingsProfileRoute,
      settingsSecurityRoute,
      settingsOrgRoute,
      settingsMembersRoute,

      // Conditional routes — absent entirely if module is off
      ...(modules.billing ? [settingsBillingRoute, upgradeRoute] : []),
      ...(modules.notifications ? [settingsNotificationsRoute] : []),
      ...(modules.fileStorage ? [settingsFilesRoute, filesRoute] : []),
      ...(modules.gdpr ? [dataPrivacyRoute] : []),
      ...(modules.admin ? [adminRoute] : []),
      ...(modules.oauth ? [oauthCallbackRoute] : []),
    ];
  }
  ```

- `apps/web/src/layouts/AppLayout.tsx` — sidebar nav derived from modules:

  ```typescript
  export function AppLayout() {
    const modules = useModules()

    const navItems = [
      { label: "Dashboard", to: "/",               icon: LayoutDashboard, always: true },
      { label: "Settings",  to: "/settings/profile",icon: Settings,       always: true },
      { label: "Files",     to: "/files",           icon: Paperclip,      show: modules.fileStorage },
      { label: "Billing",   to: "/settings/billing",icon: CreditCard,     show: modules.billing },
      { label: "Admin",     to: "/_admin",          icon: Shield,         show: modules.admin },
    ].filter(item => item.always || item.show)
    ...
  }
  ```

- `apps/web/src/features/auth/LoginPage.tsx` — OAuth buttons only when `modules.oauth`:

  ```typescript
  const modules = useModules()
  // ...
  {modules.oauth && (
    <>
      <OAuthButton provider="google" />
      <OAuthButton provider="github" />
    </>
  )}
  ```

- Settings page tabs: same pattern — Billing tab absent if `!modules.billing`, Notifications tab absent if `!modules.notifications`

- `apps/web/src/features/settings/SecurityPage.tsx` — TOTP section only when `modules.totp2fa`

- Org switcher in sidebar only when `modules.multiOrg`

**Tests** (Vitest + RTL)

- `modules.billing=false` → Settings renders without Billing tab; navigating to `/settings/billing` is a 404
- `modules.oauth=false` → Login page renders without OAuth buttons
- `modules.multiOrg=false` → Org switcher absent from sidebar
- `modules.admin=true` → Admin link present; `modules.admin=false` → Admin link absent and `/_admin` is 404
- A11y: axe-core clean on login with oauth disabled and oauth enabled

**Done when**

- [ ] All conditional route tests pass
- [ ] All conditional nav tests pass
- [ ] Zero hardcoded `if (BILLING_ENABLED)` — all conditions go through `useModules()`

---

### T-065 Billing-disabled graceful fallback

> needs: T-064, T-031, T-036

**Goal** When billing is disabled, `require_feature()` becomes a no-op (everything allowed), `plan_id` on Organization is ignored, and the seed script skips Plan creation. No code path should crash when Stripe keys are absent.

**Build**

- `backend/app/modules/billing/dependencies.py` — update `require_feature`:

  ```python
  def require_feature(feature: str, minimum: int | None = None):
      async def checker(org = Depends(get_current_org)):
          # When billing is disabled, all features are implicitly available.
          # This makes non-billing products work without a plan in the DB.
          if not is_enabled("billing"):
              return
          # ... existing plan-check logic
      return checker
  ```

- `backend/app/core/config.py` — Stripe keys become optional when billing is disabled:

  ```python
  STRIPE_SECRET_KEY:    str | None = None
  STRIPE_WEBHOOK_SECRET: str | None = None
  ```

  Startup validation: if `billing.enabled` and either key is `None` → `RuntimeError`. If `billing.disabled` and keys are absent → silently skip.

- `backend/app/core/seed.py` — skip Plan creation when billing is disabled:

  ```python
  from app.core.module_loader import is_enabled

  async def seed() -> None:
      if is_enabled("billing"):
          await seed_plans(session)
      await seed_superadmin(session)
      await seed_demo_org(session)
  ```

- `backend/app/modules/tenancy/models.py` — `Organization.plan_id` is already nullable (`UUID | None`); document explicitly that it is intentionally null in non-billing products

**Tests**

- `billing=False`, no Stripe keys in env → app starts and passes `/health/ready`
- `billing=False` → `require_feature("anything")` returns without raising
- `billing=True`, `STRIPE_SECRET_KEY=None` → startup raises with clear message
- Seed with `billing=False` → no Plan rows created, no error

**Done when**

- [ ] App starts cleanly with `billing=False` and no Stripe env vars
- [ ] All graceful-fallback tests pass

---

### T-066 Module toggle smoke test + updated runbook

> needs: T-058, T-059, T-060, T-061, T-062, T-063, T-064, T-065

**Goal** Prove the toggle system works end-to-end with two representative configurations before Phase 2 adds more modules.

**Build**

- `backend/tests/modules/test_module_toggles.py` — parametrized test matrix:

  ```python
  MODULE_CONFIGS = [
      # (config_name, modules_override, expected_absent_routes, expected_absent_tables)
      (
          "no_billing",
          {"billing": False, "notifications": False, "file_storage": False},
          ["/api/v1/billing/plans", "/api/v1/files", "/api/v1/notifications"],
          ["plan", "subscription", "webhookevent", "storedfile", "notification"],
      ),
      (
          "no_admin",
          {"admin": False},
          ["/api/v1/admin/orgs"],
          [],   # admin has no tables of its own
      ),
      (
          "minimal",  # auth + tenancy + gdpr only
          {"billing": False, "notifications": False, "file_storage": False,
           "feature_flags": False, "admin": False, "oauth": False, "totp_2fa": False},
          ["/api/v1/billing/plans", "/api/v1/files"],
          ["plan", "subscription", "storedfile", "notification"],
      ),
  ]

  @pytest.mark.parametrize("name,overrides,absent_routes,absent_tables", MODULE_CONFIGS)
  async def test_module_config(name, overrides, absent_routes, absent_tables, ...):
      # monkeypatch get_modules() with the override
      # assert each absent_route returns 404
      # assert each absent_table is not in pg_tables
  ```

- `docs/runbooks/module-toggle-guide.md`:

  ```markdown
  # Module toggle guide

  ## Disabling billing (example: internal tool, no Stripe)

  1. In `backend/product.config.py`:
     billing=ModuleConfig(enabled=False),
     notifications=ModuleConfig(enabled=True), # OK — notifications works standalone
  2. In `product.config.ts`: set `billing: false, notifications: true`
  3. Remove STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET from .env
  4. `make migrate` — billing tables skipped
  5. `make dev` — confirm /health/ready returns 200
  6. `GET /api/v1/config` — confirm modules.billing === false
  7. Open the web app — confirm Billing tab absent from Settings

  ## Re-enabling a disabled module on an existing product

  1. Set enabled=True in product.config.py and product.config.ts
  2. `make migrate` — module's tables created from existing migrations
  3. `make seed` — module's seed data created if applicable
  4. Redeploy

  ## Adding a new product-specific module

  1. `make new-module NAME=invoices`
  2. Add INVOICES = ModuleConfig(enabled=True) to product.config.py
  3. Register router conditionally in build_router() using is_enabled("invoices")
  4. Create migrations under alembic/versions/product/ with your prefix (see TEMPLATE.md)
  ```

- Update `docs/runbooks/phase1-verification.md` with a step 15:
  ```
  15. Toggle billing off, restart, verify /health/ready still 200 and Billing tab gone.
      Toggle back on, restart, verify /health/ready still 200 and Billing tab returns.
  ```

**Tests** — the parametrized matrix above covers this task's requirements

**Done when**

- [ ] All three config scenarios in the test matrix pass
- [ ] Runbook documents the toggle workflow for disabling and re-enabling
- [ ] `make new-module` generates a module that registers itself correctly in `build_router()`

---

## Q · Internationalisation foundation

> Why now: every task from Phase 2 onwards adds user-facing strings (notification copy, email templates, billing UI, file management). Establishing i18n here means each new string is added the right way from the start. Retrofitting i18n after Phase 2 means rewriting every email template, every notification body, and every UI string.

### T-067 Shared i18n package + key conventions

> needs: T-038 (shared packages exist), T-059 (product config)

**Goal** A single source-of-truth translation package consumed by backend, web, and mobile. English locale is fully populated; one additional locale (Finnish) seeded as a worked example.

**Build**

- New workspace package `packages/i18n/`:

  ```
  packages/i18n/
  ├── package.json              # name: @forge/i18n
  ├── locales/
  │   ├── en/                   # source of truth — every key starts here
  │   │   ├── common.json       # buttons, generic errors, generic UI
  │   │   ├── auth.json         # login, register, verify, reset, magic link, 2FA
  │   │   ├── tenancy.json      # org, members, invitations
  │   │   ├── billing.json      # plans, checkout, errors
  │   │   ├── notifications.json
  │   │   ├── emails.json       # subject + body keys for each template
  │   │   ├── errors.json       # DomainError → user message mapping
  │   │   └── validation.json   # form validation messages
  │   └── fi/                   # Finnish — worked example, ~30% complete is fine
  │       └── (same files)
  ├── src/
  │   ├── index.ts              # exports: SUPPORTED_LOCALES, DEFAULT_LOCALE,
  │   │                         #          loadLocale(), type Translations
  │   ├── keys.ts               # generated typed keys (script regenerates on build)
  │   └── load-python.py        # helper for backend to load locales/<code>/*.json
  └── scripts/
      └── generate-types.ts     # walks locales/en/, generates TypeScript key types
  ```

- Key conventions documented in `packages/i18n/README.md`:
  - Namespace by surface: `auth.login.email_label`, `emails.invitation.subject`
  - Interpolation uses ICU MessageFormat: `"You have {count, plural, one {1 message} other {# messages}}"`
  - Never use a bare English string as a key — keys describe location, values are content
  - Source-of-truth English keys are required before adding any other locale

- `SUPPORTED_LOCALES` is overridable in `product.config.py` and `product.config.ts`:

  ```python
  # product.config.py
  SUPPORTED_LOCALES = ["en", "fi"]        # control which locales ship in this product
  DEFAULT_LOCALE = "en"
  ```

  Locales not in the list are not bundled by web/mobile and not loadable by the backend.

- `scripts/generate-types.ts` walks `locales/en/` and emits a `keys.ts` exporting:

  ```typescript
  export type TranslationKey =
    | "common.button.save"
    | "common.button.cancel"
    | "auth.login.email_label";
  // ... auto-generated from every leaf key in locales/en/*.json
  ```

  This makes typos in translation keys a TypeScript error.

- Migration of existing strings: Phase 1 emails (`packages/email-templates` from T-106 if reached, else the Jinja templates from T-035) are scanned; every visible English string moves to `locales/en/emails.json`. The templates reference the keys, not the strings.

**Tests**

- `pnpm --filter @forge/i18n build` produces valid JSON for every locale
- `keys.ts` is regenerated and matches `locales/en/`
- A missing key in `fi` falls back to the `en` value (not the key string)
- Adding a key to `en` then running build → CI catches if `keys.ts` is stale
- ICU pluralisation: `t("messages.count", {count: 1})` → "1 message", `count: 5` → "5 messages"

**Done when**

- [ ] `@forge/i18n` builds cleanly
- [ ] Both `en` and `fi` locales present (fi may be partial — worked example, not full coverage)
- [ ] Type generation works and is wired into the turbo pipeline
- [ ] README documents the key-naming conventions

---

### T-068 Backend i18n — locale resolution + retrofit of Phase 1 strings

> needs: T-067, T-006, T-007

**Goal** Backend resolves the user's locale per request and uses it for every user-facing string (errors, emails, validation). Phase 1's hardcoded English is migrated to translation keys.

**Build**

- `backend/app/core/i18n.py`:

  ```python
  _translations: dict[str, dict[str, Any]] = {}   # loaded once at startup

  def load_translations() -> None:
      """Called from app lifespan startup.
      Reads packages/i18n/locales/<code>/*.json for every code in SUPPORTED_LOCALES.
      Merges all files in a locale into one nested dict."""

  def t(key: str, locale: str = DEFAULT_LOCALE, **params) -> str:
      """Lookup with dot-notation, ICU interpolation, fallback to en.
      Raises KeyError only if the key is missing in both target and en
      (this is a developer error — keys must exist in en first)."""

  def get_supported_locales() -> list[str]:
      """Returns list from product.config — frontend reads this via /config endpoint."""
  ```

- `backend/app/middleware/locale.py`:

  ```python
  async def resolve_locale(request: Request, call_next):
      """Resolves locale in this order, attaches to request.state.locale:
      1. request.state.user.locale if authenticated and set
      2. Accept-Language header, matched against SUPPORTED_LOCALES (use babel.negotiate_locale)
      3. DEFAULT_LOCALE
      """
  ```

  Registered after auth middleware so user.locale takes precedence when available.

- New dependency `get_locale(request: Request) -> str` for use in routers.

- `DomainError` extended to carry a translation key, not a hardcoded English message:

  ```python
  class DomainError(Exception):
      status_code: int = 400
      code: str = "domain_error"
      message_key: str | None = None   # e.g. "errors.plan_limit_reached"

      def __init__(self, message_key: str | None = None,
                   *, fallback: str = "An error occurred",
                   details: dict | None = None): ...
  ```

  The global exception handler from T-006 now calls `t(error.message_key, locale=request.state.locale)` and falls back to `error.fallback` if the key is missing.

- Update T-006 subclasses: `PlanLimitError("errors.plan_limit_reached", fallback="Plan limit reached")` etc. All existing English literals retrofit to keys defined in `locales/en/errors.json`.

- Email templates updated:
  - If T-106 (React Email) is in scope: each template component receives a `locale` prop and reads its strings from `@forge/i18n`
  - If Phase 1 used Jinja templates: each Jinja file is split per locale, or the body becomes `{{ t("emails.invitation.body", inviter_name=name) }}` style
  - Email job (T-035) reads `recipient.locale` and selects the matching template/key set before rendering

- `GET /api/v1/config` (from T-063) extended to return `supported_locales: list[str]` and `default_locale: str` so the frontend knows which language switcher options to render.

- Backfill: every email template subject/body, every `DomainError` raise, every validation message in Pydantic schemas (use `Field(... description="users.email.invalid")` pattern or a translation hook on the global validation handler) — all converted.

**Tests**

- `resolve_locale` middleware: authenticated user with `locale="fi"` → `request.state.locale == "fi"`
- `Accept-Language: fi,en;q=0.5` → resolves to `fi` if `fi` in SUPPORTED_LOCALES, else `en`
- `Accept-Language: de` (not supported) → falls back to default
- `DomainError("errors.plan_limit_reached")` raised under `locale=fi` → response message in Finnish
- `DomainError("errors.unknown_key")` → falls back to `fallback` string, logs a warning
- Email job: user with `locale="fi"` receives invitation email with Finnish subject + body
- `GET /api/v1/config` returns the supported locales list
- 100% coverage on `i18n.py` and the locale middleware

**Done when**

- [ ] All Phase 1 user-facing strings live in `@forge/i18n` keys
- [ ] No hardcoded English in error messages, email templates, or validation messages
- [ ] Locale resolution test matrix passes
- [ ] Email job sends localised emails based on `recipient.locale`
- [ ] `mypy --strict` clean

---

### T-069 Web i18n — react-i18next + language switcher + retrofit

> needs: T-067, T-043, T-044

**Goal** The web app reads translations from `@forge/i18n`, switches language live, persists the user's choice, and Phase 1 UI strings are migrated.

**Build**

- `apps/web/src/i18n.ts`:
  - Initialise `react-i18next` with `@forge/i18n` as the resource source
  - Backend `/config` response provides the supported locale list at runtime
  - Detection order: explicit user choice in localStorage → user's profile `locale` → browser `navigator.language` → default
  - ICU plugin (`i18next-icu`) for plural/select support
  - Suspense mode disabled — locale resources are bundled, not lazy-loaded for the first locale; additional locales lazy-load on switch

- `packages/hooks/src/useLocale.ts`:

  ```typescript
  export function useLocale(): {
    locale: string;
    supportedLocales: string[];
    setLocale: (locale: string) => Promise<void>; // updates User.locale + i18n
  };
  ```

  Persists by calling `PATCH /api/v1/me { locale }` so the choice survives device changes. No JSX in this package (lint rule).

- Language switcher component in `packages/ui/`:

  ```tsx
  <LocaleSwitcher /> // dropdown of supported locales with native names
  ```

- Settings → Profile page: language preference dropdown using `<LocaleSwitcher />`.

- Retrofit pass on Phase 1 web surfaces:
  - All auth pages (T-043): every label, button, error message replaced with `t("auth.login.email_label")` etc.
  - App shell, settings tabs, navigation labels (T-044)
  - Form validation messages: react-hook-form resolvers updated to return translation keys, rendered via `t()` at the form level
  - Error toasts mapping `envelope.error.code` → `t("errors.<code>")`

- `apps/web/eslint.config.ts`: enable `react/jsx-no-literals` for JSX children (with the `// i18n-ok` escape hatch documented).

**Tests**

- Vitest + RTL: render login page → assert `screen.getByLabelText(t("auth.login.email_label", { lng: "en" }))`
- **First-visit detection:** mock `navigator.language = "fi-FI"`, render the app with no localStorage and no auth → app initialises in Finnish; `<html lang="fi">` attribute set correctly
- **Unsupported system language falls back:** mock `navigator.language = "de-DE"` with `SUPPORTED_LOCALES = ["en", "fi"]` → app initialises in English (DEFAULT_LOCALE)
- **Authenticated user override:** `navigator.language = "fi-FI"` but `User.locale = "en"` → English wins (explicit choice beats automatic detection)
- Locale switch: change to `fi` → input label re-renders in Finnish, `PATCH /api/v1/me` called with `{ locale: "fi" }`
- A11y: axe-core clean on language switcher (`lang` attribute updates on `<html>`)
- Lint guard: a deliberate `<button>Save</button>` literal in test fixture fails lint

**Done when**

- [ ] All Phase 1 web auth + settings strings live in `@forge/i18n`
- [ ] Language switcher works end-to-end against the backend
- [ ] User's choice persists across sessions and devices (stored on `User.locale`)
- [ ] `react/jsx-no-literals` lint enabled and passing
- [ ] axe-core clean

---

### T-070 Mobile i18n — expo-localization + retrofit

> needs: T-067, T-052

**Goal** Mobile auto-detects device locale on first launch, allows manual override in settings, and shares the exact same translation keys as web.

**Build**

- `apps/mobile/src/lib/i18n.ts`:
  - Same `react-i18next` setup as web, with mobile-specific detector
  - First-launch flow: `expo-localization.getLocales()[0].languageCode` → match against `SUPPORTED_LOCALES` from `/config` → fallback to `DEFAULT_LOCALE`
  - Subsequent launches: read from `expo-secure-store` (user has logged in → use their `User.locale`; not logged in → use the previously-selected locale stored in `AsyncStorage`)
  - Re-renders on system language change via `expo-localization` listener

- Shared `useLocale()` hook from `packages/hooks` works on mobile unchanged — the underlying i18n instance is platform-specific but the API is identical. This is why the hook went to the shared package in T-069.

- Settings screen: language picker that calls `useLocale().setLocale(...)`.

- Retrofit pass on Phase 1 mobile screens (T-047, T-052): every visible string moves to `@forge/i18n` keys.

- Push notification body: backend already sends localised content (per T-068's `recipient.locale` lookup), so no client-side change needed — the notification arrives pre-translated.

**Tests**

- Mock `expo-localization` returning `fi` → on first launch i18n initialises in Finnish
- User logs in with `User.locale = "en"` → app re-renders in English (user preference overrides device default)
- Mocked system language change event → app re-renders
- React Native ESLint rule (`react-native/no-raw-text` with allowlist for the `<Text>` component) catches literal text in non-Text components

**Done when**

- [ ] All Phase 1 mobile screens use `@forge/i18n` keys
- [ ] Device locale detected on first launch
- [ ] User profile locale overrides device locale after login
- [ ] Language picker in mobile settings works end-to-end

---

## Phase 1.5 complete when ALL of the following are true

- [ ] T-057 (Phase 1 verification) remains green — Phase 1.5 must not regress anything
- [ ] `billing=False` configuration passes the T-066 module-toggle matrix
- [ ] `GET /api/v1/config` reflects the active modules correctly and returns `supported_locales`
- [ ] Disabled module routes return 404 and are absent from OpenAPI schema
- [ ] Disabled module tables are absent from the DB schema
- [ ] Frontend shows no nav items, tabs, or buttons for disabled modules
- [ ] App starts with no Stripe keys when `billing=False`
- [ ] `TEMPLATE.md` and `CHANGELOG.md` checked in; `make update-from-template` works
- [ ] `docs/runbooks/module-toggle-guide.md` complete
- [ ] All Phase 1 user-facing strings (errors, emails, web UI, mobile UI) live in `@forge/i18n`
- [ ] Language switcher works end-to-end on web; persists to `User.locale`
- [ ] Mobile detects device locale on first launch
- [ ] Email job sends localised emails based on recipient's locale
- [ ] `jsx-no-literals` lint rule active and passing
- [ ] `pytest --cov` thresholds not regressed (85% overall, 100% on core modules)
- [ ] CI green on `main`

---

# Phase 2 — Platform features

> HTML ref: §"Build phases" → "Phase 2"
>
> **Prerequisite:** T-057 (Phase 1 end-to-end verification) is complete and green. Do not start Phase 2 until the Phase 1 acceptance flow runs without intervention.
>
> **Phase 2 acceptance criterion:** A user can (1) receive in-app + email + push notifications for member-invited and payment-failed events, (2) upload and download a file via presigned URLs with virus scanning, (3) see their plan's feature flags drive UI gating in real time, (4) experience the full billing lifecycle including dunning and trial-expiry warnings, (5) a superadmin can impersonate a user and override a plan, and (6) every `packages/ui` component renders in Storybook. Each acceptance path has integration tests.

---

## P2-A · Notifications

> HTML ref: §"Notifications" (entire section)
>
> Architectural rule: one `Notification` model, three delivery channels. The service decides channels from user preferences. Adding a new channel later (SMS, Slack) is one new delivery method on the service — no model changes.

### T-101 Notification + PushToken models + migration

> HTML ref: §"Notifications" → "Data model"
> needs: T-010, T-025

**Goal** Two new tables: `Notification` (the notification record itself) and `PushToken` (mobile device tokens for push delivery).

**Build**

- `backend/app/modules/notifications/models.py`:

  ```python
  class Notification(SQLModel, table=True):  # UUIDPKMixin + TimestampMixin + OrgScopedMixin
      user_id: UUID = Field(foreign_key="user.id", index=True)
      type: str = Field(index=True)          # "member_invited", "payment_failed", "weekly_summary"
      title: str
      body: str
      action_url: str | None = None
      channels: list[str] = Field(sa_column=Column(JSONB))  # ["email", "in_app", "push"]
      read_at: datetime | None = None
      email_sent_at: datetime | None = None
      push_sent_at: datetime | None = None
      # NOTE: org_id is on every notification — even system-wide ones get an org

  class PushToken(SQLModel, table=True):  # UUIDPKMixin + TimestampMixin + SoftDeleteMixin
      user_id: UUID = Field(foreign_key="user.id", index=True)
      token: str = Field(unique=True)        # Expo push token
      platform: Literal["ios", "android"]
      device_label: str | None = None
      last_used_at: datetime
  ```

- Composite index on `(user_id, read_at)` for the "unread count" query (extremely hot path)
- Composite index on `(org_id, user_id, created_at DESC, id DESC)` for cursor pagination
- Migration: `alembic revision --autogenerate -m "notifications_tables"`

**Tests**

- Migration round-trip
- `PushToken.token` unique constraint enforced
- Insertion of two notifications for same user → both queryable

**Done when**

- [ ] Migration applies both directions cleanly
- [ ] Indexes present (assert via `pg_indexes` query in test)
- [ ] `mypy --strict` clean

---

### T-101.5 Notification preferences on User

> HTML ref: §"Notifications" → "User preferences"
> needs: T-013, T-101

**Goal** `User.notification_preferences` JSONB already exists from T-013; this task defines the schema and migration to backfill defaults.

**Build**

- `backend/app/modules/notifications/preferences.py`:

  ```python
  DEFAULT_PREFERENCES: dict[str, dict[str, bool]] = {
      "member_invited":    {"email": True,  "in_app": True,  "push": False},
      "payment_failed":    {"email": True,  "in_app": True,  "push": True},
      "subscription_expiring": {"email": True, "in_app": True, "push": False},
      "weekly_summary":    {"email": True,  "in_app": False, "push": False},
      "file_uploaded":     {"email": False, "in_app": True,  "push": False},
  }

  def channels_for(prefs: dict, notification_type: str) -> list[str]:
      """Return enabled channels for this user + this notification type.
      Falls back to DEFAULT_PREFERENCES if the user has not customised."""
  ```

- Pydantic schema `NotificationPreferencesUpdate` for the settings page
- Idempotent backfill job `backfill_notification_preferences(ctx)` in `app/workers/jobs/`:
  - Iterate users with empty `notification_preferences`
  - Set to `DEFAULT_PREFERENCES`
  - Run via `make seed` and once on first deploy

**Tests**

- `channels_for({}, "payment_failed")` returns `["email", "in_app", "push"]` (defaults)
- User opts out of email for `payment_failed` → `channels_for(prefs, "payment_failed")` returns `["in_app", "push"]`
- Backfill idempotent: running twice produces no extra writes

**Done when**

- [ ] Backfill job tests pass
- [ ] All notification types from §"User preferences" represented

---

### T-102 NotificationService + email/in-app/push fan-out

> HTML ref: §"Notifications" → "Delivery channels"
> needs: T-035, T-101, T-101.5

**Goal** One service method enqueues a notification; channel fan-out happens inside the service driven by user preferences.

**Build**

- `backend/app/modules/notifications/repository.py`:
  - `NotificationRepository(session, org_id)` extends `BaseRepository[Notification]`
  - `list_for_user(user_id, after_cursor, limit, unread_only: bool = False) -> tuple[list[Notification], cursor]`
  - `mark_read(notification_id, user_id) -> Notification` — scoped to user_id, raises `NotFoundError` cross-user
  - `mark_all_read(user_id) -> int` — returns count updated
  - `unread_count(user_id) -> int` — cached in Redis with key `notif:unread:{user_id}`, 30 s TTL, invalidated on read/create
  - `PushTokenRepository(session)` (not org-scoped — user-scoped):
    - `register(user_id, token, platform, device_label) -> PushToken` (upsert by token; updates `last_used_at`)
    - `list_active_for_user(user_id) -> list[PushToken]`
    - `deactivate(token) -> None` — soft-delete (Expo returns "DeviceNotRegistered" for revoked tokens)

- `backend/app/modules/notifications/service.py`:
  - `NotificationService.send(self, *, user_id, org_id, type, title, body, action_url=None) -> Notification`:
    1. Load user preferences via `channels_for(user.notification_preferences, type)`
    2. Create `Notification` row with the resolved channels
    3. If `"email"` in channels → enqueue `send_email_job` with template `notification_<type>`
    4. If `"push"` in channels → enqueue `send_push_job(user_id, notification_id)`
    5. If `"in_app"` in channels → publish to Redis pub/sub channel `notif:user:{user_id}` (T-103 SSE consumer listens here)
    6. Return the `Notification` row
  - **Idempotency:** accepts optional `idempotency_key: str | None` — if provided, attempts to find an existing `Notification` with the same key in the past 24 h; if found, no-op. Prevents duplicate notifications from webhook retries.
  - All channel dispatch is fire-and-forget — service success does not depend on delivery success
  - Audit log entry on every send (`action="notification.sent"`, includes channels and type)

**Tests**

- User with email-only preference → only `send_email_job` enqueued, no push job
- User with push enabled but no `PushToken` → push job runs but no-ops gracefully
- Cross-tenant: `mark_read` for a notification owned by org B from a session in org A → `NotFoundError`
- Idempotency: two calls with same `idempotency_key` within 24h → one `Notification` row created
- `unread_count` cache hit on second call (assert no DB query via query-count fixture)
- 100% coverage on `NotificationService`

**Done when**

- [ ] All fan-out tests pass
- [ ] Idempotency test passes
- [ ] 100% coverage on `service.py`

---

### T-103 SSE in-app notification stream

> HTML ref: §"Notifications" → "In-app (SSE)"
> needs: T-102

**Goal** A single SSE endpoint streams notifications to the user's open browser tabs in real time.

**Build**

- `backend/app/modules/notifications/sse.py`:
  - `GET /api/v1/notifications/stream` (auth required via JWT):
    - Subscribes to Redis pub/sub channel `notif:user:{user_id}`
    - Yields SSE events: `event: notification\ndata: <json>\n\n`
    - Sends a `: ping` comment every 30 s to keep the connection alive (browsers + load balancers drop idle connections)
    - On client disconnect, unsubscribes cleanly (use `asyncio.CancelledError` handler)
    - Per-user concurrency cap of 5 connections (a Redis counter; reject 6th with 429)
    - Total connection cap from settings `SSE_MAX_CONNECTIONS=10000` to bound memory
  - `GET /api/v1/notifications` — cursor-paginated list (uses repo's `list_for_user`)
  - `PATCH /api/v1/notifications/{id}/read` — mark single read
  - `POST /api/v1/notifications/read-all` — mark all read
  - `GET /api/v1/notifications/unread-count` — returns `{ count: N }` (cached)
  - `PATCH /api/v1/me/notification-preferences` — update preferences

**Tests**

- SSE: publish to Redis channel → connected client receives the event within 1 s
- Reconnect: after disconnect, no orphan subscription remains (assert Redis pubsub count)
- 6th connection from same user → 429
- Mark-read invalidates the unread-count cache
- Rate-limited: 100/min on PATCH endpoints

**Done when**

- [ ] SSE test green using an in-process Redis publisher
- [ ] Connection cap test passes
- [ ] All endpoints in standard envelope format

---

### T-104 Web notification center

> HTML ref: §"React web app" → "Notification center + SSE feed"
> needs: T-103, T-044

**Goal** Bell icon in the topbar with unread count, dropdown feed, mark-read, mark-all-read, and the SSE subscription.

**Build**

- `apps/web/src/features/notifications/`:
  - `NotificationBell.tsx`: lucide-react `Bell` icon + numeric badge; opens the dropdown
  - `NotificationFeed.tsx`: virtualized list of last 50 notifications (TanStack Virtual); infinite scroll for older via cursor; each row: title, body, relative time (`formatDistanceToNow`), action button if `action_url` set
  - `EmptyState`, `LoadingState`, `ErrorState` components (per Phase 1 four-states rule)
- `apps/web/src/lib/sse.ts`: typed EventSource wrapper
  - Auto-reconnect with exponential backoff (1s, 2s, 4s, 8s, max 30s)
  - On reconnect, invalidate the notifications query so any events missed during disconnect are fetched
  - Pause when document hidden (Page Visibility API) — saves a connection for inactive tabs
- `@forge/hooks/useNotifications.ts`:
  - Returns `{ notifications, unreadCount, markRead, markAllRead, isConnected }`
  - Uses TanStack Query for the initial fetch and for cursor pagination
  - SSE events trigger `queryClient.setQueryData` to insert new notifications optimistically + invalidate unread count
- Settings page tab "Notifications": editable preferences matrix (per type × per channel checkboxes) using `PATCH /api/v1/me/notification-preferences`
- Toast on new notification (sonner) — only when document is visible

**Tests** (Vitest + RTL + MSW)

- Bell renders unread count
- New notification via mocked SSE event appears at top
- Mark-read clears badge; mark-all-read clears the list visually
- A11y: `axe-core` clean on the dropdown
- Preferences page: toggle email off → API called with correct payload

**Done when**

- [ ] All tests pass
- [ ] Bell + dropdown work end-to-end against the local backend
- [ ] No JSX in `@forge/hooks` (lint check)

---

### T-105 Mobile push token registration + handler

> HTML ref: §"React Native" → "Push notifications"
> needs: T-102, T-047

**Goal** Mobile app registers an Expo push token on first authenticated launch and handles received pushes.

**Build**

- `apps/mobile/src/lib/push.ts`:
  - `async function registerPushToken(): Promise<void>`:
    - Request permission via `expo-notifications`
    - If denied, exit (no error — user choice)
    - Get token via `Notifications.getExpoPushTokenAsync({ projectId })`
    - POST to `/api/v1/notifications/push-tokens` with `{ token, platform, device_label: Device.modelName }`
    - Store last-registered token in `expo-secure-store` to skip re-registration if unchanged
  - `setupNotificationHandlers()`:
    - Foreground: show in-app banner via custom component (Expo's default handler is suppressed)
    - Tap on background notification: read `data.action_url` → navigate via Expo Router
    - Token refresh listener: re-register on token change
- Call `registerPushToken()` in the `(app)/_layout.tsx` mount effect (only when user is authenticated)
- `apps/mobile/src/features/notifications/`: in-app notification list screen mirroring the web feed

**Tests**

- Mock `expo-notifications`: token retrieval → POST called with correct payload
- Permission denied → no POST, no error
- Tap navigation: notification with `action_url: "/_app/settings/billing"` → router pushes the route
- Token refresh: simulated event → second POST with new token

**Done when**

- [ ] All push tests pass
- [ ] Manual smoke: physical iOS + Android device receives a test push from the backend

---

### T-106 Email templates package (React Email)

> HTML ref: §"Notifications" → "Email (Resend)"
> needs: T-035

**Goal** Replace the Jinja2 templates from T-035 with type-safe React Email components, one per notification type. Better preview tooling, shared design tokens with the marketing site (Phase 4).

**Build**

- New workspace package `packages/email-templates`:
  - Deps: `@react-email/components`, `@react-email/render`
  - One component per template, all in `src/templates/`:
    - `VerifyEmail.tsx`, `PasswordReset.tsx`, `MagicLink.tsx`, `Invitation.tsx`, `Welcome.tsx` (existing — migrated from Jinja)
    - `MemberInvited.tsx`, `PaymentFailed.tsx`, `SubscriptionExpiring.tsx`, `TrialEnding.tsx`, `FileUploaded.tsx` (new for Phase 2)
  - Shared `BaseLayout.tsx`: header, footer, brand colors from `packages/ui/tokens`
  - Render utility: `renderEmail(template: string, props: object) -> { html: string, text: string }`
- Backend integration:
  - `app/workers/jobs/email.py` updated: instead of Jinja, call a small Node subprocess (via `nodejs` package in the worker image) that runs `pnpm --filter @forge/email-templates render <name> <json>` and returns rendered HTML
  - Cache rendered output per `(template, props_hash)` for 1 h to avoid re-rendering identical emails
- Storybook entries (under T-150) for every template
- Preview server: `pnpm --filter @forge/email-templates dev` serves rendered previews at `localhost:3030` with hot reload

**Tests**

- Each template renders without error for example props
- HTML output passes a basic email-client compatibility check (no flex/grid, table-based layout, inline styles)
- Plain-text fallback present and non-empty

**Done when**

- [ ] All 10 templates render
- [ ] Email worker switched from Jinja to React Email
- [ ] Old Jinja templates deleted (no dead code)
- [ ] Preview server documented in module README

---

## P2-B · File storage

> HTML ref: §"File storage" (entire section)
>
> Architectural rule: **files never proxy through the API**. Clients upload directly to object storage via presigned URLs. The API only manages metadata.

### T-110 StoredFile model + migration

> HTML ref: §"File storage" → "StoredFile model"
> needs: T-010, T-025

**Goal** Single table tracking all file metadata, with virus-scan status and soft delete.

**Build**

- `backend/app/modules/storage/models.py`:
  ```python
  class StoredFile(SQLModel, table=True):  # UUIDPKMixin + TimestampMixin + SoftDeleteMixin + OrgScopedMixin
      uploaded_by: UUID = Field(foreign_key="user.id", index=True)
      key: str = Field(unique=True, index=True)   # storage object key (org_id-prefixed)
      filename: str
      content_type: str = Field(index=True)
      size_bytes: int
      checksum_sha256: str | None = None          # set after confirm
      status: Literal["pending", "ready", "infected", "failed", "deleted"] = "pending"
      virus_scan_result: str | None = None
      # categorisation for plan-quota counting
      bucket: str = "default"                     # logical bucket: "default", "avatars", "exports", ...
  ```
- Composite index `(org_id, status, created_at DESC, id DESC)` for cursor lists
- Composite index `(org_id, uploaded_by)` for "files uploaded by me"
- Migration: `alembic revision --autogenerate -m "stored_files"`

**Tests**

- Migration round-trip
- `key` unique enforced across orgs (key includes org_id prefix so collisions impossible)

**Done when**

- [ ] Migration clean both directions
- [ ] Indexes verified

---

### T-111 StorageBackend protocol + S3 / R2 / MinIO implementations

> HTML ref: §"File storage" → "Storage backend interface"
> needs: T-003

**Goal** A single `StorageBackend` interface with three implementations selected by `settings.STORAGE_BACKEND`.

**Build**

- `backend/app/modules/storage/backend.py`:

  ```python
  @dataclass(frozen=True)
  class PresignedUpload:
      url: str
      fields: dict[str, str]       # POST form fields for the upload
      key: str
      expires_at: datetime

  class StorageBackend(Protocol):
      async def presign_upload(
          self, key: str, content_type: str, max_bytes: int, expires_in: int = 600
      ) -> PresignedUpload: ...
      async def presign_download(
          self, key: str, expires_in: int = 3600, *, filename: str | None = None
      ) -> str: ...
      async def head(self, key: str) -> dict | None: ...   # None if not found
      async def delete(self, key: str) -> None: ...
      async def exists(self, key: str) -> bool: ...
  ```

- Implementations in `backend/app/modules/storage/backends/`:
  - `s3.py` — `S3Backend(bucket, region)` using `aioboto3`
  - `r2.py` — extends `S3Backend` with R2's endpoint and signature config (R2 is S3-compatible but needs `signature_version="s3v4"` and `endpoint_url` from settings)
  - `minio.py` — same as S3 but with `endpoint_url=settings.AWS_ENDPOINT_URL` and `addressing_style="path"`
- Presigned upload **enforces server-side constraints**:
  - `Content-Length` range condition: `["content-length-range", 0, max_bytes]`
  - `Content-Type` equality
  - Server-side encryption header required (`x-amz-server-side-encryption: AES256`) on S3/R2
- Factory `get_storage_backend() -> StorageBackend` selected by `settings.STORAGE_BACKEND`, cached at module level
- Object keys are namespaced: `<org_id>/<bucket>/<uuid>/<filename>` — makes per-tenant access policies straightforward in S3/R2

**Tests** (using MinIO via testcontainers)

- Round-trip presign upload → PUT bytes → `head` returns metadata
- `delete` removes the object; subsequent `exists` returns False
- Presigned URL expires (use `freezegun` to advance time)
- Oversized PUT (over `max_bytes`) → S3 rejects with 400
- Wrong content-type → S3 rejects

**Presigned URL security requirements** — these are non-negotiable and must each have a dedicated test asserting the wire format, not just the happy path:

- [ ] **The `content-length-range` policy condition is signed into the URL**, not enforced only by application logic. Tampering with the form field after the URL is generated must cause S3 to reject the upload. Test: generate URL with `max_bytes=1024`; attempt PUT of 2048 bytes; assert S3 returns 400.
- [ ] **The `Content-Type` condition is signed as an exact match**, never a wildcard or prefix. `image/jpeg` is acceptable; `image/*` is forbidden. Test: generate URL for `image/jpeg`; attempt PUT with `Content-Type: application/octet-stream`; assert S3 returns 400.
- [ ] **The `x-amz-server-side-encryption: AES256` header is signed**, not optional. An upload attempt without this header must be rejected by S3. Test: generate URL; strip the encryption header from the PUT; assert S3 returns 400.
- [ ] **The exact object key is bound to the signature** — the client cannot upload to any key other than the one your service generated. Test: generate URL for key `org-a/files/uuid-1/x.jpg`; attempt PUT to `org-b/files/uuid-2/y.jpg`; assert S3 returns 403.
- [ ] **The expiry is short and configurable**, defaulting to 600 seconds (10 min) and never exceeding 3600 (1 h) for uploads. Test: `freezegun` to 11 min in the future; attempt PUT; assert S3 returns 403.

**Done when**

- [ ] Backend tests green against real MinIO
- [ ] All three implementations share the same test suite via fixture parametrisation
- [ ] All five presigned URL security tests above pass on every backend implementation
- [ ] 100% coverage on `backend.py` and each implementation

---

### T-112 Presign + confirm endpoints (plan-gated)

> HTML ref: §"File storage" → "Upload flow"
> needs: T-110, T-111, T-031

**Goal** Two endpoints that orchestrate the three-step upload flow; storage quota enforced from plan features.

**Build**

- `backend/app/modules/storage/repository.py`:
  - `StoredFileRepository(session, org_id)` extends `BaseRepository`
  - `total_bytes_for_org(self) -> int` — sum of `size_bytes` where `status="ready"` (cached 60s in Redis with explicit invalidation on file create/delete)
  - `count_files_for_org(self) -> int`
- `backend/app/modules/storage/service.py`:
  - `StorageService.presign_upload(self, user, *, filename, content_type, size_bytes, bucket="default") -> dict`:
    1. Validate content_type against allowlist (default: images, PDFs, common docs; configurable per bucket)
    2. Validate `size_bytes <= plan.features.max_upload_mb * 1_000_000` (per-file cap)
    3. Validate `total_bytes_for_org + size_bytes <= plan.features.storage_gb * 1_000_000_000` (org quota)
    4. Generate `key = f"{org_id}/{bucket}/{uuid4()}/{slugify(filename)}"`
    5. Create `StoredFile` row with `status="pending"`
    6. Call `backend.presign_upload(key, content_type, max_bytes=size_bytes + 1024)`
    7. Audit log: `file.presigned`
    8. Return `{ file_id, upload_url, fields, key, expires_at }`
  - `StorageService.confirm_upload(self, user, file_id) -> StoredFile`:
    1. Load `StoredFile` (org-scoped — cross-tenant impossible)
    2. Reject if `status != "pending"` (idempotent: re-confirming a `ready` file returns the file unchanged)
    3. `backend.head(key)` — verify file exists, get `Content-Length` and ETag
    4. Reject if actual size > claimed `size_bytes` by more than 1 KB tolerance
    5. Set `status="ready"`, `checksum_sha256` (from ETag if S3 single-part)
    6. Enqueue `virus_scan_job(file_id)`
    7. Audit log: `file.uploaded`; emit `Notification(type="file_uploaded")` if user prefers
- `backend/app/modules/storage/router.py`:
  - `POST /api/v1/files/presign` — request body `{ filename, content_type, size_bytes, bucket? }` → presigned upload
  - `POST /api/v1/files/{id}/confirm` — confirm after upload completes
  - `GET /api/v1/files` — cursor list (org-scoped, optionally filtered by bucket)
  - `GET /api/v1/files/{id}/download` — returns `{ url, expires_at }` via `presign_download`; requires `FILES_READ`
  - `DELETE /api/v1/files/{id}` — soft-delete row, enqueue hard delete from storage (with 7d delay for accidental-delete recovery); requires `FILES_MANAGE`
- All endpoints use `require_feature("storage_gb")` to deny on free plans without storage

**Tests**

- Cross-tenant: org B cannot confirm or download a file owned by org A → 404
- Quota: presign request that would exceed `plan.features.storage_gb` → 402 `PlanLimitError`
- Per-file size cap: oversized request → 402
- Confirm idempotent: confirming a `ready` file twice → 200 both times
- Confirm rejects if actual size != claimed size
- Content-type allowlist enforced (forbid `application/x-executable`)
- Permission: MEMBER can read files, only FILES_MANAGE can delete
- 100% coverage on `service.py`

**Orchestration-layer security requirements** — these belong to the service that calls the storage backend, not the backend itself. Each must have a dedicated test:

- [ ] **Object keys are generated server-side with a UUID component**, never accepted from the client. A presign request with a client-supplied `key` parameter must ignore it or reject the request. Test: POST presign with `{"key": "evil-path/file.jpg"}`; assert the returned key matches `{org_id}/{bucket}/{uuid4}/{filename}` pattern, not the client's input.
- [ ] **Filename is treated as a display-only string with no authorisation weight.** It is `slugify`'d for the storage key but the UUID component is what makes the key unique and safe. Test: two users in the same org upload `report.pdf`; both succeed with different keys (different UUIDs).
- [ ] **Confirm step verifies file existence in storage** via `backend.head(key)`, not by trusting the client to report success. Test: skip the actual PUT, then call confirm; assert it returns an error and the row stays `status="pending"`.
- [ ] **Confirm step verifies actual file size** matches the claimed `size_bytes` within a small tolerance (1 KB for header overhead). Test: claim 1024 bytes; upload 2048 bytes; assert confirm returns 422 and the storage object is deleted.
- [ ] **Cross-tenant presigned URL reuse is impossible** — a presigned URL generated for org A's prefix cannot be used to upload into org B's prefix even by a member of org B with their own valid session. Test: extract URL from org A's presign response; authenticate as a user in org B; attempt PUT; assert S3 rejects via signature mismatch on key.

**Done when**

- [ ] All tests green
- [ ] Cross-tenant isolation sweep (T-049) extended to include file endpoints
- [ ] All five orchestration-layer security tests above pass
- [ ] Audit log entries verified for presign, upload, delete

---

### T-113 Virus scan job

> HTML ref: §"File storage" → "Upload flow" (virus_scan_job step)
> needs: T-112, T-034

**Goal** Async virus scan that updates `StoredFile.status` to `"infected"` or `"ready"`.

**Build**

- `backend/app/workers/jobs/virus_scan.py`:
  - `virus_scan(ctx, *, file_id: UUID) -> None`:
    1. Load `StoredFile`; abort if `status != "ready"` (already scanned or deleted)
    2. Generate a presigned **download** URL valid for the scan window
    3. Call the configured scanner (see below)
    4. On clean: update `virus_scan_result="clean"`, leave `status="ready"`
    5. On infected: set `status="infected"`, `virus_scan_result=<signature>`; **delete the underlying object immediately** from storage; emit `Notification(type="file_infected")` to uploader + AuditEvent
    6. On scanner error: log + retry up to 3× with exponential backoff; final failure sets `status="failed"`, audit-logged
- Pluggable scanner interface (Protocol) with two implementations:
  - `ClamAVScanner`: connects to `clamd` over Unix socket / TCP; for self-hosted
  - `VirusTotalScanner`: stub using VT API for SaaS workflows (rate-limited, file-hash first)
  - Selection via `settings.VIRUS_SCANNER: Literal["clamav", "virustotal", "noop"] = "noop"`
  - `"noop"` is the default for dev — marks every file `"clean"` immediately
- Add `clamav` service to `docker-compose.prod.yml` (optional, behind a profile)

**Tests**

- Mock scanner returns "clean" → file stays `status="ready"`, `virus_scan_result="clean"`
- Mock scanner returns "infected" → file `status="infected"`, object deleted from storage, notification sent
- Scanner exception → retried 3× then `status="failed"`
- 100% coverage on `virus_scan.py`

**Done when**

- [ ] All scanner outcomes tested
- [ ] Infected files are removed from storage (not just flagged)

---

### T-114 Web file upload component

> HTML ref: §"File storage" (Web feature implied)
> needs: T-112, T-041

**Goal** Reusable dropzone component in `@forge/ui` that handles the full three-step flow.

**Build**

- `packages/ui/src/components/FileUploader.tsx`:
  - Props: `bucket?, accept?, maxSize?, onUploaded(file: StoredFileDTO) => void`
  - Drag-and-drop zone + click-to-browse
  - For each file: call `/files/presign` → PUT to `upload_url` with progress → call `/files/{id}/confirm`
  - Per-file progress bar (XHR `upload.onprogress`)
  - Cancellable: pending uploads can be aborted; in-flight calls cancellation token
  - Concurrency: max 3 simultaneous uploads (FIFO queue for the rest)
  - Error states: validation rejection (size/type), upload failure (network), confirm failure (size mismatch)
  - A11y: keyboard-accessible (Tab focus, Enter to open picker, screen-reader announcements of progress)
- `apps/web/src/features/files/`:
  - `FilesPage` — list of org's files (uses cursor pagination from `useCursorPagination` shared hook)
  - File row: filename, size (formatBytes), uploader avatar, status badge, download / delete actions
  - Empty / loading / error states
- Avatar upload in Settings → Profile uses the same uploader with `bucket="avatars"` and `accept="image/*"`

**Tests** (Vitest + RTL + MSW)

- Drop file → presign called with correct content-type and size
- Successful upload triggers `onUploaded` callback
- Validation error (file too large) → reject before presign called
- Progress events update the bar
- A11y: axe-core clean on file picker open

**Done when**

- [ ] FileUploader storybook entry exists (T-150)
- [ ] Avatar upload works end-to-end
- [ ] Files page renders, downloads, deletes

---

### T-115 Mobile file picker

> needs: T-112, T-047

**Goal** Mobile app can upload from camera roll, document picker, or in-app camera.

**Build**

- `apps/mobile/src/lib/uploads.ts`:
  - `pickAndUpload(options: { source: "camera" | "library" | "document", bucket?: string })` — orchestrates picker + presign + PUT + confirm
  - Permissions handled via `expo-image-picker` / `expo-document-picker`
  - Progress via `FileSystem.createUploadTask` (Expo's task supports progress callbacks)
- `apps/mobile/src/features/files/`:
  - List screen mirroring web
  - Upload button opens a sheet with three options: camera, library, document
- Avatar upload in Profile settings: source = library, bucket = "avatars"

**Tests**

- Mocked picker returning a fake URI → presign + PUT + confirm sequence runs
- Cancellation: aborting partway leaves no orphaned `StoredFile` row (call `DELETE /files/{id}` on abort)

**Done when**

- [ ] All three pick sources work on iOS + Android simulators

---

## P2-C · Feature flags

> HTML ref: §"Best practices" → "DB-backed flags + plan features dict"
>
> Architectural rule: two-layer flags. Plan features come from `Plan.features` (set by us, gated by billing). Per-org overrides come from `OrgFeatureFlag` (set by us via admin, or by the org's owner if exposed in settings).

### T-120 FeatureFlagService with Redis cache

> HTML ref: §"Multi-tenancy" → "Feature flags" | §"Billing" → "Plan-gating dependency"
> needs: T-025, T-031

**Goal** A single source of truth for "is feature X enabled for org Y" that consolidates plan features and per-org overrides.

**Build**

- `backend/app/modules/feature_flags/repository.py`:
  - `OrgFeatureFlagRepository(session, org_id)` extends `BaseRepository`
  - `get_overrides(self) -> dict[str, bool]`
  - `set_override(self, flag_name: str, enabled: bool) -> OrgFeatureFlag` (upsert)
  - `delete_override(self, flag_name: str) -> None`
- `backend/app/modules/feature_flags/service.py`:
  - `FeatureFlagService.is_enabled(self, org, flag: str) -> bool`:
    1. Cache lookup: `Redis GET feature_flags:{org_id}:{flag}` (60 s TTL)
    2. On miss: load org's plan + overrides; result = `bool(plan.features.get(flag)) AND override` (override defaults True if absent, can be explicitly disabled)
    3. Cache result; return
  - `FeatureFlagService.all_enabled(self, org) -> dict[str, bool]`:
    1. Cache key `feature_flags:{org_id}:all` (60 s TTL)
    2. On miss: compute the resolved flag map (plan features ∩ overrides) and cache
  - `FeatureFlagService.invalidate(self, org_id) -> None`:
    - Deletes both per-flag keys and the `:all` key
    - Called from: subscription updates, override changes, plan changes
- Replace the inline `require_feature` from T-036 to delegate to this service (better caching, central truth)
- `backend/app/modules/feature_flags/router.py`:
  - `GET /api/v1/orgs/{org_id}/feature-flags` — returns `{ flags: { name: enabled } }`, requires membership

**Tests**

- Flag enabled in plan + no override → True
- Flag enabled in plan + override=False → False (override wins)
- Flag absent from plan → False (override cannot enable it)
- Cache hit on second call (assert no DB query)
- Invalidation on subscription update test: change plan → cached `all_enabled` invalidated
- 100% coverage on service

**Done when**

- [ ] All resolution paths tested
- [ ] `require_feature` migrated to use the service
- [ ] Cache invalidation hooks added to billing webhook handlers (T-030) and override endpoints

---

### T-121 useFeatureFlag hook

> HTML ref: §"React web app" → "Key hooks"
> needs: T-120, T-038

**Goal** Frontend hook reads the resolved flag map once per session via TanStack Query; UI gates with one-line checks.

**Build**

- `packages/hooks/src/useFeatureFlag.ts`:

  ```typescript
  export function useFeatureFlags() {
    const { org } = useOrg();
    return useQuery({
      queryKey: ["feature-flags", org.id],
      queryFn: () => apiClient.featureFlags.list({ orgId: org.id }),
      staleTime: 60_000,
    });
  }

  export function useFeatureFlag(flag: string): boolean {
    const { data } = useFeatureFlags();
    return Boolean(data?.flags?.[flag]);
  }
  ```

- No JSX in `packages/hooks` (enforced by lint)
- On org switch (via `useOrg`), the query key changes so the cache is automatically separated per-org

**Tests**

- Mock API returns `{ flags: { advanced_reports: true } }` → `useFeatureFlag("advanced_reports")` returns true
- Org switch causes refetch
- Default (loading) returns false (deny by default)

**Done when**

- [ ] Tests pass
- [ ] Web app uses `useFeatureFlag` to hide a real navigation item (e.g. "Advanced reports") when disabled

---

### T-122 Admin endpoint to toggle flags

> needs: T-120, T-140

**Goal** Superadmin can override flags per org (without modifying the user's plan) for support and beta-testing.

**Build**

- Endpoints (under T-140's admin auth guard):
  - `GET /api/v1/admin/orgs/{org_id}/feature-flags` — current resolved map + per-flag override state
  - `PUT /api/v1/admin/orgs/{org_id}/feature-flags/{flag}` — body `{ enabled: bool }` → upserts override
  - `DELETE /api/v1/admin/orgs/{org_id}/feature-flags/{flag}` — removes the override (revert to plan default)
- Every mutation writes an `AuditEvent` (`action="admin.feature_flag.set"`, includes flag name and value)
- Mutation invalidates the flag cache for that org

**Tests**

- Non-superadmin → 403
- Set override → `useFeatureFlag` reflects within `staleTime`
- Delete override → resolved map reverts to plan default
- Audit event written

**Done when**

- [ ] Permission and audit tests pass
- [ ] Admin UI in T-143 wires to these endpoints

---

## P2-D · Billing completeness

> HTML ref: §"Billing" → "What's included" (dunning, trial, usage metering, invoice history)

### T-130 Dunning email sequence

> HTML ref: §"Billing" → "Dunning"
> needs: T-030, T-035

**Goal** On `invoice.payment_failed`, schedule a sequence of reminder emails at 1 day, 3 days, and 7 days. Stop the sequence when payment succeeds or subscription is canceled.

**Build**

- `backend/app/modules/billing/dunning.py`:
  - `DunningService.start(subscription_id, invoice_id) -> None`:
    1. Create `DunningCampaign` row: `{ subscription_id, invoice_id, started_at, status: "active" }`
    2. Enqueue three ARQ jobs with delays of 1d, 3d, 7d (`arq.queue.enqueue_job(..., _defer_by=timedelta(days=N))`)
    3. Each job stores its arq job_id on the campaign so cancel can revoke them
  - `DunningService.cancel(subscription_id) -> None`:
    - Mark campaign `status="canceled"`, revoke all queued ARQ jobs by id
  - `DunningService.complete(subscription_id) -> None`:
    - Same as cancel but `status="completed"`
- `DunningCampaign` model (UUIDPKMixin + TimestampMixin + OrgScopedMixin via subscription):
  - `subscription_id, invoice_id, status, scheduled_job_ids: list[str] (JSONB), started_at, ended_at`
  - Migration in same alembic revision as a stripe_event_received_at column addition
- Webhook handler (T-030) updated:
  - `invoice.payment_failed` → `DunningService.start(...)`
  - `invoice.payment_succeeded` → `DunningService.complete(...)`
  - `customer.subscription.deleted` → `DunningService.cancel(...)`
- Email job per step:
  - Day 1: "Your payment failed — please update your card" (gentle, link to billing portal)
  - Day 3: "Reminder — your subscription will be suspended in 4 days"
  - Day 7: "Subscription suspended" — set sub status to `canceled` if still `past_due`
- Idempotent at every step: re-running same step does not re-send (check audit log for the previous send)

**Tests**

- `payment_failed` webhook → campaign created, 3 jobs scheduled
- `payment_succeeded` cancels remaining jobs
- Day-7 step on still-`past_due` sub transitions to `canceled`
- Re-trigger payment_failed while a campaign is active → previous campaign canceled, new one started
- 100% coverage on `dunning.py`

**Done when**

- [ ] All dunning paths tested
- [ ] Audit events for every email sent
- [ ] Cancel revokes queued ARQ jobs (verify via Redis inspection)

---

### T-131 Trial expiry warning emails

> needs: T-034, T-042, T-035

**Goal** Daily scheduled job sends "your trial ends in 3 days" and "1 day" emails to org owners.

**Build**

- `backend/app/workers/scheduler.py` (extend the APScheduler instance from T-042):
  - Job: `notify_expiring_trials` — daily at 09:00 UTC
- `backend/app/workers/jobs/billing.py`:
  - `notify_expiring_trials(ctx) -> None`:
    1. Query subscriptions where `status="trialing"` AND `trial_end` is between 3 days and 3 days + 1 hour from now → send "3-day warning"
    2. Query where `trial_end` is between 1 day and 1 day + 1 hour → send "1-day warning"
    3. For each: enqueue `send_email_job` with `TrialEnding.tsx` template addressed to org OWNER memberships
    4. Idempotent: stores a sent marker `audit_event` of action `trial.warning_sent_3d` / `_1d` per subscription; skips if already sent
- Sub-job: `expire_trials_to_inactive` — daily at 10:00 UTC:
  - For subs where `trial_end < now` AND `status="trialing"` AND no payment method: transition to `canceled`, send "trial expired" email

**Tests** (use `freezegun`)

- Sub with `trial_end = now + 3d2h` → no email
- Sub with `trial_end = now + 3d0.5h` → 3d warning sent
- Re-running scheduler same day → no duplicate (audit marker check)
- Expired-trial transition test

**Done when**

- [ ] Time-window tests green
- [ ] Idempotency test green
- [ ] Manual end-to-end smoke confirms email delivered to MailHog

---

### T-132 UsageRecord model + report_usage job skeleton

> HTML ref: §"Billing" → "Usage metering"
> needs: T-029, T-034

**Goal** A skeleton for usage-based billing — model + ARQ job stub — so adding a metered feature later is a one-day task, not a one-week one.

**Build**

- `backend/app/modules/billing/usage_models.py`:
  ```python
  class UsageRecord(SQLModel, table=True):  # UUIDPKMixin + TimestampMixin + OrgScopedMixin
      metric: str = Field(index=True)        # "api_calls", "ai_tokens", "exports"
      quantity: int                          # always non-negative integer (multiply for fractional)
      occurred_at: datetime = Field(index=True)
      reported_to_stripe_at: datetime | None = None
      stripe_usage_record_id: str | None = None
      idempotency_key: str | None = Field(default=None, unique=True, index=True)
      # idempotency: dedup at write time via the unique constraint
  ```
- `backend/app/modules/billing/usage_service.py`:
  - `UsageService.record(self, *, metric, quantity, idempotency_key=None) -> UsageRecord`:
    - Optionally use idempotency_key to skip duplicates (rolling 24h)
  - `UsageService.aggregate(self, *, metric, since, until) -> int`
- `backend/app/workers/jobs/usage.py`:
  - `report_usage_to_stripe(ctx) -> None` — scheduled every 15 min:
    - Find unreported records, group by `(org_id, metric)`, sum
    - Call `stripe.SubscriptionItem.create_usage_record(...)` for each `(subscription_item, metric)` (mapping config in `Plan.features.usage_metric_to_stripe_item`)
    - Mark records reported
- Helper dependency `def record_usage(metric: str, quantity: int = 1) -> Callable` — usable as a FastAPI dependency on any metered route

**Tests**

- Duplicate idempotency_key within 24h → second insert rejected (unique constraint)
- Aggregation respects time window
- Mock Stripe: usage records reported and marked
- 100% coverage on `usage_service.py`

**Done when**

- [ ] All usage tests green
- [ ] Module README documents how to add a new metric

---

### T-133 Invoice history endpoint + UI

> HTML ref: §"Billing" → "Invoice history"
> needs: T-031

**Goal** Show users their past invoices without storing them ourselves (fetch from Stripe, cache for performance).

**Build**

- `backend/app/modules/billing/invoices.py`:
  - `InvoiceService.list_for_org(self, *, after_cursor=None, limit=20) -> tuple[list[InvoiceDTO], cursor]`:
    - Cache key `invoices:{org_id}:{cursor or "first"}` with 5 min TTL
    - On miss: `stripe.Invoice.list(customer=org.stripe_customer_id, starting_after=cursor, limit=limit)`
    - Returns DTOs: `{ id, number, amount_paid, currency, status, hosted_invoice_url, invoice_pdf, period_start, period_end, created_at }`
  - `InvoiceService.invalidate(self) -> None` — called by webhook handler on `invoice.*` events
- `GET /api/v1/billing/invoices` — cursor-paginated; requires `BILLING_READ`
- Web: Settings → Billing → "Invoices" tab:
  - Table with date, amount, status, "Download PDF" link to `invoice_pdf` URL (Stripe-hosted, no PCI scope)
  - Empty state for orgs without an active subscription

**Tests**

- Mock Stripe list → DTOs correctly shaped
- Cache invalidated by webhook
- BILLING_READ permission gates the endpoint
- Cross-tenant isolation (org A cannot see org B's invoices)

**Done when**

- [ ] All endpoint tests green
- [ ] UI renders against mocked + real Stripe

---

## P2-E · Admin panel

> HTML ref: §"Admin panel" stub in Phase 2

### T-140 is_superadmin guard

> needs: T-021

**Goal** A dedicated dependency separate from RBAC. Superadmin is a platform-level role (the ForgeSaaS operators), distinct from org-level roles.

**Build**

- `backend/app/core/dependencies.py` (extend):
  - `require_superadmin(user: User = Depends(get_current_user)) -> User`:
    - Raises `PermissionDeniedError` if `user.is_superadmin is False`
    - Logs every superadmin access with structured fields (path, query, request_id, actor_id, target if extractable)
- All admin routes live under `/api/v1/admin/`
- Admin routes are explicitly excluded from the OpenAPI schema shown to non-admin tokens (`include_in_schema=False` for the router, or schema filtered by token introspection)
- A separate Loguru sink writes admin actions to `admin-audit.log` in addition to the normal log stream

**Tests**

- Non-superadmin → 403
- Superadmin → 200
- Access logged with `admin_action=true` field
- Admin endpoints not in OpenAPI schema for non-admin tokens

**Done when**

- [ ] Permission test passes
- [ ] Separate admin log sink verified

---

### T-141 Admin endpoints — list orgs, list users, manual plan override

> needs: T-140, T-025, T-031

**Goal** Read-mostly endpoints for support operations.

**Build**

- `backend/app/modules/admin/router.py` — all under `/api/v1/admin/`:

  | Method | Path                      | Purpose                                                                                                                                                 |
  | ------ | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | GET    | `/orgs`                   | Cursor list with filters: `?q=` (slug or name), `?plan=`, `?status=`                                                                                    |
  | GET    | `/orgs/{id}`              | Full org detail including subscription, member count, file count, last activity                                                                         |
  | GET    | `/users`                  | Cursor list with filters: `?q=` (email), `?active=`                                                                                                     |
  | GET    | `/users/{id}`             | Full user detail including all memberships, sessions, audit summary                                                                                     |
  | PUT    | `/orgs/{id}/plan`         | Body: `{ plan_slug: str, billing_interval: ..., free_override: bool, expires_at: datetime? }` — Force-assign a plan. Records audit event with full diff |
  | POST   | `/orgs/{id}/extend-trial` | Body: `{ days: int }` — Extends trial by N days                                                                                                         |
  | POST   | `/orgs/{id}/comp-credit`  | Body: `{ amount_cents: int, reason: str }` — Issues Stripe coupon                                                                                       |

- All write operations record `AuditEvent` with `action="admin.<op>"`, `actor_id=superadmin.id`, full diff
- Audit log entries for admin actions get a special `severity="elevated"` tag so they can be alerted on

**Tests**

- Non-superadmin → 403 for every endpoint
- Plan override updates org and writes audit event with diff
- Trial extension updates `subscription.trial_end`; webhook reconciliation respects manual extensions
- Comp credit creates a Stripe coupon via mocked Stripe; audit logged

**Done when**

- [ ] All admin endpoints tested
- [ ] Diff captured in every audit event

---

### T-142 User impersonation

> needs: T-140, T-028

**Goal** Superadmin can act as a user for support — issues a short-lived access token scoped to one user, fully audited, with a banner so it's impossible to forget you're impersonating.

**Build**

- `backend/app/modules/admin/impersonation.py`:
  - `POST /api/v1/admin/users/{user_id}/impersonate` — body: `{ reason: str (required, min 10 chars), duration_minutes: int (max 60) }`
    - Validates reason (free-form but stored verbatim)
    - Mints a special access token with claim `impersonator_id=<superadmin.id>` and `imp_exp=<short-lived>` (max 60 min, hard cap)
    - **Crucial:** these tokens cannot be refreshed (no refresh token issued)
    - Cannot impersonate other superadmins (defence-in-depth)
    - Audit event: `admin.impersonate.start`, severity=elevated, payload includes reason
  - `POST /api/v1/admin/impersonate/end` — invalidates the current impersonation token (writes a deny entry to Redis blocklist for `jti`)
- JWT verification (T-014) updated to:
  - Reject tokens whose `jti` is in the Redis blocklist
  - On every request with `impersonator_id` present, attach `impersonator_id` to logs + audit events for all actions taken
- Frontend: when access token contains `impersonator_id`, the app shell shows a fixed top banner: "Impersonating <email> — [End impersonation]". Banner is non-dismissable.

**Tests**

- Impersonate non-superadmin user → token issued, banner shows in frontend
- Impersonate another superadmin → 403
- Token refresh attempt with impersonation token → 401
- Hard 60-minute cap enforced (token rejected after expiry even if not explicitly ended)
- Every action during impersonation has `impersonator_id` in its audit event
- Ending impersonation: token rejected on next use
- Missing reason → 422

**Done when**

- [ ] All security boundary tests green
- [ ] Audit trail complete
- [ ] Banner visible in web UI

---

### T-143 Admin web UI

> needs: T-141, T-142, T-122

**Goal** Separate web surface for admin operations — never mixed with the user-facing app.

**Build**

- `apps/web/src/routes/_admin/` — separate layout (no org switcher, distinct header color, "ADMIN" badge), separate guard requiring `user.is_superadmin`
- Pages:
  - `/_admin/` — dashboard: counts (orgs, users, MRR estimate from Stripe), recent signups, recent payment failures
  - `/_admin/orgs` — searchable list with quick filters
  - `/_admin/orgs/$id` — detail view; tabs for Subscription, Members, Files, Audit, Feature flags
  - `/_admin/users` — searchable list
  - `/_admin/users/$id` — detail; impersonate button (modal asks for reason, duration)
  - `/_admin/audit` — global audit feed, filterable by actor, action, date
- All admin pages have a visible "Admin" badge in the header to prevent confusion with user-facing pages
- Admin routes never appear in the user-app sidebar (separate router group)

**Tests** (Vitest + RTL)

- Non-superadmin navigating to `/_admin/` → redirect to user app
- Impersonate flow: click → modal → submit → access token replaced → banner appears → "End impersonation" returns to admin
- Search debounce and pagination work

**Done when**

- [ ] All admin pages render
- [ ] Impersonation flow works end-to-end against the local backend
- [ ] Visual distinction (color, badge) clear

---

## P2-F · Storybook + design system

> HTML ref: §"Build phases" → "Phase 2" → "Storybook for packages/ui components"

### T-150 Storybook for packages/ui

> needs: T-041, T-046 (FileUploader), T-106 (email templates)

**Goal** Every public component in `packages/ui` and every email template in `packages/email-templates` has a Storybook story. New components are not merged without a story.

**Build**

- `packages/ui/.storybook/`:
  - `main.ts` with Vite framework, MDX support, a11y addon, viewport addon, interactions addon
  - `preview.tsx` wraps every story in: ThemeProvider (light/dark toggle), `@tanstack/react-query` test provider, Tailwind globals
- Stories for every component from T-041:
  - `Button.stories.tsx` — variants (default, destructive, outline, ghost), sizes, loading + disabled
  - `Input.stories.tsx` — with label, with error, disabled
  - `Form.stories.tsx` — full form with Zod schema
  - `Dialog.stories.tsx`, `Tabs.stories.tsx`, `Table.stories.tsx`, `Badge.stories.tsx`, `Avatar.stories.tsx`, `DropdownMenu.stories.tsx`, `Skeleton.stories.tsx`, `Alert.stories.tsx`, `Separator.stories.tsx`, `Tooltip.stories.tsx`
- Stories for Phase 2 additions:
  - `FileUploader.stories.tsx` — idle, uploading, success, error states (mocked API via MSW)
  - `NotificationBell.stories.tsx` — empty, with unread, dropdown open
- Email template stories under `packages/email-templates/.storybook/` — each template rendered with example props
- Accessibility: every story automatically tested with axe in CI (a11y addon error mode)
- Visual regression: chromatic config in CI (Phase 3 wires the actual snapshots; Phase 2 just configures the project)

**Tests**

- `pnpm --filter @forge/ui storybook:test` runs Storybook test-runner; every story renders without throwing; every interaction passes
- A11y violations in any story → test fails
- CI: `storybook:build` outputs static files; published to a preview URL per PR

**Done when**

- [ ] Every component in `packages/ui/src/components/` has a story
- [ ] Every email template has a story
- [ ] axe-core in test-runner passes on all stories
- [ ] Static Storybook builds in CI

---

## §Phase-2 verification

### T-160 Phase 2 acceptance runbook

> needs: every Phase 2 task

**Goal** Document and execute the Phase 2 acceptance flow. This is the gate to Phase 3.

**Build**

- `docs/runbooks/phase2-verification.md` documenting exact steps:
  1. `make dev` — all services up; `make seed` — superadmin + demo plans (now includes a "Pro" plan with feature flags `advanced_reports`, `file_storage`)
  2. **Notifications happy path:** as user A, invite user B → user B receives email (MailHog) + in-app notification appears in user A's bell within 1 s (via SSE)
  3. **Push:** install mobile app on simulator, log in, accept push permission → register a push token → trigger an admin-side test push → notification received
  4. **File storage:** upload a file via web dropzone → file appears in list with status "ready" → download via signed URL works
  5. **File storage quota:** attempt to upload a file that would exceed plan quota → 402 with clear message
  6. **Virus scan:** with `VIRUS_SCANNER=noop`, status transitions to "ready" + virus_scan_result="clean"
  7. **Feature flags:** as superadmin, set override `advanced_reports=false` for the user's org → within 60 s the corresponding nav item disappears in the user's UI
  8. **Dunning:** trigger a Stripe test event `invoice.payment_failed` via Stripe CLI → DunningCampaign created → first email sent (visible in MailHog)
  9. **Trial expiry:** with `freezegun`-style time advance via a test endpoint (admin-only), set `trial_end` to 3 days from now → run scheduler manually → 3-day warning email sent
  10. **Invoice history:** Settings → Billing → Invoices shows past invoices; download link opens Stripe-hosted PDF
  11. **Impersonation:** superadmin impersonates a user → UI shows red banner with "Impersonating <email>" → ending returns to admin
  12. **Storybook:** `pnpm --filter @forge/ui storybook` opens, all components render, a11y errors zero

**Phase 2 complete when ALL of:**

- [ ] Runbook executes without intervention
- [ ] `pytest --cov` reports ≥85% overall, 100% on `auth/`, `billing/`, `tenancy/`, `gdpr/`, `notifications/`, `storage/`, `feature_flags/`, `admin/`
- [ ] CI jobs (lint, test, security, build) green on `main`
- [ ] Cross-tenant isolation sweep (T-049) extended to cover every new Phase 2 endpoint
- [ ] Schemathesis (T-050) clean on the new endpoints
- [ ] Storybook test-runner passes; all stories a11y-clean
- [ ] Import-linter and Semgrep rules pass
- [ ] Launch checklist sections from `forgesaas-plan.html` covering notifications + files + admin all checked

---

# Phase 3 — Hardening

> HTML ref: §"Build phases" → "Phase 3"
>
> **Prerequisite:** T-160 (Phase 2 acceptance runbook) is complete and green. Do not start Phase 3 until every Phase 2 acceptance path passes without intervention.
>
> **Phase 3 acceptance criterion:** (1) An error thrown on backend, web, or mobile lands in Sentry with a request ID; a request can be followed from HTTP span → DB span → job span in traces; Grafana dashboards render from committed JSON. (2) ZAP baseline scan, OWASP Top 10 test mapping, and the ASVS L2 checklist are green in CI. (3) The full signup → verify → invite → upgrade flow passes in Playwright on Chromium + WebKit, in Maestro on mobile, under k6 load within latency budgets, and with zero axe-core violations. (4) The app ships in five+ locales including one RTL locale. (5) No list endpoint issues N+1 queries and no route exceeds its bundle budget. (6) `make new-module NAME=foo` produces a module that passes lint + mypy out of the box. Each item has an automated check — Phase 3 "done" is a green pipeline, not a claim.

---

## P3-A · Observability (full)

> HTML ref: §"Observability" (entire section)
>
> The three pillars — logs (done in T-011), metrics, traces — plus error tracking and resilience. Rule of thumb: every incident postmortem question ("what happened, to whom, how slow, which dependency") must be answerable from a dashboard, not from a shell session.

### T-201 Sentry SDK wired on backend, web, and mobile

> HTML ref: §"Observability" → "Request ID middleware" (Sentry tag integration)
> needs: T-002, T-040, T-045

**Goal** Errors on all three platforms report to Sentry with release, environment, and request ID — and never contain PII or secrets.

**Build**

- Backend — `backend/app/core/sentry.py`, called from the app factory **before** middleware registration:
  - `sentry_sdk.init(dsn=settings.SENTRY_DSN, environment=settings.ENVIRONMENT, release=app_version, send_default_pii=False, traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE)`
  - Init is a no-op when `SENTRY_DSN` is unset (dev default) — the rest of the app must not care
  - `before_send` hook reuses the T-011 scrubber key list (`password`, `token`, `secret`, `api_key`, `authorization`, `cookie`) to redact event payloads and breadcrumbs
  - New settings: `SENTRY_TRACES_SAMPLE_RATE: float = 0.1` (+ `.env.example` entry)
  - Note: the global handler (T-006) already calls `sentry_sdk.capture_exception` and T-007 sets the `request_id` tag — with the SDK initialised those now actually deliver. Verify both still work; do not double-report (the FastAPI integration also captures unhandled exceptions — configure the handler to only capture what the integration misses, or disable the integration's duplicate capture).
- Web — `apps/web/src/lib/sentry.ts`:
  - `@sentry/react` init gated on `import.meta.env.VITE_SENTRY_DSN`; `environment` + `release` from build-time env
  - Top-level `<ErrorBoundary>` with a branded fallback page ("Something went wrong · Reload") — never a white screen
  - Source maps uploaded via the Sentry Vite plugin, CI builds only (auth token from CI secret; plugin disabled locally)
- Mobile — `apps/mobile/`:
  - `@sentry/react-native` with the Expo config plugin; init in the root layout, gated on env
  - Release tagging compatible with OTA updates (`release` = app version + update ID) so stack traces map to the right bundle
- All three: user context set to `{ id: user_id }` only — never email or name

**Tests**

- Backend: `before_send` given an event containing `"password": "hunter2"` in extra → value replaced with `"***"`
- Backend: init with `SENTRY_DSN=None` → app boots, no SDK calls fail
- Backend: endpoint raising `RuntimeError` → exactly one Sentry event captured (assert via `sentry_sdk` test transport), tagged with `request_id`
- Web: rendering a component that throws inside the boundary shows the fallback page (Vitest + RTL)

**Done when**

- [ ] A thrown test error on each platform appears in Sentry (verify once against a real DSN, then rely on transport tests)
- [ ] Scrubber test passes — no secret keys in any event
- [ ] Source maps resolve web stack traces in the Sentry UI
- [ ] `mypy --strict` / `tsc --strict` clean

---

### T-202 Prometheus metrics — `/metrics` on internal network only

> HTML ref: §"Observability" → "Metrics (Prometheus)" | "Health endpoints"
> needs: T-002

**Goal** RED metrics (rate, errors, duration) for every endpoint plus app-specific gauges, scrapeable inside the Docker network but unreachable from the public edge.

**Build**

- `backend/app/core/metrics.py`:
  - `prometheus-fastapi-instrumentator` (already pinned in T-002 deps): `Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)`
  - Custom collectors, registered here and updated where the data lives:
    - `forge_db_pool_used` / `forge_db_pool_size` gauges (read from `engine.pool.checkedout()` / `.size()`)
    - `forge_arq_queue_depth` gauge (Redis `LLEN` on the ARQ queue key, sampled by a collector callback)
    - `forge_active_sessions` gauge (count of non-revoked, non-expired `Session` rows — refreshed by a 60 s APScheduler job, not per-scrape)
    - `forge_stripe_webhook_seconds` histogram — wrap the T-030 webhook handler
    - `forge_emails_sent_total` / `forge_push_sent_total` counters in the respective jobs
- Edge lockout — `/metrics` must never be public:
  - `Caddyfile` and `Caddyfile.prod`: `respond /metrics 404` before the `reverse_proxy` directive
  - `docker-compose.prod.yml`: backend port stays internal to `forge-net`; only Caddy publishes ports
- Metric label discipline: route templates (`/api/v1/users/{id}`), never raw paths — no per-UUID label cardinality explosions

**Tests**

- Direct ASGI request to `/metrics` → 200, `text/plain` Prometheus format, contains `forge_db_pool_size`
- Request through Caddy to `https://localhost/metrics` → 404 (integration, dev compose)
- CI grep: both Caddyfiles contain the `respond /metrics 404` rule (same pattern as the T-004.5 redirect grep)
- Stripe webhook test asserts the histogram observed a sample

**Done when**

- [ ] `/metrics` scrapeable from inside `forge-net`, 404 via Caddy
- [ ] All five custom metric families present in output
- [ ] No high-cardinality labels (assert label values for the request metric are route templates)

---

### T-203 OpenTelemetry auto-instrumentation — FastAPI + SQLAlchemy + ARQ + Redis

> HTML ref: §"Observability" → "Traces (OpenTelemetry)"
> needs: T-002, T-011

**Goal** One request produces one trace: HTTP server span → DB query spans → Redis spans; background jobs produce their own root spans. Exporter is vendor-neutral and switched by a single env var.

**Build**

- `backend/app/core/tracing.py`, called from the app factory:
  - `TracerProvider` with resource attrs `service.name="forge-backend"`, `service.version`, `deployment.environment`
  - OTLP exporter configured from standard `OTEL_EXPORTER_OTLP_ENDPOINT` env var; when unset, tracing is a no-op (dev default) — document that Tempo, Jaeger, and Honeycomb all speak OTLP
  - Parent-based ratio sampler, rate from `OTEL_TRACES_SAMPLER_ARG` (default `0.1`)
  - Instrument: `FastAPIInstrumentor`, `SQLAlchemyInstrumentor` (on the async engine's sync core), `RedisInstrumentor`
- ARQ has no official instrumentation — wrap job execution in the worker (T-034's `WorkerSettings.on_job_start` / custom runner): span name `arq.<job_name>`, attributes `job.id`, `job.try`, `job.queue`
- Log/trace correlation: inject `trace_id` + `span_id` into Loguru records (extend the T-011 configuration) so a log line can be pasted into the trace viewer
- `request_id` (T-007) set as a span attribute on the server span

**Tests**

- Using the in-memory span exporter: one API call that reads the DB → a server span with ≥1 child span of kind CLIENT with `db.system="postgresql"`
- Enqueue + run a test ARQ job → root span `arq.<name>` with the job attributes
- `OTEL_EXPORTER_OTLP_ENDPOINT` unset → no exporter configured, app boots, zero overhead beyond no-op tracer
- A log record emitted inside a traced request contains the active `trace_id`

**Done when**

- [ ] Span tree test passes (HTTP → DB parent/child relationship asserted)
- [ ] ARQ job spans emitted
- [ ] Trace/log correlation test passes
- [ ] `mypy --strict` clean

---

### T-204 Grafana dashboards exported as JSON

> HTML ref: §"Observability" → "Metrics (Prometheus)"
> needs: T-202

**Goal** Dashboards are code: committed JSON that any Grafana instance can import, plus a local compose profile to view them during development.

**Build**

- `docs/grafana/` dashboards (datasource as a `${DS_PROMETHEUS}` template variable, never a hardcoded UID):
  - `api-overview.json` — request rate, error rate (4xx/5xx split), p50/p95/p99 latency by route, in-flight requests
  - `workers.json` — ARQ queue depth, job duration by name, job failure rate, emails/push sent
  - `database.json` — pool usage vs size, connection wait, (slow-query panels land in T-205)
  - `business.json` — active sessions, signups/day, webhook processing time
- Optional local stack — `docker-compose.yml` profile `observability`:
  - `prom/prometheus` scraping `backend:8000/metrics` (config in `docs/grafana/prometheus.yml`)
  - `grafana/grafana` with provisioning mounts auto-loading the four dashboards + datasource
  - `make observe` target: `docker compose --profile observability up -d`
- README in `docs/grafana/` — how to import into a managed Grafana, how to regenerate JSON after editing (export → strip `id`/`uid` → commit)

**Tests**

- CI script: every `docs/grafana/*.json` parses, declares `${DS_PROMETHEUS}`, and contains no hardcoded datasource UID
- Smoke (documented, run once locally): `make observe` → Grafana at `:3001` renders all four dashboards with live data

**Done when**

- [ ] Four dashboards committed and CI-validated
- [ ] `make observe` shows live request data end-to-end
- [ ] README documents the export-and-commit loop

---

### T-205 Postgres slow-query observability — `pg_stat_statements`

> HTML ref: §"Observability" → "Metrics (Prometheus)" | §"Best practices" → "Database best practices"
> needs: T-202, T-204

**Goal** The slowest statements in the system are visible on a dashboard and alert before users complain.

**Build**

- Enable the extension:
  - `docker-compose.yml` + `docker-compose.prod.yml`: postgres `command: -c shared_preload_libraries=pg_stat_statements -c pg_stat_statements.track=all`
  - Migration: `op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_stat_statements"))` — raw SQL justified per §0.3 (DDL not expressible in SQLAlchemy); `downgrade()` drops the extension
- `prometheuscommunity/postgres-exporter` service (compose `observability` profile + prod):
  - Custom queries file `docs/grafana/postgres-exporter-queries.yaml` exporting per-statement `calls`, `mean_exec_time`, `total_exec_time`, `rows` for the top 50 statements by total time
  - Note honestly: `pg_stat_statements` exposes mean/max, not true p95 — the p95 alert target from the plan is implemented as **mean > 500 ms sustained**, with the k6 suite (T-223) owning true end-to-end p95
- Prometheus alert rule file `docs/grafana/alerts.yml`: statement with `calls > 100` and `mean_exec_time > 500ms` for 15 min → warning; validated with `promtool check rules` in CI
- "Hall of shame" panel added to `database.json` (T-204): top 10 statements by total time, with query text
- Runbook `docs/runbooks/slow-queries.md`: how to read the dashboard, `EXPLAIN ANALYZE` workflow, when to add an index vs rewrite vs cache

**Tests**

- Migration round-trip (extension created and dropped)
- Integration: after running the test suite against the dev stack, `SELECT count(*) FROM pg_stat_statements` > 0
- CI: `promtool check rules docs/grafana/alerts.yml` exits 0

**Done when**

- [ ] Extension live in dev + prod compose
- [ ] Exporter metrics visible in Prometheus; hall-of-shame panel renders
- [ ] Alert rule validated in CI; runbook written

---

### T-206 Circuit breakers on all external calls

> HTML ref: §"Best practices" → "Error handling" (degraded modes)
> needs: T-002, T-030, T-035, T-111, T-113

**Goal** A slow or dead third party (Stripe, Resend, S3, virus scanner, Expo push) degrades one feature — it never cascades into request-path timeouts or a worker pile-up.

**Build**

- `backend/app/core/resilience.py`:

  ```python
  # One named breaker per integration, module-level registry
  BREAKERS: dict[str, pybreaker.CircuitBreaker] = {
      "stripe": ..., "resend": ..., "s3": ..., "virus_scanner": ..., "expo_push": ...,
  }  # fail_max=5, reset_timeout=30s

  def resilient(integration: str, *, retries: int = 3) -> Callable:
      """Decorator: tenacity retry (exponential 0.5s→4s, retry ONLY on
      transient errors — timeouts, connection errors, 5xx) wrapped inside
      the integration's circuit breaker. Open breaker raises
      ExternalServiceUnavailableError without attempting the call."""
  ```

- New exception `ExternalServiceUnavailableError(DomainError)`: `status_code=503`, `code="service_unavailable"`, message names the degraded feature, not the vendor internals
- Retry discipline: never retry non-idempotent calls without an idempotency key — §0.4 already mandates keys on Stripe, webhooks, and email sends; assert the decorator is only applied to call sites that pass one
- Apply to every outbound call site: Stripe API calls (T-031), Resend send (T-035), S3/R2/MinIO operations (T-111), virus scanner (T-113), Expo push (T-105)
- Metrics (registered via T-202): `forge_breaker_state{integration}` gauge (0 closed / 1 half-open / 2 open), `forge_breaker_open_total{integration}` counter, `forge_external_call_seconds{integration}` histogram
- Degraded-mode behaviour, implemented and documented in `docs/runbooks/degraded-mode.md`:
  - **Stripe down** — checkout/portal endpoints return 503 envelope; webhooks are safe (Stripe retries for days)
  - **Resend down** — email jobs re-queue with ARQ backoff; emails delayed, never dropped
  - **S3 down** — presign/download return 503; existing metadata unaffected
  - **Virus scanner down** — uploads stay `pending_scan` and undownloadable; scan job retries
  - **Expo push down** — push dropped after retries; in-app + email channels unaffected

**Tests**

- 5 consecutive simulated failures → breaker opens → 6th call raises `ExternalServiceUnavailableError` **without** invoking the wrapped function
- After `reset_timeout` (freezegun / injected clock) → half-open → one success closes it
- Retry fires on `httpx.TimeoutException`, does **not** fire on a 400 response
- Open `resend` breaker → email job re-queued, not marked failed
- Metrics reflect open/close transitions

**Done when**

- [ ] All five integrations wrapped; grep test asserts no bare outbound `httpx`/SDK call outside `resilience`-decorated call sites
- [ ] Breaker lifecycle tests pass, 100% coverage on `resilience.py`
- [ ] Degraded-mode runbook covers all five integrations

---

## P3-B · OWASP hardening

> HTML ref: §"OWASP & security testing" (entire section)
>
> Security is verified continuously, not audited once. T-054 already runs SAST (Bandit, Semgrep), dependency scans, and secrets scanning. This section adds DAST, proves the OWASP Top 10 mapping with tests, and produces the documents a real engagement needs.

### T-210 OWASP ZAP baseline scan in CI against an ephemeral deploy

> HTML ref: §"OWASP & security testing" → "DAST — OWASP ZAP"
> needs: T-055

**Goal** Every PR gets a passive DAST pass against a real running instance — missing headers, cookie flags, and info leaks fail the build before review.

**Build**

- CI job `zap-baseline` in the security workflow (needs the compose stack, so runs after build):
  - Boot the ephemeral stack: `docker compose up -d` (backend + Caddy + Postgres + Redis + MinIO + Mailhog), seed via `make seed`, wait for `/health/ready`
  - Run `ghcr.io/zaproxy/zaproxy:stable zap-baseline.py -t https://caddy -c .zap/baseline.conf -r zap-report.html` on the compose network (trusting the dev cert)
  - Fail the job on any FAIL; WARNs allowed only if listed in `.zap/baseline.conf` with a dated justification comment
  - Upload `zap-report.html` as a workflow artifact
- `.zap/baseline.conf` starts empty except documented ignores (e.g. Mailhog UI if scanned) — every ignore has an owner and reason
- Scan scope: the app origin only; explicitly exclude `/metrics` (returns 404 via Caddy anyway, per T-202)

**Tests**

- One-time verification on a throwaway branch: comment out the `security_headers` middleware → ZAP job fails (proves the gate bites); revert
- Steady state: job green on `main`

**Done when**

- [ ] `zap-baseline` job green in CI on a real PR
- [ ] Header-removal canary run demonstrated failure once (link the red run in the PR description)
- [ ] Report artifact downloadable from the workflow run

---

### T-211 Nightly ZAP full active scan against staging

> HTML ref: §"OWASP & security testing" → "DAST — OWASP ZAP"
> needs: T-210

**Goal** The aggressive scan (active attacks, fuzzing) runs where it can't hurt anyone: nightly, against staging, authenticated.

**Build**

- `.github/workflows/zap-full.yml`: `schedule: cron "0 3 * * *"` + `workflow_dispatch`
- ZAP Automation Framework plan (`.zap/full-scan.yaml`):
  - Target: staging URL from a repo variable
  - Authentication: dedicated `zap-scanner@<staging-domain>` user (MEMBER role, own org, synthetic data) logging in via the real `/api/v1/auth/login` flow; session token refreshed by the plan
  - Active scan policy: default minus DoS-category rules (staging is shared)
  - Out of scope: Stripe-hosted pages, Mailhog, `/metrics`, GDPR delete endpoint for the scanner's own account (it would lock itself out)
- Findings handling: new alerts above LOW open a GitHub issue (one per alert type, deduped by fingerprint) labeled `security`; report artifact retained 90 days
- Scanner credentials rotated by the same process as other staging secrets; documented in the workflow README header

**Tests**

- `workflow_dispatch` run completes end-to-end: authenticates (assert ≥1 authenticated request in the report), scans, uploads report
- Deduplication: re-running against unchanged staging opens no duplicate issues

**Done when**

- [ ] Nightly run green (or red with real findings triaged as issues)
- [ ] Authenticated coverage confirmed in the report
- [ ] Runbook note: what to do when the nightly scan finds something (severity → SLA from T-214)

---

### T-212 OWASP Top 10 test mapping — verified in CI

> HTML ref: §"OWASP & security testing" → "OWASP Top 10 — mitigation & test mapping"
> needs: T-049

**Goal** The claim "every OWASP Top 10 risk has a test proving its mitigation" becomes machine-checked, not a table in a document that drifts.

**Build**

- `docs/security/owasp-top10.md`: the mapping table from the HTML plan (A01–A10 → mitigation in this codebase → test file), kept human-readable
- Pytest marker: `@pytest.mark.owasp("A01")` applied to the existing tests that prove each mitigation:
  - A01 broken access control → T-049 cross-tenant sweep + RBAC matrix (T-021)
  - A02 crypto failures → T-011 log-scrubber tests, T-007 HSTS header test, T-009.5 encryption tests
  - A03 injection → T-050 Schemathesis run + Semgrep raw-SQL rule (CI presence check)
  - A04 insecure design → T-008/T-022 rate-limit tests, T-018 lockout tests
  - A05 misconfiguration → T-007 header tests, T-003 wildcard-CORS startup guard
  - A06 vulnerable components → CI presence check: T-054 audit jobs exist and are non-optional
  - A07 auth failures → T-019 rotation + theft detection, T-024 TOTP, T-018 lockout
  - A08 integrity failures → T-030 webhook bad-signature test, lockfile-unchanged CI check
  - A09 logging failures → T-028 audit-row-on-sensitive-action tests
  - A10 SSRF → **new code**: `backend/app/core/url_guard.py` `validate_outbound_url(url)` rejecting private ranges (RFC 1918, loopback, link-local, `169.254.169.254` metadata), non-http(s) schemes, and DNS-rebinding via resolved-IP check — built now, consumed by Phase 4 outbound webhooks (T-32x)
- CI step `owasp-map-check`: script asserts every category A01–A10 has ≥1 **passing** test carrying its marker (`pytest -m owasp` + a collect step that fails on a missing category); also fails if `owasp-top10.md` references a test file that no longer exists

**Tests**

- `url_guard` unit tests: `http://localhost`, `http://10.0.0.1`, `http://169.254.169.254/latest/meta-data`, `file:///etc/passwd`, hostname resolving to a private IP → all rejected; `https://example.com` accepted
- Meta-test: removing a marker from any category makes `owasp-map-check` fail (verified once on a branch)

**Done when**

- [ ] All 10 categories carry passing marked tests; CI check green
- [ ] `url_guard.py` at 100% coverage
- [ ] `docs/security/owasp-top10.md` cross-checked by CI

---

### T-213 ASVS L2 checklist — `docs/adr/0001-security-baseline.md`

> HTML ref: §"OWASP & security testing" → "Beyond the Top 10"
> needs: T-212

**Goal** A written, evidence-linked security baseline: which ASVS Level 2 requirements the template meets, which it deliberately doesn't, and why.

**Build**

- `docs/adr/0001-security-baseline.md` in ADR format (context / decision / consequences), containing the checklist:
  - One row per applicable ASVS 4.0 L2 requirement across chapters V1 (architecture), V2 (auth), V3 (sessions), V4 (access control), V5 (validation), V6 (crypto), V7 (logging), V8 (data protection), V9 (communications), V12 (files), V13 (API), V14 (config)
  - Columns: requirement · status (`met` / `partial` / `n/a`) · evidence — a repo-relative link to the code, test, or config that satisfies it
  - `n/a` rows carry one-line reasons (e.g. V11 business logic — product-specific, template documents the pattern only)
  - `partial` rows each name a follow-up task ID (existing or newly filed at the bottom of this document)
- This is the document referenced by T-214's pentest scope and by enterprise security questionnaires — write it for an external reader

**Tests**

- CI link-check script: every evidence link in the checklist resolves to an existing file (same mechanism as T-212's doc check)

**Done when**

- [ ] Every applicable L2 requirement has a row; zero rows with empty status
- [ ] Every `met` row links real evidence; every `partial` row names a follow-up
- [ ] Link-check green in CI

---

### T-214 Pentest readiness runbook

> HTML ref: §"OWASP & security testing" → "Penetration testing before real money"
> needs: T-210, T-211, T-213

**Goal** When a manual security engagement happens, day 1 is spent finding bugs — not asking basic questions.

**Pentest guidance** — read before commissioning any external security engagement. Pentesting is not one thing; pick the right tier for your stage:

| Trigger                                                                | Recommended tier                                                                                             | Rough cost      |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | --------------- |
| First deployment, no paying customers yet                              | Automated CI tooling only (already in the plan: T-054, T-210, T-211, T-049, T-050). No external engagement.  | £0              |
| Real users, want ongoing scrutiny                                      | Private bug bounty on HackerOne or Intigriti, scoped to auth + billing + file access                         | £500–£3000 / yr |
| Pre-enterprise sales or pre-fundraise                                  | Time-boxed pentest from a boutique firm (3–5 days)                                                           | £3000–£6000     |
| Enterprise procurement requires a report; regulated data; annual audit | Full formal pentest from a reputable firm (2–4 weeks) with executive summary suitable for sharing            | £8000–£25,000   |
| Handling actual payment cards                                          | Not applicable — the template uses Stripe Checkout (hosted). Cards never touch your infrastructure.          |                 |

**Build**

- `docs/runbooks/pentest-prep.md` containing:
  - **Scope document** — in scope: `/api/v1/*`, web app, mobile app; out of scope: Stripe-hosted pages, S3 storage internals, third-party integrations
  - **Test account provisioning** — a `make pentest-accounts` seed target creating one staging account per role (OWNER, ADMIN, MEMBER, superadmin) with realistic data volumes; credentials delivered out-of-band, rotated after the engagement
  - **Sensitive-endpoint inventory** — auth flows, payment endpoints, admin routes, impersonation (T-142/T-260/T-261), GDPR export/delete — generated from the OpenAPI schema, tagged
  - **Known-issues list** — anything already known (link the `security`-labeled issues from T-211) so testers don't waste time re-discovering it
  - **Staging environment details** — separate from prod, synthetic data only, snapshot/restore procedure
  - **Communication channel** — dedicated contact + escalation path for critical findings
  - **Remediation SLA policy** — critical = 24 h, high = 7 d, medium = 30 d, low = next release
  - **Report handling** — findings tracked to closure as issues, closure evidence linked, retest confirmation recorded
- Cross-link: ASVS checklist (T-213) attached as the baseline claim the testers try to break

**Tests**

- `make pentest-accounts` creates the four accounts on a fresh staging seed, idempotently (documentation-level verification; no CI gate beyond the target existing)

**Done when**

- [ ] Runbook complete — someone unfamiliar with the project could brief a pentest firm from it alone
- [ ] `make pentest-accounts` works against staging
- [ ] SLA policy agreed and written down

---

### T-215 Secure-SDLC & architecture documentation

> Filed by: T-213's ASVS checklist (`docs/adr/0001-security-baseline.md`) — closes rows V1.1.1, V1.1.2, V1.1.4, V1.1.5, V1.1.7, V1.5.1, V1.6.1, V1.7.2, V1.14.4
> needs: T-213

**Goal** Turn the architecture knowledge that currently only exists as prose in `agents.md` and scattered docstrings into the standalone docs ASVS V1 (Architecture, Design and Threat Modeling) expects to exist on their own.

**Build**

- `docs/security/sdlc.md` — the secure-SDLC policy: where security fits in planning/review/release. References the CI security gates already enforced (bandit/semgrep/trivy/pip-audit/gitleaks/trufflehog/ZAP) rather than inventing a new process on top of them
- `docs/security/threat-model.md` — trust boundaries + a data-flow diagram: client ↔ Caddy ↔ backend ↔ Postgres/Redis/object storage/Stripe/Resend/OAuth providers
- `docs/security/key-management.md` — lifecycle for `ENCRYPTION_KEY`/`JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY`: generation, rotation procedure (ties into the existing `ENCRYPTION_KEY_OLD`/`MultiFernet` support), who holds them
- `docs/runbooks/log-shipping.md` — where logs go beyond stdout/`admin-audit.log`/Sentry today, and the plan (or explicit non-plan) for a centralized remote sink
- Update `docs/adr/0001-security-baseline.md`: flip the rows listed above from `partial` to `met`, evidence links pointing at these new docs

**Tests**

- `backend/tests/security/test_security_baseline.py`'s link-check already covers `docs/...` paths — no test changes needed unless a new doc lives outside `docs/`/`backend/`/`.github/`

**Done when**

- [ ] All four docs exist and are cross-linked from `0001-security-baseline.md`
- [ ] ADR rows updated, link-check still green

---

### T-216 Auth/session UX gaps

> Filed by: T-213's ASVS checklist — closes rows V2.1.2, V2.1.5, V2.1.6, V2.1.8, V2.1.9, V2.1.12, V2.2.3, V2.5.5, V3.2.3, V3.3.4, V3.4.4, V3.5.1, V8.2.2, V8.2.3
> needs: T-213

**Goal** Close the concrete auth/session gaps the ASVS pass found. Most are small; the standout is that there's currently no way to change your password while logged in — only a forgot-password reset flow.

**Build**

- `POST /api/v1/auth/change-password` (current + new password; current re-verified via Argon2; same password-policy validation as registration; revokes other sessions on success, same as password reset)
- `password: str = Field(min_length=12, max_length=128)` on every password-accepting schema (currently has no upper bound)
- Reconsider `_validate_password_strength`'s upper/lower/digit composition requirement against NIST SP 800-63B / ASVS V2.1.9 (which explicitly recommends against composition rules in favor of length + breach-list checks) — either drop the composition check or write down why the template deliberately deviates from ASVS here
- `backend/app/modules/auth/router.py`: rename the refresh cookie to use the `__Host-` prefix
- `GET /api/v1/auth/sessions` (list caller's active sessions) + `DELETE /api/v1/auth/sessions/{id}` (revoke one, scoped to the caller)
- `POST /api/v1/auth/oauth/{provider}/unlink` — self-service OAuth unlink
- New email template + send on: password change, 2FA enable/disable, OAuth link/unlink
- Frontend: password-strength meter + temporary-reveal toggle on password fields; confirm the access token is held in memory, not `localStorage`

**Tests**

- `backend/tests/test_auth_service.py` — change-password requires the correct current password, rejects on mismatch, revokes other sessions on success; security-notification email enqueued (mocked) on each of the three triggers
- `backend/tests/test_auth_router.py` — session list/revoke endpoints are scoped to the caller's own sessions (can't revoke someone else's)

**Done when**

- [ ] Authenticated password change works end-to-end, other sessions revoked
- [ ] `__Host-` cookie prefix live; session-list/revoke UI shipped in Settings
- [ ] ADR rows updated

---

### T-217 WebAuthn/FIDO2 second factor

> Filed by: T-213's ASVS checklist — closes rows V2.3.2, V2.9.1, V2.9.2, V2.9.3
> needs: T-024, T-213

**Goal** Offer a phishing-resistant second factor alongside TOTP, for users/orgs that want one.

**Build**

- `WebAuthnCredential` model (member ↔ credential public key, sign counter, AAGUID, nickname)
- A WebAuthn library (e.g. `py_webauthn`) drives registration (`navigator.credentials.create`) and assertion (`navigator.credentials.get`) ceremonies — `backend/app/modules/auth/webauthn.py`
- Router: `POST /auth/webauthn/register/options`, `POST /auth/webauthn/register`, `POST /auth/webauthn/login/options`, `POST /auth/webauthn/login`
- Sign-counter check on every assertion — reject and alert on a counter that goes backwards (clone-detection, same spirit as the existing refresh-token reuse detection in T-019)
- Treated as an alternative to TOTP at the same "2FA enabled" gate in `login()`, not an additional required factor

**Tests**

- Registration + assertion round-trip against a virtual authenticator
- Sign-counter regression rejected
- Existing TOTP flow unaffected when WebAuthn is unused

**Done when**

- [ ] A user can register a hardware key and log in with it instead of a TOTP code
- [ ] Counter-regression test passes
- [ ] ADR rows updated

---

### T-218 Step-up auth & mandatory admin 2FA

> Filed by: T-213's ASVS checklist — closes rows V2.5.7, V2.8.4, V2.8.5, V3.3.2, V3.7.1, V4.3.1, V4.3.3
> needs: T-024, T-140, T-213

**Goal** Sensitive actions and superadmin access require more than "has a valid 15-minute access token."

**Build**

- `require_superadmin` gains a check: `user.totp_enabled or user.webauthn_enabled` required — a superadmin with no second factor is rejected with an error telling them to enable one
- TOTP same-window replay tracking: store `(user_id, last_consumed_totp_step)`, reject a code for a step already consumed, log + notify on rejection
- Step-up re-auth dependency (`require_recent_auth(max_age_minutes=5)`) applied to: email change, 2FA disable (already effectively has this), billing plan changes, member removal
- Idle/periodic session policy: shorten the refresh token's max idle window or add a sliding expiry, per the ASVS L2 target (~12h or 30min idle)
- Documented admin-assisted recovery path for a user who's lost both their TOTP device and all backup codes (superadmin action, fully audited, same posture as impersonation in T-140)

**Tests**

- Superadmin login without 2FA enabled is rejected with an actionable error
- TOTP code reuse within the same 30s step is rejected and logged
- Step-up-gated routes 403 without a recent-enough auth, succeed after re-entering credentials
- Idle-timeout test using a frozen clock

**Done when**

- [ ] Superadmin accounts can't operate without 2FA enabled
- [ ] TOTP replay-within-window rejected
- [ ] Step-up gate covers the four listed actions
- [ ] ADR rows updated

---

### T-219 Data classification & PII handling

> Filed by: T-213's ASVS checklist — closes rows V1.8.1, V1.8.2, V6.1.1, V7.1.2, V7.1.3, V7.2.1, V7.2.2, V8.3.4, V8.3.8
> needs: T-213

**Goal** Replace "only the TOTP secret is classified/encrypted" with an actual, if modest, data-classification pass, and log the security events the ASVS pass found weren't being logged.

**Build**

- `docs/security/data-classification.md` — one row per PII-bearing column across all modules (`User.email`/`full_name`/`avatar_url`, `Membership`, GDPR `ConsentRecord`, etc.): classification (public/internal/sensitive), current protection, retention
- Encrypt `User.full_name` via the existing `EncryptedString` — the highest-value plaintext PII column; document the remaining plaintext columns as an accepted, reasoned scope decision rather than a silent gap
- Extend `_scrub()` in `backend/app/core/logging.py` to redact by known PII field *names* seen in practice (`email`, `full_name`), not just the existing secret-shaped key list
- Emit an audit/security-log event on permission-denied (403 from `require_permission`) and on a failed login attempt (not just the existing lockout counter increment)
- Apply a `GDPR_PURGE_GRACE_DAYS`-style retention/expiry to the next-highest-value data class named in the classification doc (expired sessions and GDPR-deleted users already have this)

**Tests**

- `backend/tests/test_logging.py` — a log record with `email`/`full_name` in `extra` gets redacted
- A 403 from `require_permission` produces an audit-log row
- `backend/tests/test_auth_service_login.py` — a failed login attempt produces a log/audit event, not just a counter increment

**Done when**

- [ ] Data-classification doc covers every PII column across every module
- [ ] `full_name` encrypted at rest
- [ ] Permission-denied and failed-login events logged
- [ ] ADR rows updated

---

### T-225 Build/infra hardening batch

> Filed by: T-213's ASVS checklist — closes rows V1.6.2, V1.9.1, V1.14.4 (shared with T-215), V2.4.5, V2.10.1, V2.10.3, V6.4.1, V6.4.2, V8.1.4, V8.1.5, V8.1.6, V9.1.2, V9.1.3, V9.2.2, V9.2.4, V12.1.3, V12.2.1, V12.3.4, V12.4.2, V13.1.5, V13.2.5, V14.2.5, V14.3.2, V14.3.3, V14.4.2, V14.5.1
> needs: T-213

**Goal** A grab-bag of smaller, mostly-independent infra/build hardening items the ASVS pass found. Grouped into one task because each is too small to justify its own task, not because they share an implementation.

**Build**

- SBOM: a `syft`/`cyclonedx-py` step in CI, uploaded as a workflow artifact
- Content-sniffing file-type validation on upload confirm (e.g. `python-magic` against the first N bytes, cross-checked against the declared `Content-Type`)
- File-count quota per org, alongside the existing byte quota
- `Content-Disposition: attachment` on API responses; explicit 406/415 rejection of unexpected `Content-Type`
- Confirm + document whether `/docs`/`/redoc` are disabled outside `dev`
- Confirm the `Server` header is stripped/replaced at the Caddy edge
- Confirm `sslmode=require` on `DATABASE_URL`/`REDIS_URL` outside `dev`
- Confirm OCSP stapling is active on Caddy's ACME certs
- TLS cipher-suite/version check added to the `zap-baseline`/`zap-full` CI jobs (or a dedicated `testssl.sh`/`sslyze` step)
- Documented and actually-tested backup/restore runbook for the managed Postgres instance
- Move `ENCRYPTION_KEY`/`JWT_PRIVATE_KEY`/etc. to a secrets vault/KMS where the hosting platform supports one, instead of plain env vars
- Add a secret pepper (vault-held) on top of Argon2's per-hash salt
- Reflective-File-Download-safe headers on file download responses
- Fail prod deploys where `VIRUS_SCANNER=noop`
- Request-anomaly alerting (Prometheus alert rule on abnormal per-user/per-IP request volume)
- Confirm/require TLS between the backend and Postgres/Redis within the Docker network
- Log/alert on requests using an HTTP method not valid for the matched route

**Tests**

- One test per mechanical check where feasible: content-sniffing rejects a mislabeled file, file-count quota enforced, SBOM artifact produced, `/docs` 404s outside dev, deploy fails fast on `VIRUS_SCANNER=noop` in prod config
- TLS/OCSP/cipher-suite items are infra-verification, not unit-testable — documented as a one-time manual verification + CI scan instead

**Done when**

- [ ] Each bullet above is either implemented or has a documented reason it's deferred further
- [ ] `docs/adr/0001-security-baseline.md` rows updated (`partial` → `met` for whichever land; the rest stay `partial` with an explicit note on what's still pending)

---

### T-226 Production-ready change-email flow

> Filed by: T-218, which needed step-up re-auth to gate *something* called "email change" but found no such feature built yet. Added a minimal request/confirm implementation as part of T-218 itself (`AuthService.request_email_change`/`confirm_email_change`, `EmailChangeToken`, `POST /auth/change-email` + `/auth/change-email/confirm`, gated by `require_recent_auth`) so the step-up gate had a real endpoint to protect, but deliberately scoped it down. This task is the "do it properly" follow-up.
> needs: T-218

**Goal** Bring the minimal T-218 implementation up to the bar the rest of the auth module holds (T-216/T-217's OAuth-unlink/WebAuthn-removal flows, password reset, etc.).

**Build**

- Re-verification of the *old* address, not just the new one: today `request_email_change` only proves control of the new address (confirm-link email) -- ASVS V2.5.5-adjacent flows generally also want the requester to prove they're the current account holder at request time (e.g. current password), beyond just having a valid session recent enough to pass `require_recent_auth`
- A grace/undo window: a `cancel_email_change` action reachable from the *old* address's `email_change_requested` notification, so a real owner who sees an unexpected request can stop it before the confirm link is clicked
- Rate limiting specific to repeated change-email requests targeting the same `new_email` (distinct from the existing per-IP/per-user `sensitive_limit`) -- prevents using the confirm-email flow to spam an arbitrary inbox
- Decide and document what happens to `email_verified_at`, active sessions, and any pending invitations tied to the old address when the change completes -- T-218's minimal version leaves all three untouched
- Frontend: a real settings-page card (T-218 shipped none) plus the `/confirm-email-change` confirmation route, following `ChangePasswordCard`'s pattern in `apps/web/src/features/settings/SecuritySettingsPage.tsx` and `VerifyEmailPage.tsx`'s route-confirmation pattern respectively
- Revisit whether `EmailChangeToken` should carry a device/IP fingerprint the way `Session` does, for the admin-assisted recovery / audit story

**Tests**

- Old-address re-verification is enforced (wrong current password rejected)
- Cancel-from-old-address invalidates the pending token
- Per-target-email rate limiting on repeated requests
- Frontend: settings card submits and shows pending/confirmed state; confirmation route handles valid/expired/already-used tokens

**Done when**

- [ ] Old-address proof required alongside new-address proof
- [ ] Cancel path shipped and tested
- [ ] Frontend card + confirmation route shipped
- [ ] ADR/task docs updated to point at this task instead of T-218 for the email-change feature itself

---

## P3-C · Comprehensive E2E + load

> HTML ref: §"Testing strategy" → "The test pyramid" (E2E + load rows), "Frontend & mobile testing", "Load & resilience"
>
> The money paths get browser/device-level coverage; everything else stays at unit/integration level. E2E suites are small and ruthless — a flaky E2E test gets fixed or deleted the week it flakes.

### T-220 Playwright E2E — signup → verify → invite → upgrade (Chromium + WebKit)

> HTML ref: §"Testing strategy" → "E2E web — Playwright"
> needs: T-044, T-056

**Goal** The Phase 1 acceptance flow — the one that must never break — runs as a browser test on every PR, on two engines.

**Build**

- `apps/web/e2e/` with `playwright.config.ts`: projects `chromium` + `webkit`, base URL `https://localhost` (dev Caddy cert trusted via `ignoreHTTPSErrors` in config), retries 1 in CI, trace on first retry
- Helpers:
  - `mailhog.ts` — poll Mailhog's API (`:8025/api/v2/search?kind=to&query=<email>`) for the verification / invitation email, extract the action link
  - `stripe.ts` — two upgrade strategies behind an env switch:
    - default (every PR): stub the payment by POSTing a signed `checkout.session.completed` test event to the T-030 webhook endpoint (reuse the webhook test fixtures) and asserting the UI reflects the paid plan
    - `STRIPE_E2E=1` (nightly): drive the real Stripe Checkout test-mode page with card `4242 4242 4242 4242`
- The spec, one continuous journey with unique per-run emails:
  1. Register → "check your inbox" screen
  2. Fetch verification email via Mailhog → visit link → logged-in dashboard
  3. Create org → invite `teammate+<run>@example.com`
  4. Second browser context: fetch invite email, accept, assert membership visible to both sides
  5. Upgrade to paid plan → assert plan badge + gated feature unlocked
- Selectors: `data-testid` only — add them to the app where missing; no text or CSS-path selectors
- CI job `e2e-web` (in the test workflow, needs the compose stack): boots stack, seeds, runs both projects, uploads traces on failure

**Tests**

- The suite is the test. Additionally: a deliberately broken invite flow on a throwaway branch fails the job (one-time gate verification)

**Done when**

- [ ] Full journey green on Chromium + WebKit in CI
- [ ] Zero non-`data-testid` selectors (lint rule or grep in CI)
- [ ] Failure artifacts (trace, screenshot) uploaded and usable

---

### T-221 axe-core accessibility gate in Playwright

> HTML ref: §"Testing strategy" → "Accessibility — axe-core"
> needs: T-220

**Goal** WCAG A/AA violations fail the build — accessibility is a test, not a hope.

**Build**

- `@axe-core/playwright` scan step appended to the E2E suite, run against each key page in its realistic state: login, register, dashboard, members list (with data), billing/plan page, settings, notification center (open), file upload
- Fail on any violation with impact `serious` or `critical`; `moderate` and below reported as annotations
- `apps/web/e2e/a11y-allowlist.ts`: rule/selector pairs that are explicitly accepted, each with a `// a11y-ok: <reason + date>` comment — empty allowlist is the goal
- Scan both color schemes if the app has a dark mode; include the RTL locale once T-231 lands (add a follow-up marker)

**Tests**

- Meta-verification once: add `aria-hidden="true"` to the login form on a throwaway branch → job fails

**Done when**

- [ ] All key pages scanned in CI, zero serious/critical violations
- [ ] Allowlist empty or every entry justified and dated

---

### T-222 Maestro mobile E2E — login, deep link, logout

> HTML ref: §"Testing strategy" → "E2E mobile — Maestro"
> needs: T-047

**Goal** The three mobile flows that break most often — auth, deep links, session teardown — verified on a real emulator.

**Build**

- `.maestro/` flows:
  - `login.yaml` — launch → login with seeded credentials → dashboard visible
  - `deep-link.yaml` — open a magic-link/invite deep link (`forge://…`) cold and warm → correct screen with correct state
  - `logout.yaml` — logout → secure store cleared (assert login screen on relaunch, no auto-login)
- Seeded test user dedicated to mobile E2E (`make seed` addition), pointing the app at the local backend via env
- CI: run on a macOS runner with the iOS Simulator (or Maestro Cloud if a device farm is preferred — write the choice down); nightly rather than per-PR (emulator boot cost), `workflow_dispatch` for on-demand
- Document local usage in `apps/mobile/README.md`: `maestro test .maestro/` against the dev stack

**Tests**

- The flows are the tests; each asserts on visible UI state, not timing sleeps (use Maestro's `assertVisible` with timeouts)

**Done when**

- [ ] All three flows green locally on iOS Simulator + Android emulator
- [ ] Nightly CI job green
- [ ] Cold-start deep link verified (app killed, link opens correct screen after auth)

---

### T-223 k6 load scripts with asserted budgets

> HTML ref: §"Testing strategy" → "Load & resilience"
> needs: T-048, T-056

**Goal** A latency regression fails a pipeline, not a launch. p95 < 300 ms and error rate < 1% are asserted, not eyeballed.

**Build**

- `tests/load/` k6 scripts modelling realistic traffic:
  - `login-burst.js` — spike of logins (rate-limit-aware: distinct virtual users, not one hammered account)
  - `browse.js` — authenticated list pagination: members, notifications, files (cursor-follows-cursor)
  - `presign.js` — file presign + confirm loop
  - `mixed.js` — weighted combination approximating production (80% reads / 15% writes / 5% auth), ramping 0 → 50 VUs → hold 3 min
- Shared thresholds in every script:
  ```javascript
  thresholds: {
    http_req_duration: ["p(95)<300"],
    http_req_failed:   ["rate<0.01"],
  }
  ```
- Data setup: `make seed-load` creates orgs with realistic volumes (1k members, 10k notifications) so pagination is actually exercised
- `make load-test` runs the suite against the local compose stack; CI runs it **nightly** against a dedicated compose stack on a consistent runner class (per-PR runs on shared runners produce noise, not signal — say so in the README)
- Results exported as a JSON summary artifact; a trend note is appended to `tests/load/RESULTS.md` per run

**Tests**

- Thresholds present in every script (CI grep); nightly run green against seeded stack

**Done when**

- [ ] All four scripts pass budgets against the seeded local stack
- [ ] Nightly CI job wired with artifacts
- [ ] README explains why load gates are nightly, not per-PR

---

### T-224 Visual regression — Playwright snapshots on dashboard + settings

> HTML ref: §"Testing strategy" → "Visual regression — Playwright snapshots"
> needs: T-220

**Goal** Unintended UI changes on the two most-seen pages are caught by pixel comparison, with a sane update workflow so the suite doesn't rot.

**Build**

- `toHaveScreenshot()` specs for: dashboard (seeded, deterministic data), settings (profile + org tabs), and the app shell at mobile viewport (375px) + desktop (1280px)
- Determinism rules:
  - dynamic regions masked (`mask:` option): avatars, timestamps, notification badges
  - animations disabled (`reducedMotion: "reduce"` + CSS override), fonts loaded before capture
  - fixed seed data — snapshots never depend on run order
- Baselines committed per browser/platform under `apps/web/e2e/__screenshots__/`; CI compares with `maxDiffPixelRatio: 0.01`
- Update workflow documented in the spec file header: `pnpm e2e --update-snapshots` locally, reviewer eyeballs the diff in the PR — baseline changes must be intentional and visible in review

**Tests**

- Suite green twice in a row on CI (proves determinism, not just correctness)

**Done when**

- [ ] Snapshots stable across two consecutive CI runs
- [ ] A deliberate 2px layout change fails the job (verified once)
- [ ] Update workflow documented

---

## P3-D · i18n expansion + advanced

> HTML ref: §"Build phases" → "Phase 3" (i18n row)
>
> Foundation already in place from T-067–T-070 (`en` + `fi`, server-side locale resolution, localised emails). This section adds languages, RTL, translator tooling, and locale-aware routing. §0.11 remains binding: any hardcoded string found during this work is a defect to fix, not to note.

### T-230 Three more locales — de, es, fr

> needs: T-067

**Goal** The template ships with six locales (`en`, `fi`, `de`, `es`, `fr` + the T-231 RTL locale), and CI guarantees no locale silently rots.

**Build**

- Full translation of every `@forge/i18n` key into `de`, `es`, `fr` under `packages/i18n/locales/<locale>/` — namespace files mirror `en/` exactly
- Machine-translate first pass is acceptable **if** flagged: untranslated-quality keys carry a `"__review": true` sibling marker file listing keys needing human review; the goal state is zero markers
- `packages/i18n/scripts/check-locales.ts`:
  - For every non-`en` locale: fail if >5% of `en` keys are missing
  - Fail on **orphan keys** (present in a locale but absent from `en`)
  - Fail if any `en` value is empty
  - Wired into `pnpm lint` and CI
- Locale metadata registry in `packages/i18n/src/locales.ts`: `{ code, nativeName, dir }` — the language switcher (T-069) and mobile picker (T-070) render from this, no hardcoded lists
- Pluralisation spot-check: keys using count-based plurals verified for each new language's plural rules (German/French/Spanish differ from English in zero/one handling)

**Tests**

- `check-locales` passes for all locales; deliberately deleting 6% of `de` keys fails it
- Orphan-key detection: adding a `de`-only key fails
- Language switcher shows all six locales from the registry; switching to `de` renders a known translated string

**Done when**

- [ ] All locales ≥95% coverage, CI-enforced
- [ ] Zero orphan keys
- [ ] Switcher + mobile picker driven by the registry

---

### T-231 RTL support — add `ar`, verify layout flips

> needs: T-069, T-230

**Goal** One right-to-left locale (`ar`) works correctly across web and `packages/ui` — proving the layout system is direction-safe, not just translated.

**Build**

- `ar` locale added per T-230 conventions (`dir: "rtl"` in the locale registry)
- Web: `<html dir>` set reactively from the active locale's `dir` (i18next `languageChanged` listener); persisted across reloads
- `packages/ui` sweep: replace physical direction utilities with logical ones — `ml-*/mr-*` → `ms-*/me-*`, `pl-*/pr-*` → `ps-*/pe-*`, `left-*/right-*` → `start-*/end-*` where the intent is reading-direction-relative (deliberate physical positioning, e.g. a close button pinned top-right in both directions, gets a `/* rtl-ok: <reason> */` comment)
- ESLint/grep CI rule: physical direction classes in `packages/ui` and `apps/web` require the `rtl-ok` annotation
- Directional icons (chevrons, arrows, "back") flip via a `useDirection()` hook from `packages/hooks`
- Mobile: document `I18nManager` behaviour (RTL on native requires app restart); enable and verify the auth + settings screens; full mobile RTL polish may be deferred with a written note, web may not

**Tests**

- Switching to `ar` sets `dir="rtl"` on `<html>` (Playwright assertion)
- Visual snapshot (T-224 mechanism) of dashboard + settings in `ar` — sidebar, nav, and text alignment mirrored
- CI grep: zero unannotated physical direction classes in `packages/ui`
- axe-core pass (T-221) on the `ar` locale

**Done when**

- [ ] Dashboard + settings render correctly mirrored in `ar`
- [ ] Logical-properties rule enforced in CI
- [ ] RTL snapshots committed as baselines

---

### T-232 Translator workflow — export, import, source-of-truth checks

> needs: T-067, T-230

**Goal** A non-developer can translate the product without touching git, and the developer loop for getting those translations back in is one command.

**Build**

- `pnpm i18n:export [--locale de] [--missing-only]` → writes `i18n-export/<locale>.csv` (columns: key, en value, current translation, context/namespace) — CSV because every tool and every human can open it; also emit gettext `.po` for tool compatibility
- `pnpm i18n:import i18n-export/de.csv` → merges back into the locale JSONs, preserving key order, failing on unknown keys
- TMS integration: document (not implement) the Crowdin and Lokalise setups in `docs/runbooks/translations.md` — config file examples, sync direction (repo is the source of truth for `en`, TMS is upstream for translations), and the manual CSV loop as the zero-dependency fallback
- Source-of-truth CI checks (extends T-230's `check-locales`):
  - `en` is the **only** locale required at 100% — a key added to any other locale first is an orphan (already failing per T-230)
  - export/import round-trip is lossless: `export → import` produces zero diff (CI job on the `en` locale)

**Tests**

- Round-trip test: export `de`, re-import unchanged, `git diff --exit-code` clean
- Import with an unknown key → exits non-zero with the offending key named
- `--missing-only` export contains exactly the keys absent from the target locale

**Done when**

- [ ] Export + import commands work and are documented
- [ ] Round-trip CI check green
- [ ] `docs/runbooks/translations.md` covers TMS and manual workflows

---

### T-233 Locale-aware URL routing on web

> needs: T-069, T-042

**Goal** The locale lives in the URL (`/en/settings`, `/fi/settings`) so pages are shareable, bookmarkable, and indexable per language — and T-305 (marketing SEO) can rely on the same convention.

**Build**

- TanStack Router: top-level `/$locale` layout route wrapping the app route tree; `$locale` validated against the T-230 registry (unknown → 404)
- Redirect rules:
  - `/` and any locale-less deep link → resolved locale prefix (resolution order mirrors the backend: user preference → `Accept-Language` → `en`), via a redirect route, HTTP-level where SSR/edge applies
  - Language switcher (T-069) rewrites the current path's locale segment in place — never loses the page the user is on
- The active URL locale wins over the stored preference for rendering (a shared `/de/...` link shows German even to an `en` user), but does not silently overwrite `User.locale` — switching explicitly does
- `<link rel="canonical">` + `hreflang` alternates emitted per page via a small head component (required by T-305; app pages behind auth get canonical only, `noindex` stays on)
- Auth redirects, invite links, and email deep links updated to carry the locale segment; legacy locale-less links keep working via the redirect rule (backwards compatibility test)

**Tests**

- `/settings` visited by a `fi` user → redirected to `/fi/settings`
- `/de/settings` renders German regardless of stored preference; explicit switch updates `User.locale`
- Unknown locale `/xx/settings` → 404
- Old bookmarked locale-less URL → 301/redirect to prefixed equivalent
- Switcher preserves path + query across locale change

**Done when**

- [x] All routes locale-prefixed; redirects cover legacy links
- [x] hreflang/canonical component emits correct alternates
- [x] E2E (T-220) still green with prefixed URLs

---

### T-234 Locale-aware push notifications — verified end-to-end

> needs: T-068, T-105

**Goal** Prove (don't assume) that a push notification reaches each recipient in their own language — the full chain from event to Expo payload.

**Build**

- Audit the chain built in T-068/T-102/T-105: event → `NotificationService` fan-out → per-recipient locale lookup (`User.locale`) → translated title/body via backend `t()` → Expo payload. Fix any point where the sender's locale, `request.state.locale`, or a default leaks into another recipient's message (the classic bug: translating once before the fan-out loop instead of per recipient)
- Localise at **send time** per recipient, matching the email rule in §0.11
- Push payload carries `"locale"` in its data dict so the mobile client can localise any client-side rendering consistently
- Integration test fixture: capture outbound Expo API calls (mock transport) and assert on payload content

**Tests**

- Org with user A (`locale="en"`) and user B (`locale="fi"`); trigger one `payment_failed` event notifying both → A's push title is the English string, B's is the Finnish string, from the same event
- Recipient with an unsupported stored locale falls back to English (never raw key strings)
- Recipient locale changes between event creation and job execution → job uses the locale at send time
- In-app + email channels for the same event are also per-recipient localised (regression net around T-102)

**Done when**

- [x] Mixed-locale fan-out test passes
- [x] Fallback + late-change tests pass
- [x] Any bug found in the audit is fixed with its own regression test

---

## P3-E · Performance tuning

> HTML ref: §"Build phases" → "Phase 3" (performance row) | §"Best practices" → "Performance"
>
> §0.9 already mandates async everywhere, ARQ for slow work, and Redis on hot reads. This section verifies those claims with tests and budgets, and adds the infrastructure pieces (PgBouncer, key rotation at scale) that only matter under load.

### T-240 Redis cache layer — invalidation proven by tests

> needs: T-021

**Goal** The three hot reads (current user, org settings, plan features) are cached with 60 s TTL and — the part that actually breaks in production — invalidated correctly on every write path.

**Build**

- `get_current_user` cache exists from T-021 (`core/dependencies.py`, with `invalidate_user_cache`). This task completes the set:
  - `backend/app/core/cache.py`: small typed helper — `cached_json(key, ttl=60)` get/set + `invalidate(key)`; all cache keys defined in one module (`cache_keys.py`-style constants), never inline f-strings at call sites
  - Org settings cache (`org:{org_id}:settings`) read in `get_current_org`; plan features cache (`org:{org_id}:plan`) read by the billing/feature-gate path
- Invalidation matrix — every write that must bust each key, enumerated and wired:
  - user cache: profile update, locale change, deactivation, role/permission change, `is_superadmin` flip, GDPR delete
  - org settings: org update, org deletion
  - plan features: subscription change (checkout completed, plan override T-141, dunning downgrade), feature-flag toggle (T-122)
- Serialization: cached payloads are versioned (`{"v": 1, ...}`) — a deserialization mismatch is a cache miss, never an exception
- Redis unavailable → cache layer degrades to direct DB reads (log a warning, don't 500) — consistent with T-206's philosophy

**Tests**

- Query-count proof (uses T-243's fixture): two consecutive `get_current_user` calls → second issues zero DB queries
- Each row of the invalidation matrix: write → immediately read → fresh value (parametrised test over the matrix)
- Deactivated user is rejected on the next request, not after TTL expiry (security-relevant invalidation)
- Versioned-payload mismatch → treated as miss
- Redis down (mock) → reads still succeed from DB

**Done when**

- [x] All three caches live with the invalidation matrix fully tested
- [x] Deactivation propagates immediately
- [x] No inline cache-key strings (grep check)

---

### T-241 TanStack Virtual on member list + notification list

> needs: T-044, T-104

**Goal** The two lists that grow unboundedly render a constant number of DOM nodes regardless of data size.

**Build**

- `@tanstack/react-virtual` (confirmed-clean family per §0.12 — still pin latest) on:
  - members list (T-044 settings/members page)
  - notification center list (T-104)
- Integration with the existing cursor-based `useInfiniteQuery`: virtualizer range end nearing the loaded count triggers `fetchNextPage` — scroll position is the pagination driver
- Preserve semantics: row height measurement for variable-height notification items, `role="list"` / `role="listitem"` on the scroll container contents, keyboard navigation (arrow keys move focus through virtual rows without scroll jank)
- Scroll restoration on back-navigation (TanStack Router scroll restoration + virtualizer `initialOffset`)

**Tests**

- Render with 1,000 seeded items → DOM contains < 50 row nodes (RTL assertion on the container)
- Scrolling to the bottom of loaded data triggers exactly one next-page fetch (MSW)
- Keyboard: focus moves through rows across a virtualization boundary
- axe-core (T-221) still passes on both pages with virtualization active

**Done when**

- [x] Both lists virtualized, DOM-node budget asserted in tests
- [x] Infinite scroll + a11y + scroll restoration verified — infinite scroll,
      keyboard-across-a-boundary, and axe-core are asserted in vitest against
      the real virtualizer (`apps/web/src/test/virtualLayout.ts` supplies the
      layout jsdom can't); scroll restoration is wired end to end but only
      observable in a real browser
- [ ] No regression in the T-224 snapshots — not re-run here (needs the Docker
      stack + Linux-generated baselines). The three snapshot targets
      (dashboard, settings-profile, settings-org) don't include the member
      list, and the notification panel is closed in all of them, so no
      baseline change is expected

---

### T-242 Per-route bundle budgets in CI

> needs: T-040

**Goal** Bundle growth is a reviewed decision, not an accident discovered in Lighthouse six months later.

**Build**

- `size-limit` (with the file preset) configured in `apps/web/package.json` against the built output:
  - initial JS for the entry route (login) — budget ~150 KB gzipped
  - each lazy route chunk (dashboard, settings, billing, notifications, admin) — per-chunk budgets set from current size + 10% headroom
  - total CSS budget
- Budgets are explicit numbers committed in config — raising one requires editing the file in the PR, which is the point
- `rollup-plugin-visualizer` wired into the build (`pnpm build --analyze`) emitting `stats.html`; CI uploads it as an artifact on every build so regressions are diagnosable without reproducing locally
- CI job `bundle-budget` in the frontend workflow: build → `size-limit` → fail on any exceeded budget, printing the per-chunk delta table
- `docs/runbooks/bundle-budget.md`: what to do when the gate fails (analyze artifact → lazy-load / replace dep / consciously raise budget with reviewer sign-off)

**Tests**

- CI job green at current sizes; deliberately importing a large lib eagerly on a throwaway branch fails it (one-time verification)

**Done when**

- [ ] Budgets committed for entry + every lazy chunk
- [ ] `stats.html` artifact on every CI build
- [ ] Gate failure verified once, runbook written

---

### T-243 pytest query-count fixture — N+1 detection on every list endpoint

> needs: T-048

**Goal** An N+1 query introduced on any list endpoint fails a test with a diff of the executed SQL — the day it's introduced, not when the table grows.

**Build**

- `backend/tests/fixtures/query_counter.py`:

  ```python
  @asynccontextmanager
  async def assert_max_queries(session, n: int):
      """Counts statements via SQLAlchemy's before_cursor_execute event
      on the test connection. On failure, prints every captured statement
      so the N+1 is visible in the diff, not just a number."""
  ```

- Parametrised test module `tests/test_query_budgets.py` covering every list endpoint (members, invitations, notifications, files, invoices T-133, audit log, admin directory T-141):
  - Seed 3 items → record query count; seed 30 items → assert count is **identical** (constant-query property — stronger and less brittle than a magic number)
  - Budget registry: `{endpoint: max_queries}` dict in the test module; a new list endpoint without an entry fails a meta-check (mirrors the T-049 sweep pattern)
- Fix any N+1 found while wiring this: the usual suspects are relationship access after list load — fix with `selectinload` per §0.3 (never `joinedload` for collections)

**Tests**

- The suite is the deliverable; plus: meta-check fails when a new `list_`-style route lacks a budget entry (verified by adding a dummy route in the test)

**Done when**

- [x] Constant-query property holds on every list endpoint — 15 endpoints,
      each measured at 3 rows and again at 30. `GET /orgs/{org_id}/invitations`
      (MEMBERS_MANAGE, cursor-paginated) was built here: the brief named an
      invitations list, but the tenancy module only created, previewed and
      accepted them
- [x] Every N+1 found is fixed and stays covered — three in the admin module
      (audit feed, user detail, dashboard payment failures); all three were
      bare-UUID lookups with no ORM relationship to `selectinload`, so each is
      a batched `IN` via the new `list_by_ids` repository methods
- [x] Meta-check guards future endpoints

---

### T-244 PgBouncer in transaction mode (prod)

> needs: T-005

**Goal** Connection count to Postgres stays flat as backend replicas scale — without breaking asyncpg.

**Build**

- `docker-compose.prod.yml`: `pgbouncer` service (bitnami/pgbouncer or edoburu image — pick and pin):
  - `pool_mode=transaction`; `default_pool_size` sized to `max_connections * 0.8` minus a superuser reserve; auth via userlist file from env
  - healthcheck (`psql -c 'SHOW POOLS'` or TCP), backend `depends_on` pgbouncer healthy
  - backend `DATABASE_URL` points at pgbouncer; **Alembic migrations connect directly to Postgres** (session-level locks + DDL don't mix with transaction pooling) — separate `MIGRATIONS_DATABASE_URL` setting, defaulting to `DATABASE_URL` in dev
- The asyncpg tradeoff, handled not hand-waved: transaction pooling breaks server-side prepared statement reuse across connections →
  - `create_async_engine(..., connect_args={"statement_cache_size": 0})` when `PGBOUNCER_ENABLED=true` (new setting)
  - documented in `docs/adr/0002-pgbouncer.md`: why transaction mode, the prepared-statement tradeoff, measured overhead, and the alternative (server-side cache in newer asyncpg) if it becomes a bottleneck
- Dev compose stays direct-to-Postgres — one less moving part locally

**Tests**

- Prod-compose integration (CI service matrix or dedicated job): app boots through pgbouncer, `/health/ready` 200, a request that opens two sequential transactions succeeds
- With `statement_cache_size=0`: repeated identical queries succeed (no `DuplicatePreparedStatementError`)
- Migration path: `alembic upgrade head` via `MIGRATIONS_DATABASE_URL` succeeds while app traffic flows through pgbouncer

**Done when**

- [x] Prod stack runs all traffic through pgbouncer, migrations direct —
      `edoburu/pgbouncer:v1.25.2-p0`, `pool_mode=transaction`, and the new
      `pgbouncer-prod-compose` CI job boots the real prod compose stack and
      migrates over `MIGRATIONS_DATABASE_URL` while pooled traffic flows.
      Two pre-existing defects had to be fixed for that stack to boot at all:
      its postgres service had no `POSTGRES_PASSWORD` (now `.env.postgres`),
      and both Python services' build context couldn't satisfy
      `backend/Dockerfile`'s `COPY`s (now the repo root, as in CI)
- [x] Prepared-statement regression test passes — `tests/test_pgbouncer.py`
      runs a real pgbouncer in front of a real Postgres and asserts *both*
      directions: the unconfigured engine deterministically fails with
      `InvalidSQLStatementNameError`, the configured one doesn't. Three knobs
      were needed, not the one the brief named: `statement_cache_size=0` plus
      `prepared_statement_cache_size=0` (SQLAlchemy's own per-connection LRU,
      which is the layer that actually fires) plus UUID statement names
- [x] ADR documents the tradeoff with numbers — `docs/adr/0002-pgbouncer.md`:
      +0.89ms median for the proxy hop, +0.65ms median for the disabled
      statement cache, 20 app connections resolving to 4 Postgres backends,
      and ~115 leaked prepared statements per server connection without
      `server_reset_query_always`

---

### T-245 Production-ready encryption key rotation job

> needs: T-009.5, T-034

**Goal** Rotating `ENCRYPTION_KEY` on a database with millions of encrypted rows is a routine, observable, resumable operation — upgrade T-009.5's stub to the real thing.

**Build**

- `backend/app/workers/jobs/encryption.py`, rewritten:
  - **Column discovery**: walk SQLModel metadata for every column whose type is `EncryptedString` — no hardcoded table list; new encrypted columns are picked up automatically
  - **Batched cursor scan** per (table, column): keyset pagination on PK (no OFFSET, §0.3), `batch_size` configurable (default 500), per-batch commit
  - **Checkpointing**: progress (`table`, `column`, `last_pk`, counts) persisted via `JobRecord` (T-034) after every batch — a crashed or redeployed worker resumes from the checkpoint, and re-running is idempotent (already-current ciphertext is skipped by token-prefix check)
  - **Dry-run mode**: counts rows needing re-encryption per column, writes nothing, prints the work estimate
  - **Metrics** (T-202): `forge_reencrypt_rows_total{table,column}`, `forge_reencrypt_remaining` gauge, batch duration histogram
  - **Throttle**: configurable inter-batch sleep so the job never saturates the pool (§0.9: `max=20`)
- `docs/runbooks/encryption-key-rotation.md`, the operation end-to-end:
  1. Generate new key → set as `ENCRYPTION_KEY`, demote old key to `ENCRYPTION_KEY_OLD` → deploy (MultiFernet reads both)
  2. Dry-run → review counts
  3. Run job → watch the `remaining` gauge → handle a mid-run crash (resume) and a mid-run rollback (keys unchanged, safe)
  4. Verify zero rows remain on old key → remove `ENCRYPTION_KEY_OLD` → deploy
  5. Rollback story at every step

**Tests**

- Discovery finds all `EncryptedString` columns (assert against known models; add a throwaway model → it's discovered)
- Rotation: 1,500 rows under key A → run with key B primary → all decrypt under B alone; job report matches
- Resume: kill after batch 1 (simulated) → re-run → completes without re-processing batch 1 (checkpoint assert)
- Dry-run writes nothing (query-count/mutation assert)
- Mixed state mid-rotation: reads of not-yet-rotated rows still succeed via MultiFernet

**Done when**

- [x] Stub replaced; discovery, checkpointing, dry-run, metrics all tested —
      `reencrypt_column_job(table=, column=)` is gone, replaced by
      `rotate_encryption_keys_job` (+ a `python -m app.workers.jobs.encryption`
      CLI, since `WorkerSettings.job_timeout` is 300s and a real rotation
      isn't a five-minute job). Discovery walks `SQLModel.metadata` and
      refuses, with a named error, a composite PK or an encrypted PK — neither
      can be keyset-paged. The scan goes through a Text-typed mirror `Table`
      rather than `text()` with interpolated identifiers, so the job moves raw
      tokens with SQLAlchemy still doing the quoting
- [x] Runbook walks the full rotation including crash recovery —
      `docs/runbooks/encryption-key-rotation.md`, five steps with expected
      output and a rollback each. The step that mattered most to write down:
      the verification scan *before* dropping `ENCRYPTION_KEY_OLD` must report
      `rotated_total: 0`, because a row still on the old key when that key
      goes away gets its ciphertext re-encrypted as if it were plaintext
- [x] 100% coverage on the job module — 248 statements, 44 branches, 18 tests
      against a real Postgres + Redis. Two things the spec didn't call out but
      the build needed: the `forge_reencrypt_remaining` gauge is published
      through Redis (T-202's `forge_active_sessions` split — the job's process
      doesn't serve `/metrics`), and `[tool.coverage.report] exclude_also`
      now drops `if __name__ == "__main__":`, which otherwise makes a module
      with a CLI uncoverable by construction

---

### T-246 Read-replica plumbing (optional — scale-triggered)

> needs: T-005

**Goal** The code path for routing reads to a replica exists and is tested against a single database — so enabling a real replica later is an infra change, not a refactor. **Do not provision an actual replica now.**

**Build**

- `backend/app/core/db.py`: `write_engine` (existing) + `read_engine` — when `READ_REPLICA_URL` (new optional setting) is unset, both names bind to the same engine; zero behaviour change by default
- Session routing: `get_session(read_only: bool = False)` — explicit opt-in per repository method via a `use_read_replica=True` argument on safe read methods (`list_cursor`, `get` on non-auth paths); **never** implicit by HTTP verb
- Hard rules, documented in the code and the ADR:
  - auth reads (sessions, tokens, permissions) always hit the primary — replication lag on auth is a security bug
  - read-your-writes flows (create → immediately list) stay on the primary within the request
- `docs/adr/0003-read-replica.md`: replication-lag consistency implications, which query classes are lag-tolerant, and the activation trigger (sustained primary CPU > 60% from read traffic, or pool exhaustion from reads — with dashboard links from T-204/T-205), plus the activation checklist

**Tests**

- `READ_REPLICA_URL` unset → both engines are the same object; all existing tests pass unchanged
- With a second testcontainers Postgres as a fake replica: `use_read_replica=True` queries hit it (assert via connection inspection); auth repository queries hit the primary regardless
- Flag on a mutating method → raises at definition time (misuse guard)

**Done when**

- [x] Plumbing merged, default-off, zero behaviour change (full suite green with flag unset)
      — `READ_REPLICA_URL` unset makes `read_engine` *the same object* as
      `write_engine` (and `read_session_factory` the same as
      `async_session_factory`), so a flagged read resolves to the caller's own
      session: no second pool, no branch. Full backend suite green (1735
      passed, 6 skipped)
- [x] Routing + auth-pinning tests pass — `tests/test_read_replica.py` runs a
      second testcontainers Postgres as a stand-in replica (deliberately *not*
      replicating: rows seeded on one side only make "which database answered"
      decidable) and asserts routing at the connection level via
      `before_cursor_execute`. Auth pinning is enforced twice: auth
      repositories expose no `use_read_replica` at all, and
      `db.PRIMARY_ONLY_TABLES` pins per *statement*, so a join or subquery
      that reaches auth state is pinned too
- [x] ADR captures when to actually enable this — `docs/adr/0003-read-replica.md`:
      both triggers point at named panels (`forge-database`'s "Pool
      utilization" and "hall of shame", `api-overview`'s in-flight requests),
      the cheaper fixes to rule out first, a per-query-class eligibility
      table, and a 7-step activation checklist (replica pool gauge +
      replication-lag alert land *before* the second call site)
- Two things the brief's shape had to change to survive contact: `get_session`'s
      `read_only` is annotated as a FastAPI dependency rather than a bare
      `bool = False`, because a bare default publishes `?read_only=` as a query
      parameter on all ~250 routes that depend on it — i.e. it would make read
      routing client-settable, the one thing this design forbids (asserted
      against the real OpenAPI schema). And the misuse guard rejects the flag on
      any method not *named* like a read (`create`, `get_or_create` and an
      ambiguous `sync_everything` all raise `TypeError` at class-definition
      time), rather than trying to detect mutation itself

---

## P3-F · Module scaffold

> HTML ref: §"Extending the template" (entire section)
>
> The measure of the template is how fast "new idea" becomes "first endpoint". This section makes the module pattern copyable in one command and gives decisions and operations their standing document types.

### T-250 `_template` module — all five files, working example

> needs: T-009, T-021

**Goal** `backend/app/modules/_template/` (currently an empty `.gitkeep`) becomes a complete, compilable reference module demonstrating every §0 law in ~200 lines.

**Build**

- The five files per §2 module anatomy, using `__MODULE_NAME__` / `__ModelName__` placeholders where the generator will substitute, around one example org-scoped entity `Item`:
  - `models.py` — `Item` with `UUIDPKMixin + TimestampMixin + SoftDeleteMixin + OrgScopedMixin`, one indexed filter column
  - `schemas.py` — separate `ItemCreateRequest` / `ItemUpdateRequest` / `ItemPublic` (§0.4: no mass assignment)
  - `repository.py` — extends `BaseRepository[Item]`; one custom query method showing the org-scope + soft-delete pattern
  - `service.py` — create/list/update/delete raising typed `DomainError`s; one plan-limit check example (`PlanLimitError`)
  - `router.py` — CRUD under `/api/v1/__MODULE_NAME__/`, envelope responses, `require_permission` gates, cursor pagination, `api_limit`
  - `README.md` — public interface, events emitted, and a checklist of what to customise after generating
- Matching test template `backend/tests/modules/_template_test.py.tmpl`: cross-tenant isolation test, RBAC gate test, cursor pagination test — the non-negotiables from §0.8 pre-written
- Exclusions verified, not assumed: `_template` is skipped by the module registry/loader (T-060 — confirm the underscore convention is enforced there), by router registration (T-061), and by Alembic autogenerate (T-062) — add explicit guards if any relies on convention alone
- The template code itself passes `ruff` + `mypy --strict` (placeholders chosen to be valid identifiers)

**Tests**

- `ruff check` + `mypy --strict` pass on `_template/` as-is
- App boots with the template present: no `_template` routes in the OpenAPI schema, no `item` table in autogenerate output
- Registry test: `_template` absent from loaded modules

**Done when**

- [x] All five files + README + test template exist and are lint/type-clean —
      `models.py` / `schemas.py` / `repository.py` / `service.py` /
      `router.py` + `README.md` around one org-scoped `Item`, plus
      `tests/modules/_template_test.py.tmpl`. `ruff check`, `ruff format
      --check` and `mypy --strict` all pass on `_template/` exactly as it
      sits, which is only possible because the module imports itself
      *relatively* (`from .models import Item`): an absolute
      `app.modules.__MODULE_NAME__.models` is unresolvable until the module
      is generated, so mypy could never check the template itself — and the
      relative form survives the rename for free. `.gitkeep` is gone; the
      directory has real contents now
- [x] Exclusion from registry, routing, and migrations proven by tests —
      `tests/test_module_template.py`, plus
      `test_autogenerate_never_proposes_the_template_scaffolds_table` in
      `test_conditional_migrations.py`. Registry and routing turned out to
      exclude the scaffold *by construction* rather than by the underscore
      convention (`ModuleName` is a closed `Literal`; `build_router()` and
      `app/models/__init__.py` are hand-written lists), so those tests assert
      the property and nothing had to change. Alembic was the one place
      relying on nothing at all: `target_metadata` **is** the process-wide
      `SQLModel.metadata`, so a single import of the scaffold anywhere in the
      process registers `Item` in front of autogenerate — and
      `tests/test_read_replica.py` already does exactly that, sweeping every
      `app.modules.*.repository` through `pkgutil`. `alembic/env.py` now
      drops any object whose (parent) table name still carries the
      `__MODULE_NAME__` placeholder: exact, not heuristic, because generation
      substitutes every occurrence away (T-251). Verified by deleting the
      guard and watching the test fail
- [x] Example demonstrates envelope, RBAC, cursor pagination, org-scoping, and typed errors —
      five CRUD routes, each with `@api_limit`, `require_permission(...)` and
      an `Envelope`/`Meta` response; `list_items` pages by cursor with the
      next one in `meta.next_cursor`; `ItemRepository.count_active` is the
      worked example of the org-scope + soft-delete pattern a hand-written
      query has to repeat; `NotFoundError` / `UnprocessableEntityError` /
      `PlanLimitError` all leave the service typed
- Four decisions the brief's shape didn't settle. (1) `require_permission`
      alone gates every route and yields the `Membership`, so `org_id` and
      the actor's `user_id` come from the gate rather than from the client —
      but a `<module>:read` / `<module>:manage` pair cannot exist before the
      module does, so `router.py` starts from two named constants pointing at
      existing permissions, and repointing them is step 2 of the README's
      checklist. (2) The plan-limit example is a module constant, not a
      `require_feature` entitlement: a gate that denies would make a
      brand-new module 402 until someone configured a plan, and coupling the
      scaffold to billing would break it for products that disable billing
      (T-065) — the README points at `storage.service._check_quota` for the
      real thing. (3) The table is `__MODULE_NAME___item`, not `item`, so two
      modules generated before either entity is renamed don't collide on
      `SQLModel.metadata` at import time. (4) The test template drives the
      module through a small FastAPI app it assembles itself, overriding only
      `get_current_org` — that is what lets the generated tests be green
      *before* the router is registered and before the migration exists
      (README steps 3 and 4), and it is what lets one test choose the calling
      org and another the calling role. `[tool.coverage.run] omit` now
      excludes `_template` — it ships uncalled by design, and measuring it
      would charge every future template line against CI's
      `--cov-fail-under=85`

---

### T-251 `make new-module NAME=foo` — generation, not just copying

> needs: T-250

**Goal** One command turns the template into a registered, tested, compiling module — the existing naive `cp -r` target grows the renaming and guidance that make it actually usable.

**Build**

- Upgrade the `new-module` Makefile target (delegating to `backend/scripts/new_module.py` — logic in Python, not shell):
  1. Validate `NAME` (snake_case, not a reserved/existing module name; clear error otherwise)
  2. Copy `_template/` → `modules/<name>/`, substituting `__MODULE_NAME__` → `<name>`, `__ModelName__` → `<Name>` in file contents
  3. Copy the test template → `tests/modules/test_<name>.py` with the same substitution
  4. Print the manual follow-ups it deliberately does **not** do (each with file:line pointers): register in the module registry (T-060), add to `product.config` toggles (T-059), run `alembic revision --autogenerate -m "<name>_tables"`, rename `Item` to the real entity
- Generated module must pass `ruff` + `mypy --strict` and its generated tests immediately (against the generic `Item` entity) — a broken starting point poisons every future module
- `docs/runbooks/new-module.md`: the full loop from `make new-module` to first deployed endpoint, including the §"Extending the template" workflow from the HTML plan

**Tests**

- CI smoke: `make new-module NAME=zztest` → generated module passes `ruff`, `mypy --strict`, and its own tests; then cleaned up (temp workspace)
- `make new-module` without `NAME`, with `NAME=Foo-Bar`, with `NAME=auth` → each fails with a usable message
- Substitution completeness: no literal `__MODULE_NAME__` remains anywhere in the generated output (grep)

**Done when**

- [x] Generated module is green (lint + types + tests) with zero manual edits —
      `make new-module NAME=zztest` → `ruff check`, `ruff format --check`,
      `mypy --strict` and its own 5 tests all pass untouched, verified end to
      end here. The generated tests are green *before* the router is
      registered and before the migration exists (T-250's test template
      assembles its own FastAPI app), so "green with zero edits" is a real
      starting point rather than a definition. `ruff format` runs over the
      output as a guard: the placeholder is 15 characters wide, so
      substitution moves every line carrying it, and
      `test_generation_changes_nothing_that_ruff_would_change` pins the fact
      that today the formatter never actually has to rewrite anything — the
      day a template edit makes it a repair, that test says so
- [x] Follow-up checklist printed with accurate pointers — eight steps
      mirroring the module README's, each `file:line` resolved against the
      real tree at generation time by searching for an anchor rather than
      being typed, so they cannot rot;
      `test_every_shared_pointer_resolves_in_the_real_tree` fails if an
      anchor is ever renamed away. Writing the pointers turned up one the
      README was missing: `alembic/env.py`'s `load_models_for_migration()` is
      a hand-written import list, so `alembic revision --autogenerate`
      proposes **nothing at all** for a new module until it is listed there —
      silent, and indistinguishable from "no schema changes". The template
      README's step 4 now says so too
- [x] CI smoke test guards the generator itself — two steps in the `test`
      job: a real `make new-module NAME=zztest` in the real checkout
      (placeholder grep, ruff, `mypy --strict`, its own pytest run, then
      deleted in a trap), plus the three refusal cases. The smoke has to run
      in the real tree rather than a temp workspace because mypy and pytest
      both need `app.modules.zztest` genuinely importable; it deletes the
      module in the same step because while it exists its `Item` is on the
      process-wide `SQLModel.metadata` for anything that sweeps
      `app.modules.*` — the alembic placeholder guard covers the
      *un*generated scaffold only, by design. `backend/tests/test_new_module_script.py`
      (59 tests) covers everything that doesn't need a real checkout, against
      a scratch backend root in `tmp_path`
- Three decisions the brief didn't settle. (1) `NAME` is capped at 27
      characters, which is not arbitrary: the scaffold's composite index is
      `ix_<name>_item_org_id_status_created_at_id`, 36 characters of fixed
      text, and Postgres truncates identifiers to 63 bytes *silently* — a
      longer name would not fail, it would collide with the next long name
      months later. The test re-derives the limit from the rendered template
      rather than trusting the arithmetic. (2) Reserved names are read out of
      the tree (module directories, `ModuleName`'s `Literal` members parsed
      with `ast`, Python keywords) instead of being listed in the script, so
      `NAME=file_storage` is refused even though no such directory exists.
      The parse rather than an import keeps the generator runnable from a
      bare checkout with no installed `app` and no `.env`. (3)
      `docs/runbooks/new-module.md` follows T-252's not-yet-written runbook
      template structure (trigger · pre-checks · steps with expected output ·
      verification · rollback · escalation) rather than inventing a shape
      T-252 will then have to retrofit

---

### T-252 ADR + runbook templates

> needs: —

**Goal** Decisions and operations each have a standing document type with a template, an index, and a lifecycle — so the docs habit survives contact with deadlines.

**Build**

- `docs/adr/_template.md`: number/title · status (`proposed` / `accepted` / `superseded by ADR-NNN`) · date · context · decision · consequences (positive **and** negative) · alternatives considered. Kept to one page — an ADR that takes >30 min to write doesn't get written
- `docs/adr/README.md`: index table (number · title · status · date) + the rule for what deserves an ADR: any decision that is expensive to reverse or that a future maintainer will ask "why on earth" about
- Backfill the ADRs this plan already owes, even if brief: 0001 security baseline (T-213), 0002 PgBouncer (T-244), 0003 read replica (T-246), 0004 marketing framework choice (placeholder — decided in T-300)
- `docs/runbooks/_template.md`: trigger (when to reach for this) · pre-checks · steps with expected output per step · verification · rollback · escalation. Retrofit heading structure onto the existing runbooks (deploy-render, module-toggle-guide, degraded-mode, maintenance-mode, etc.) without rewriting their content
- CI check: every `docs/adr/*.md` (except template/README) carries a status line; index is regenerated or verified by script so it can't drift

**Tests**

- CI: ADR status-line + index-consistency script green; a statusless ADR fails it

**Done when**

- [x] Both templates committed; ADR index live — `docs/adr/_template.md`
      (status · date · context · decision · consequences · alternatives, one
      page, with the "if it takes >30 min it doesn't get written" rule stated
      in the template itself) and `docs/runbooks/_template.md`. `docs/adr/README.md`
      carries the what-deserves-one rule (expensive to reverse, **or** a future
      maintainer asks "why on earth"), what explicitly doesn't, the
      `Proposed → Accepted → Superseded by ADR-NNNN` lifecycle, and an index
      generated between markers rather than hand-maintained
- [x] Owed ADRs exist (0001–0004) — 0001–0003 were already written, and each
      gained the `**Date:**` line the index reads plus the
      `## Alternatives considered` the template requires. That last section was
      not filler: the material was already inline in both 0002 (the pooling-mode
      table, `NullPool`, pgcat/Odyssey) and 0003 (the "not by HTTP verb"
      argument) — it just wasn't findable by anyone asking "was X considered".
      0004 is the placeholder T-300 replaces: no framework picked, but the four
      constraints that decide it recorded while they are still visible, of which
      one is the real asymmetry — `packages/ui` ships **zero** `"use client"`
      directives (verified, not assumed), so an RSC-first framework cannot
      consume the shared component package as it stands and Astro's islands can
- [x] Existing runbooks follow the template's heading structure — ten
      retrofitted to `Trigger · Pre-checks · Steps · Verification · Rollback ·
      Escalation`, keeping their own extra sections (a runbook that needs a
      per-integration behaviour table between Trigger and Steps should have
      one, so extras are allowed anywhere and only the six are ordered). Four
      documents in `docs/runbooks/` are not procedures and carry
      `<!-- runbook-template-exempt: <reason> -->` instead: `log-shipping.md`
      (reference), `pentest-prep.md` (engagement inventory), and the two
      `phase*-verification.md` files (records of passes that already ran).
      Writing the missing sections was where this stopped being clerical —
      "what is the point of no return" has a real answer in five of the ten
      (key rotation's step 5, a restore's cutover soak, a finalized Stripe
      invoice, the deleted `TOTPSecret`, the migration that runs before the
      deploy it can't be rolled back with) and none of them were written down
- [x] CI check green, and a statusless ADR fails it —
      `backend/scripts/check_docs.py`, wired into the `lint` job and
      `make lint`; `make check-docs ARGS=--write` regenerates the index.
      `backend/tests/test_check_docs.py` (39 tests) pins each rule against a
      scratch `docs/` tree in `tmp_path`, plus two that guard the real thing:
      one runs the checker over this repo's actual `docs/`, and one asserts
      each `_template.md` would itself pass the rules it documents — a template
      that drifts from its own checker is worse than no template
- Four decisions the brief didn't settle. (1) The date lives as a `**Date:**`
      line *inside* `## Status`, not as its own section or a `Accepted — date`
      suffix: ADR-0003's status is a qualifying paragraph ("Accepted — plumbing
      only. **No replica is provisioned**…"), and that prose is worth more than
      a parseable one-liner, so the parser takes the first non-empty line for
      the status word and finds the date on its own line. (2) The script checks
      ADR *section presence and order* as well as the status line the brief
      named — free once the sections exist, and it is what stops the template
      from being advisory. It deliberately stops there: it cannot check whether
      the prose is true, which is why README states the "ADR ships in the same
      PR as the change" rule as the thing no script enforces. (3) The runbook
      exemption is an in-file marker with a mandatory reason (same shape as the
      existing `rtl-ok:` / `impersonation-ok:` comments) rather than a list
      inside the script, so the reason sits where the reader is. (4) The six
      runbook headings are all `##`, following `encryption-key-rotation.md`'s
      existing `## Trigger` rather than `new-module.md`'s bold `**Trigger**`
      lead — a heading is checkable and a bold line is not, so T-251's runbook
      got the one-line retrofit instead of the other nine getting the loose
      shape

---

## P3-G · Support tooling & operator features

> Everything here hardens operational quality: safer impersonation, better support-tool integration, global product controls.

### T-260 Impersonation destructive-action blocklist (server-side)

> Hardens T-142. Applies uniformly to web and mobile impersonation sessions.
> needs: T-142

**Goal** During an active impersonation session, the server refuses actions that would produce user-visible side effects the real user didn't intend. Prevents a support agent from accidentally sending an email, marking notifications read, changing password, or issuing charges _as_ the real user.

**Build**

- `backend/app/core/dependencies.py` — add `reject_during_impersonation(action_kind: Literal["destructive","notifying","billing","data_mutation"])`:
  ```python
  def reject_during_impersonation(kind: str) -> Callable:
      async def checker(request: Request):
          if request.state.principal.impersonator_id:
              raise PermissionDeniedError(
                  f"Action '{kind}' is blocked during impersonation. "
                  f"End impersonation to perform this action as yourself."
              )
      return checker
  ```
- **Blocklist categories** and where they apply:
  - `notifying` — outbound email/push sends (`/notifications/*`, `/auth/forgot-password`, `/auth/magic-link`, invitation resends). Also: silent suppression of push notifications on the impersonated user's registered devices for any action performed during impersonation
  - `billing` — checkout session creation, subscription changes, portal session, any endpoint that could produce a charge
  - `data_mutation` — password change, 2FA disable, email change, account deletion, org deletion
  - `notification_read_state` — marking notifications read (silent state pollution)
- Impersonation-token JWTs carry `impersonator_id` claim (already in T-142); the dependency checks presence of that claim
- Every blocked attempt writes an audit event with `action="impersonation.blocked"` — useful signal for support-tool improvement

**Tests**

- Impersonation session attempting `POST /auth/forgot-password` → 403; audit event written
- Impersonation session attempting `POST /billing/checkout-session` → 403
- Impersonation session attempting `PATCH /notifications/{id}/read` → 403
- Non-impersonation session performing same actions → succeeds
- Every destructive endpoint is covered by the parametrised test matrix (mirror of T-049 cross-tenant sweep)

**Done when**

- [x] Every destructive endpoint in the codebase has `Depends(reject_during_impersonation(...))` or an explicit `# impersonation-ok: <reason>` comment —
      all 52 mutating routes decided: 43 guarded, 9 marked. The marked nine are
      the login/logout legs (`/auth/login`, `/refresh`, `/logout`,
      `/magic-link/consume`, `/2fa/login`, `/webauthn/login{,/options}`), the
      two token-only public flows (`/auth/register`, `/auth/verify-email`), the
      Stripe webhook (server-to-server, no bearer token), and
      `/admin/impersonate/end` — blocking that last one would trap the agent
      inside the identity they are trying to leave.
      `test_every_mutating_route_is_decided` walks the real route table and
      fails on a write route added with neither, so this can't rot; the
      scaffold in `app/modules/_template/router.py` carries the dependency on
      its three write routes, so a module generated by `make new-module`
      (T-251) passes that sweep the moment it exists
- [x] Parametrised block-matrix test covers every category —
      `backend/tests/security/test_impersonation_blocklist.py` (61 tests). The
      matrix is *generated from the route table* rather than hand-listed, so
      adding a guard adds a case: every one of the 44 guarded routes is hit
      with a real impersonation token and must answer 403
      `errors.impersonation_blocked` carrying the category the route claims.
      Bodies are empty and path parameters are throwaway UUIDs on purpose —
      the guard is a route-level dependency, so FastAPI solves it before it
      validates anything, and a 403 rather than a 422 is itself the proof the
      block lands ahead of the permission, entitlement and step-up gates
- [x] Push notification suppression verified end-to-end —
      `NotificationService.send()` drops the push channel (only the push
      channel) whenever `app.core.audit_context`'s impersonator ContextVar is
      set, and records `push_suppressed: true` on the `notification.sent`
      audit event. Verified through a real request behind the production
      `ImpersonationContextMiddleware`: the blocklist means no reachable
      endpoint both survives an impersonation session and sends a
      notification, so the test mounts a one-route probe app on the real
      middleware + the real service, which is the only synthetic part
- Four decisions the brief didn't settle. (1) The guard reads the
      `Authorization` header itself rather than `request.state.principal`
      (which doesn't exist here) or the ContextVar the middleware already
      sets. Both alternatives fail *open* if that middleware is ever
      reordered or a route is mounted on a sub-app, and this dependency is the
      only thing between a support agent and a charge on someone else's card.
      It also deliberately avoids `Depends(oauth2_scheme)`: that would attach
      a `security` requirement to the public endpoints it guards, and every
      generated client would then believe `/auth/forgot-password` needs a
      token (pinned by
      `test_guarding_a_public_route_does_not_make_it_look_authenticated`).
      (2) The guard commits its own audit event before raising. `AuditService`
      writes in a savepoint inside the caller's transaction, and the 403
      unwinds `get_session`'s `except: rollback()` — so without the commit the
      "every blocked attempt writes an audit event" requirement was true in a
      test using an overridden session and false in production. The same
      latent bug was already in `require_permission`'s denial event (T-219);
      fixed there in the same change. (3) Categories are the four from the
      brief's blocklist section, not the four in its signature sketch —
      `destructive` never appears in either the categories list or the route
      assignments, and an unused member of a closed `Literal` is a rename
      waiting to go half-done (`test_every_category_is_actually_used` fails if
      one stops being applied). (4) No ADR: `docs/adr/README.md` explicitly
      excludes "anything already enforced and explained by a test", which is
      exactly what the sweep plus the `# impersonation-ok:` reasons are

---

### T-261 Mobile impersonation via deep-link handoff

> needs: T-142, T-260, T-047

**Goal** Superadmin reproduces mobile-specific issues on their own device without needing physical access to the user's phone.

**Build**

- Backend endpoint `POST /api/v1/admin/impersonate/mobile-handoff`:
  - Requires an active impersonation session (must have already called T-142's impersonate endpoint)
  - Issues a one-time exchange token (short random string, 2 minute TTL, Redis-stored keyed by hash) that maps to the impersonation JWT
  - Returns `{ deep_link: "forge://impersonate?token=<exchange_token>&user_email=<email>", expires_at }`
- Admin web UI adds "Handoff to my mobile" button next to existing "Impersonate" button — displays the deep link as a QR code + copyable text
- Mobile app:
  - Deep-link handler for `forge://impersonate` navigates to a `ImpersonateConfirmScreen`
  - Confirmation screen displays the user email (from the query param — informational only, real identity comes from the exchange), the reason from the original impersonation request, and duration remaining
  - "Confirm" calls `POST /api/v1/admin/impersonate/mobile-exchange` with the exchange token → receives the actual access token → stores in secure store with a `IS_IMPERSONATION_SESSION` flag
  - "Cancel" invalidates the exchange token immediately
- Mobile app during impersonation:
  - Persistent red banner across every screen: "Impersonating <email> · <mm:ss remaining> · [End]"
  - Banner is non-dismissable
  - Push notification registration for the impersonated user is _not_ updated to the superadmin's device (prevents leaking pushes)
  - App state does not persist across cold restart when in impersonation mode (secure-store flag is cleared on app launch if it exists)
  - "End impersonation" clears secure store and returns to login (not to the superadmin's previous session — mobile has one session per install)

**Tests**

- Exchange token single-use (second attempt to exchange → 401)
- Exchange token expires in 2 minutes (freezegun-advance → 401)
- Cold-restart clears impersonation state
- Banner visible on every screen
- Push notification triggered by impersonated user's normal activity fires to their real devices, not the superadmin's device
- Destructive actions blocked (verified via T-260's blocklist)
- Audit event `impersonation.mobile_handoff` records the exchange
- Audit event `impersonation.mobile_ended` records when superadmin ends

**Done when**

- [ ] Handoff works end-to-end from admin web to iOS Simulator + Android emulator
- [ ] Push-suppression test proves no leak to superadmin device
- [ ] Runbook `docs/runbooks/mobile-impersonation.md` documents when to use mobile handoff vs regular impersonation vs screen-share alternatives

---

### T-262 Support integration hook (SupportContext)

> needs: T-021 (get_current_user), T-025 (org context)

**Goal** A thin, tool-agnostic layer that surfaces the user's context to whichever support tool a product picks (Intercom, Crisp, Plain, HelpScout, or none). The template never depends on a specific tool.

**Build**

- `packages/hooks/src/useSupportContext.ts`:

  ```typescript
  export interface SupportContext {
    userId: string;
    email: string;
    fullName: string | null;
    orgId: string;
    orgName: string;
    plan: string | null;
    subscriptionStatus: string | null;
    signupAt: string;
    locale: string;
    isImpersonating: boolean; // suppress support-tool identity if true
  }

  export function useSupportContext(): SupportContext | null;
  ```
  - Returns null while auth is loading, so support widgets don't flash unauthenticated
  - Returns `isImpersonating: true` when in an impersonation session — the wrapper below should suppress identity in that case so the superadmin's actions don't pollute the real user's support history

- Product-side wrapper example (documented, not implemented in template): `apps/web/src/features/support/SupportWidget.tsx` reads `useSupportContext()` and calls e.g. `window.Intercom("boot", { user_id: ctx.userId, ... })`
- Backend endpoint `GET /api/v1/me/support-context` — same fields, returned via API for the mobile app (which cannot use JavaScript widgets; usually opens a `mailto:` with pre-populated context or launches a chat app SDK)
- `product.config.py` adds:
  ```python
  SUPPORT_TOOL: Literal["none", "intercom", "crisp", "plain", "helpscout", "custom"] = "none"
  SUPPORT_URL: str | None = None    # fallback: opens as mailto: or web URL
  ```
  Web app renders a "Contact support" link/button in the sidebar; behaviour depends on `SUPPORT_TOOL`

**Tests**

- `useSupportContext()` returns null while unauthenticated
- During impersonation: `isImpersonating: true`
- Mobile: `GET /me/support-context` returns fields correctly
- Cross-tenant safety: user in org A cannot receive org B's context

**Done when**

- [ ] Hook + endpoint working
- [ ] `product.config.py` `SUPPORT_TOOL` option honoured on web + mobile
- [ ] Docs `docs/runbooks/support-tool-integration.md` show how to wire Intercom, Crisp, Plain

---

### T-263 ProductSettings singleton — global runtime toggles

> needs: T-140 (superadmin guard), T-063 (public config endpoint)

**Goal** Global switches an operator can flip without a redeploy: maintenance mode, feature-wide kill switches, banner announcements. Distinct from `product.config.py` (deploy-time) and `OrgFeatureFlag` (per-org).

**Build**

- `backend/app/modules/product_settings/models.py`:
  ```python
  class ProductSettings(SQLModel, table=True):
      id: int = Field(default=1, primary_key=True)   # singleton — always 1
      settings: dict = Field(sa_column=Column(JSONB, default=dict))
      updated_at: datetime = Field(default_factory=utcnow)
      updated_by: UUID | None
  ```
  Migration ensures a single row with id=1 exists on first upgrade.
- `ProductSettingsService`:
  - `get(key: str) -> Any` — Redis-cached with 60s TTL, invalidated on write
  - `set(key: str, value: Any, actor: User) -> None` — superadmin only, audit-logged
  - Well-known keys (documented, not hard-coded): `maintenance_mode: bool`, `maintenance_message: str`, `signup_disabled: bool`, `global_banner: {type, message, dismissible}`, `emergency_kill_switch: dict[str, bool]` (per-module)
- Middleware `check_maintenance_mode` (registered before routers):
  - If `maintenance_mode=True`, all non-admin, non-health endpoints return 503 with `{"error": {"code": "maintenance", "message": settings.maintenance_message}}`
  - Admin endpoints and `/health/*` remain reachable so operators can end maintenance
- `GET /api/v1/config` (from T-063) extended: includes `banner: {...}` if `global_banner` set; the frontend renders it above the app shell
- Admin UI: dedicated `/_admin/settings/product` page with editable form for well-known keys + raw JSON edit for advanced use

**Tests**

- Toggle `maintenance_mode=True` → all user endpoints return 503; admin endpoints remain 200; health remains 200
- Global banner appears in `GET /config`; disappears on unset
- Cache invalidation: setting a value updates `GET /config` within 60s (or immediately with explicit invalidation)
- Non-superadmin `PATCH /admin/settings/product` → 403
- Audit event written on every settings change with diff

**Done when**

- [ ] Maintenance mode works end-to-end (test flip and verify user impact)
- [ ] Banner shows/hides via GET /config on web + mobile
- [ ] Runbook `docs/runbooks/maintenance-mode.md` documents the incident-response flow

---

# Phase 4 — Growth layer

> HTML ref: §"Build phases" → "Phase 4"
>
> **Prerequisite:** Phase 3's acceptance criterion is green. Phase 4 adds public surface area — a site crawlers index, keys third parties hold, requests leaving your network, and identity a customer's IdP asserts. Every one of those is harder to un-ship than to ship.
>
> **Acceptance:** the per-sub-phase criteria and the phase gate are at the end of this phase, under §Phase-4 verification (T-350). In summary: (1) Each of the three modules this phase adds can be switched off and the product still boots, migrates, builds, lints and type-checks — proven by a CI matrix, not by claim. (2) The marketing site meets its Lighthouse budget on landing, pricing, a blog post and a docs page. (3) An API key calls a scoped endpoint, is rate-limited independently of any user, and stops working the moment the module is disabled. (4) A webhook whose consumer was down for an hour arrives on retry, and its payload survives replay unchanged. (5) A member of an SSO-enforced org signs in through their own IdP and never sees a password field.

---

## P4-0 · New module toggles — do this first

> Everything Phase 4 adds is optional. Read `TEMPLATE.md`'s module table and `backend/app/core/modules.py` before writing a line of P4-A.

### T-290 Three new module toggles: `marketing`, `public_api`, `enterprise_sso`

> needs: T-060, T-061, T-062 (registry, conditional routers, conditional migrations), T-063 (public config endpoint), T-064 (conditional frontend routes + nav)

**Goal** A product that wants the app and nothing else sets three booleans to `false` and gets no marketing build, no API-key surface, and no SAML dependency — the same contract `billing`, `admin` and `gdpr` already honour.

**Build**

- `backend/app/core/modules.py` — three members on `ModuleName`, three fields on `EnabledModules`:

  ```python
  marketing: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=True))
  public_api: ModuleConfig = field(default_factory=lambda: ModuleConfig(enabled=False))
  enterprise_sso: ModuleConfig = field(
      default_factory=lambda: ModuleConfig(enabled=False, requires=["oauth"])
  )
  ```

  The defaults are deliberate, not cosmetic. Nearly every product wants a landing page → `marketing` on. An org-scoped API key is attack surface nobody asked for → `public_api` off. SAML drags `python3-saml` → `xmlsec` → native `libxmlsec1` into the backend image → `enterprise_sso` off. `enterprise_sso` requires `oauth` because T-331's OIDC SP reuses authlib's client registry and T-025's account-linking; `validate_modules()` fails the boot on the illegal combination, which is exactly what that function is for.

- `backend/product/config.py` — the three entries, each with a comment saying what turning it off actually removes.
- `product.config.ts` and `packages/types/src/index.ts` — `marketing`, `publicApi`, `enterpriseSso` on `ProductConfig["modules"]`, camelCase like every existing key.
- `backend/app/api/v1/config.py` — three `bool` fields on `ModulesResponse`.
- `packages/hooks/src/useModules.ts` — three keys in `DENY_BY_DEFAULT` **and** three lines in `toActiveModules()`. Forgetting the first is the dangerous half: a key absent from `DENY_BY_DEFAULT` reads `undefined` before `/config` resolves, which is falsy today and becomes a render-it-early bug the moment someone writes `!modules.publicApi`.
- `backend/app/api/v1/__init__.py` — `if modules.public_api.enabled:` and `if modules.enterprise_sso.enabled:` blocks with function-local router imports (`# noqa: PLC0415`), the same shape as billing's. `marketing` gets **no** block: it owns no routes.
- `backend/alembic/env.py` — `MODULE_TABLES["public_api"] = {"api_key"}`, `MODULE_TABLES["enterprise_sso"] = {"sso_connection"}`, plus the conditional imports in `load_models_for_migration()`.
- `backend/app/core/migrations.py` — `MODULE_BRANCHES` entries `public_api` and `enterprise_sso`. T-310's and T-331's migrations go on those branches, never on the main line. `marketing` has no branch — no tables.
- `backend/app/models/__init__.py` — unconditional imports (only alembic's view is filtered).
- `TEMPLATE.md` — three rows in the module table, and a migration-prefix block per new branch.

`marketing` is the odd one out: it gates a **build**, not a router.

- `apps/marketing/package.json`'s `build` runs `node scripts/guard-enabled.mjs` first. The guard reads `modules.marketing` from `product.config.ts` and, when false, prints `marketing module disabled — skipping build` and exits **0**. Exit 0, not 1: `turbo build` at the repo root has to stay green for a product that turned it off.
- Caddy and compose: the marketing block serves `/srv/marketing`, and with the module off that block must be absent rather than 404ing — see T-307.
- The web app reads it from `/config` like any other module. The footer's "← Back to site" link, the login page's logo href, and marketing links in transactional emails all fall back to the app root when `marketing` is false.

**Tests**

- `enterprise_sso=True` + `oauth=False` → `RuntimeError` naming both modules (a new case on the existing `validate_modules` parametrisation).
- A set-equality test between `ModulesResponse`'s field names and `ModuleName`'s members. A fourth module can then never be added to one and forgotten in the other.
- Key-parity test in `packages/hooks` between `DENY_BY_DEFAULT`, `toActiveModules()`'s return, and `ProductConfig["modules"]`. The type-level relation is compile-time only; this catches the runtime object.
- With `public_api=False`: `/api/v1/api-keys` 404s, `alembic upgrade` creates no `api_key` table, and `alembic revision --autogenerate` proposes an empty migration (the `MODULE_TABLES` guard doing its job).
- With `marketing=False`: root `pnpm build` is green and no `apps/marketing/dist` exists.

**Done when**

- [ ] All three off → `make migrate && make dev` boots; `pnpm build && pnpm lint && pnpm type-check` clean
- [ ] T-291's matrix covers the all-off combination
- [ ] `mypy --strict` clean, `TEMPLATE.md` updated

---

### T-291 Extend the module matrix to the build, not just the process

> needs: T-290, T-066 (module toggle smoke test + `docs/runbooks/module-toggle-guide.md`)

**Goal** T-066 already parametrises three configurations in-process — it monkeypatches `get_modules()` and asserts absent routes and absent tables. **Read it first and extend it; do not write a second matrix beside it.** What it cannot reach is everything outside the Python process: the frontend build, a real `alembic upgrade` from zero, and app boot as a subprocess reading an actual `product/config.py`. Phase 4 takes the toggle count from eleven to fourteen and adds the first toggle that gates a *build* rather than a router, so that gap now has teeth.

**Build**

- Extend `backend/tests/modules/test_module_toggles.py`'s `MODULE_CONFIGS` with the three new modules: `public_api` off (absent routes `/api/v1/api-keys`, absent table `api_key`), `enterprise_sso` off (absent `/auth/sso/*`, absent `sso_connection`), and an `enterprise_sso` on + `oauth` off case asserting the `validate_modules()` `RuntimeError`. Same file, same parametrisation — a reader should find every toggle assertion in one place.
- Extend `docs/runbooks/module-toggle-guide.md` with a section per new module: what turning it off actually removes, and what a product must do when re-enabling `public_api` on a live database (existing rows in `api_key` become live again — spell this out, it is the one re-enable that has a security consequence).
- `.github/workflows/ci.yml` — a new `module-matrix` job after the backend job, covering what the in-process test cannot. Three combinations:
  - **A** — the template default, `product/config.py` as committed.
  - **B** — every optional module `False`. Note that `notifications`, `file_storage`, `usage_metering` and `outbound_webhooks` all require `billing`, so "everything off" is legal while "billing off, notifications on" is not. B is the minimum-surface product, which is the configuration a template user is most likely to want and least likely to have been tested.
  - **C** — the three Phase 4 modules on, everything else at defaults.
- Each combination copies a fixture from `backend/tests/fixtures/module_configs/` over `product/config.py` **and the matching `product.config.ts`**, then runs: `alembic upgrade` from an empty database through `migration_targets()`, app boot as a subprocess, `curl /api/v1/config`, `pytest -m "not module_dependent"`, and `pnpm build && pnpm lint && pnpm type-check` at the repo root. The frontend half is the point — it is where a disabled `marketing` either builds green or breaks the whole pipeline.
- Restore both config files in an `if: always()` step. The T-251 module-generator smoke job already does exactly this dance against a dirty checkout — copy that pattern rather than inventing a second one.
- Backend tests that assume a module is on get a `module_dependent` marker, registered in `pytest.ini`, so combination B can deselect them.

**Tests** — the job is the test. One unit test guards the fixtures: every file in `module_configs/` imports, passes `validate_modules()`, and agrees key-for-key with its `product.config.ts` twin.

**Done when**

- [x] Three combinations green, backend **and** frontend —
      `module-matrix` in `.github/workflows/ci.yml`, three legs: A the
      committed `product/config.py`, B every optional module off, C the three
      Phase 4 modules on. Each was run by hand exactly as the job runs it —
      fixture pair copied over both config files, database dropped and
      recreated, `python -m app.core.migrations` from empty, uvicorn as a
      subprocess, `GET /api/v1/config` compared field-for-field with the
      config on disk. B deselects 361 tests and passes 1670 (6 skipped);
      `pnpm build && pnpm lint && pnpm type-check` green. CI has not executed
      the job itself yet: it `needs: test`, which was red on a stale
      `alembic upgrade head` assertion until this branch fixed it
- [x] T-066's in-process matrix covers all fourteen modules —
      `MODULE_SURFACE` in `backend/tests/modules/test_module_toggles.py`
      declares route and table ownership once per module and is asserted
      set-equal to `ModuleName`, so a fifteenth toggle cannot be added
      without an entry, and every scenario diffs the whole route table
      against the baseline rather than listing paths by hand. Five scenarios
      between them switch off all fourteen, which is itself a test; a module
      that legitimately removes nothing carries a `note` explaining what its
      toggle does instead, and that is enforced too
- [x] Deliberately unconditionalising a router import in `build_router()` turns combination B red —
      confirmed by patching `if modules.billing.enabled:` to `if True:` in
      `app/api/v1/__init__.py`: `test_module_config_matrix` fails on both
      `no_billing` and `minimal`, naming all seven surviving
      `/api/v1/billing/*` paths. That file is deliberately *not*
      `module_dependent`, which is what carries the check into the matrix job
- [x] Deliberately removing the `marketing` guard script turns combination B red too —
      verified 2026-08-22, once T-300 had made `apps/marketing` real. With
      `marketing` off in both configs and `apps/marketing/package.json`'s
      `build` reduced to a bare `astro build`, the build produces a `dist/`
      and the step's `enabled=false && -d dist` branch fires. Restoring the
      guard leaves no `dist` and exits 0. It reads the toggle from
      `product.config.MODULES` rather than grepping the `.ts`, so a comment
      cannot satisfy it

---

## P4-A · Marketing site + SEO

> Module: `marketing`. Every task below is skipped entirely when it is off — including the CI jobs.

### T-300 `apps/marketing` — Astro, sharing `@forge/ui`

> HTML ref: §"Build phases" → "Phase 4" (T-300)
> needs: T-038 (design tokens in `@forge/ui`), T-290

**Goal** A pre-rendered marketing app in the workspace that shares the product's design tokens and locale files, ships approximately no JavaScript, and deploys as a directory of static files.

**Build**

- **Close ADR-0004** (`docs/adr/0004-marketing-framework.md`, currently `Proposed — placeholder`). Flip Status to `Accepted`, name **Astro**, and answer the four constraints it left open. The reasoning to record:
  - Constraint 1 (`"use client"` boundary) — **this is why Astro wins.** `@forge/ui` is React 18 + Radix and ships no `"use client"` directives; every component in it is client-only by construction. Astro's islands model asks for the opposite annotation: pages are static HTML and a React component is mounted deliberately with `client:visible` / `client:idle`. So the package needs **no directives added** and no wrapper layer, and `apps/web` is untouched. Next.js's RSC-by-default would have required one or the other.
  - Constraint 2 (locale in path) — `astro:i18n` routes on a locale segment natively; `@forge/i18n`'s locale JSON stays the single source (T-305).
  - Constraint 3 (MDX) — `@astrojs/mdx` plus content collections with typed frontmatter, first-party.
  - Constraint 4 (static deploy) — `output: "static"` is a directory Caddy serves. No Node runtime, no second deployment topology, nothing new that can be down.
  - Additionally, and not in the ADR's original framing: Astro is Vite-based, so it reuses the toolchain this repo already runs — `@tailwindcss/vite` v4 (the same plugin `apps/web` and `@forge/ui`'s Storybook use), the same tsconfig idioms, the same `turbo` task graph. Next.js would put a second build system in the monorepo.
  - Record the revisit trigger: the day marketing needs per-request personalisation or ISR, Astro's `output: "server"` adapter — or a move to Next — is back on the table. That is a hosting decision, not a rewrite.
  - Cost to record honestly in Consequences: a second component model to hold in your head alongside the SPA's, and a smaller ecosystem for SEO/analytics integrations Next treats as first-party.
- `apps/marketing/` — deps `astro`, `@astrojs/react`, `@astrojs/mdx`, `@astrojs/sitemap`, `@tailwindcss/vite`, each with the justification comment §7 requires.
  - `astro.config.mjs`: `output: "static"`, `site: process.env.PUBLIC_SITE_URL`, and an `i18n` block whose locale list is **read from `product.config.ts`**, not hardcoded a second time.
  - `src/layouts/BaseLayout.astro` — `<html lang dir>` (`dir="rtl"` for `ar`, the same rule the app shell applies), `<Seo>` slot, header, footer, skip link.
  - Tailwind imports the shared token layer from `@forge/ui` so `primaryColor` in `product.config.ts` stays the one place brand colour is defined. The marketing site, the app, and `packages/email-templates` (whose React Email components already share these tokens) must not be able to drift.
  - **Tailwind v4 will not scan `node_modules` by default.** A `@forge/ui` component mounted as an island renders classes that live only in the package's `dist`, and they will silently not exist in the marketing CSS. Add an explicit `@source "../../packages/ui/dist"` — this fails as missing styles, not as an error, so it is worth a comment.
  - `pnpm-workspace.yaml` already globs `apps/*`. Add `type-check: astro check` so the root turbo task covers it.
- **Each island is its own React root.** Wrapping every one in an i18next provider means shipping i18next to a static page and initialising it N times. Translate in the `.astro` page — read `@forge/i18n`'s locale JSON at build time and pass strings down as props. Reach for a provider only for an island that genuinely switches language client-side, and justify it in review.
- **The island rule, enforced by review:** a page may only hydrate a component that cannot be expressed as markup. Pricing toggle, mobile nav, FAQ accordion — yes. Everything else renders in `.astro`. This rule is what makes T-306 pass; without it Astro degrades into a slower Next.

**Tests**

- `pnpm --filter marketing build` produces `dist/index.html` containing the rendered landing copy, not an empty shell.
- Total `<script>` bytes for `/` are zero — assert against the build output. The zero-JS claim is a test, not a hope.
- `astro check` clean.
- Guard script: `modules.marketing = false` → build exits 0 and `dist/` is absent.

**Done when**

- [x] ADR-0004 is `Accepted` and answers all four constraints —
      `docs/adr/0004-marketing-framework.md` names Astro, answers all four,
      and records both the revisit trigger (per-request personalisation or
      ISR) and the cost (a second component model, a smaller SEO/analytics
      ecosystem)
- [x] A `@forge/ui` component renders on a marketing page as an island, with
      `apps/web` and `packages/ui` unmodified — no `"use client"` directives
      added and no wrapper layer. T-300 proved it on a diagnostic page
      (`island-check.astro`) because `/` ships zero script bytes; T-301
      deleted that page and moved the assertion onto the real island, the
      pricing period toggle (`tests/pricing.test.ts`). The token layer *did*
      move into `packages/ui` — that is this task's own instruction, and a
      separate thing from the island boundary this box is about
- [x] Root `pnpm build`, `pnpm lint`, `pnpm type-check` clean with the module
      both on and off — re-verified 2026-08-22 after T-301. With it off,
      `turbo` prints `no output files found for task marketing#build`, which
      is correct: there is no site. The one lint warning is the pre-existing
      `VirtualList` / `react-hooks` one from T-241

---

### T-301 Pages: landing, pricing, features, blog (MDX), docs (MDX), legal

> needs: T-300

**Goal** The content surface, with frontmatter that is validated at build time rather than discovered wrong in production.

**Build**

- `src/content/config.ts` — content collections with zod schemas (`zod` is already a workspace dep):
  - `blog` — title, description, publishedAt, updatedAt?, author, tags[], draft, ogImage?
  - `docs` — title, description, section, order, ogImage?
  - `legal` — title, effectiveDate
  A schema violation fails the build. That is the entire reason to use collections instead of a glob.
- Routes: `/`, `/pricing`, `/features`, `/blog`, `/blog/[slug]`, `/guides`, `/guides/[...slug]`, `/privacy`, `/terms`, plus a 404. All nested under the locale segment per T-305.
  - **`/guides`, not `/docs`.** The Caddyfile routes `/docs*` to the backend's Swagger UI. T-307 splits marketing onto its own host, which technically frees the path — but a template whose marketing docs and API docs differ only by hostname is a trap for the next person. Take the unambiguous name.
- **Pricing data.** Plans live in the `billing` module's database; a static site cannot query at request time. Fetch `/api/v1/plans` at build time into the page, with a committed `src/content/pricing.ts` as the fallback when the API is unreachable during the build. Document the staleness contract in the file: prices change on the next deploy, not on the next `UPDATE`. When `billing` is off, `/pricing` is not built, not linked in the nav, and not in the sitemap.
- Drafts (`draft: true`) render in dev, are excluded from production builds and from the sitemap.
- Prose styling: `@tailwindcss/typography` is acceptable here with a justification comment — the alternative is hand-rolling a type scale that then drifts from `@forge/ui`'s.

**Tests**

- A post missing `publishedAt` fails the build with a message naming the file.
- A `draft: true` post is absent from a production build and present in dev.
- `/guides/<slug>` renders MDX including a fenced code block.
- Pricing falls back to committed data when the build-time fetch fails, and the page still builds.
- With `billing` off: no `/pricing` in `dist`, no link to it in the rendered nav, no entry in `sitemap.xml`.

**Done when**

- [x] Every route builds in every enabled locale — 50 pages: seven routes
      (`/`, `features`, `pricing`, `blog`, `guides`, `privacy`, `terms`)
      across six locales, plus the content routes and a single `404.html`.
      `tests/routes.test.ts` reads every one of them out of `dist/`, checks
      every internal link on every landing page resolves to a page that was
      actually built *and* stays inside that page's locale, and holds the
      locale list `astro.config.mjs` derives in plain Node to the one
      `src/lib/product.ts` derives in TypeScript
- [x] No `/docs` path collision with the backend's Swagger UI — the route is
      `/guides` and so is the collection, so the two cannot drift apart
      later. The `legal` collection is the one deliberate exception to
      T-305's no-fallback rule, documented in `legalDocument()`
- [x] Frontmatter is validated at build time, not discovered wrong in
      production — a post with no `publishedAt` fails the build naming the
      file, with a message that says what to write rather than
      `expected date, received object`
- [x] `/pricing` falls back to committed data when the build-time fetch
      fails, and disappears entirely when `billing` is off — page, nav link
      and sitemap entry together, since those are the three places a
      half-applied toggle survives in

---

### T-302 `<Seo>` — typed meta, Open Graph, Twitter card

> needs: T-301

**Goal** One import per page, and a build that fails rather than shipping a page with no description.

**Build**

- `src/components/Seo.astro` with a typed props interface: `title`, `description`, `canonical?`, `image?`, `type?`, `noindex?`, `publishedAt?`. Emits `<title>`, meta description, canonical, `og:{title,description,url,image,type,locale,alternate_locale}`, and `twitter:card=summary_large_image`.
- Defaults live in one `src/config/site.ts` that reads `product.config.ts` — app name, brand colour, support email. No page repeats the brand.
- **OG images:** a committed default plus a per-entry `ogImage` override in the collection schema. Runtime/build-time image generation (satori, headless Chrome) is an explicit non-goal — a rendering dependency and a CI browser for a marginal gain over a well-made default.
- `scripts/check-seo.mjs`, run as part of the marketing build: walks `dist/**/*.html` and fails on any page missing a title, a description, or a canonical, or carrying more than one of any of them. This is what makes "one import per page" a guarantee instead of a convention.

**Tests**

- Each built route has exactly one `<title>`, one canonical, and a non-empty `og:image`.
- A `noindex` page emits the robots meta **and** is absent from `sitemap.xml`.
- `check-seo.mjs` fails against a fixture page with an empty description.

**Done when**

- [x] Every built page passes `check-seo.mjs` — it runs as an Astro
      integration on `astro:build:done`, so *every* build runs it, including
      the ones the tests spawn through `scripts/guard-enabled.mjs`. All 50
      pages pass; `tests/seo.test.ts` also runs the checker over `dist/` from
      outside the build, so unwiring the integration is still caught, and over
      committed fixtures for the three ways a page fails it
- [ ] A social preview debugger renders the landing page card correctly (verify once by hand)

---

### T-303 JSON-LD structured data

> needs: T-301

**Goal** Rich results that reflect what the product actually is, generated from the same data the pages render.

**Build**

- `src/components/JsonLd.astro` — typed props, serialised into `<script type="application/ld+json">`. Two traps to handle in the component, once, so no caller has to think about them:
  - Escape `<` as `\u003c` in the serialised JSON. A payload containing `</script>` otherwise terminates the block and injects markup.
  - **CSP.** A `<script type="application/ld+json">` block is still a script element and is subject to `script-src`. The app's strict CSP would block it, and crawlers read the DOM. T-307 gives marketing its own, simpler CSP header; confirm in a browser console that no CSP violation is reported for the block, then re-run a rich-results test.
- Types: `Organization` (site-wide, in `BaseLayout`), `SoftwareApplication` (landing), `Product` + `Offer` per plan (pricing, from T-301's plan data — never a second hand-maintained copy of the prices), `FAQPage` (landing and pricing), `BlogPosting` (blog posts), `BreadcrumbList` (guides). The last two are beyond the original table and are where most of the actual search value is.
- `schema-dts` for compile-time typing — types-only, zero runtime bytes.

**Tests**

- Each page type's JSON-LD parses and carries its required fields.
- A payload containing `</script>` round-trips without breaking the document.
- Pricing JSON-LD offers match the rendered prices exactly — one assertion over both.

**Done when**

- [ ] Google's Rich Results Test reports no errors for landing, pricing, and a
      blog post — **not verifiable from here**: the hosted tool needs a public
      URL, and this site has no host until T-307. What *is* done is the part of
      it that can be a build gate: `tests/structured-data.test.ts` carries a
      table of the properties Google documents as *required* per type and walks
      every node on every built page against it, naming the page and the
      property when one is missing (mutation-checked). That is why the landing
      page's `SoftwareApplication` carries an `AggregateOffer` — the Software
      App result requires one of `offers`, `aggregateRating` or `review`, and
      this template has no honest ratings to give. Run the hosted tool once
      after T-307 and tick this
- [x] No CSP violation on any page — verified in a browser against the real
      edge once T-307 gave marketing a host and a header. Chromium, through
      Caddy, over six pages (landing, pricing with its island, a post, a guide,
      the `ar` RTL page, the 404): zero `securitypolicyviolation` events, the
      JSON-LD blocks present in the DOM, and the pricing island hydrated and
      interactive. The premise in the plan above was wrong and this confirms
      it: a `<script>` whose `type` is not a JavaScript MIME type is a *data
      block* in the HTML spec — never prepared, never executed — so
      `script-src` does not gate these blocks and they need no nonce or hash.
      Which is load-bearing, because 51 pages carry a unique payload each and
      hashing them would mean a policy that changes whenever anybody edits a
      headline. **Mutation-checked from the other side**: stripping the three
      hashes out of the served `script-src` makes Chromium block the island's
      scripts and name the exact digests the generator computed, so the policy
      is demonstrably enforced rather than merely present

---

### T-304 `sitemap.xml` + `robots.txt`

> needs: T-301

**Build**

- `@astrojs/sitemap`, with a filter that drops `noindex` pages, drafts, and any route belonging to a disabled module. Alternates per locale come from T-305.
- `src/pages/robots.txt.ts` as an endpoint rather than a static file, so it can read the module and locale config: allow the marketing paths, `Disallow: /api`, `/_admin`, and the app host's paths. Reference the sitemap URL absolutely.
- **Staging must not be indexed.** When `PUBLIC_SITE_URL` is not the production host, emit `X-Robots-Tag: noindex` (T-307 sets the header in Caddy) *and* a `Disallow: /` robots.txt. A staging marketing site that outranks production is a genuinely expensive mistake to undo.

**Tests**

- `sitemap.xml` contains every indexable page and no draft, no `noindex` page, no disabled-module page.
- `robots.txt` on a non-production `PUBLIC_SITE_URL` disallows everything.
- Every sitemap URL returns 200 from the built output.

**Done when**

- [x] Sitemap validates and every URL in it resolves — as a build gate, not as
      a thing somebody checked once. `scripts/check-indexing.mjs` runs on
      `astro:build:done` after `@astrojs/sitemap` and fails the build on a
      `<loc>` that resolves to no built page, one on an origin other than the
      build's own, a missing sitemap namespace, or a `Sitemap:` line naming a
      file that was not written. It also checks the converse a filter can never
      catch — a page that was built, wants to be indexed and is in no sitemap.
      `tests/indexing.test.ts` runs it over `dist/` from outside the build, so
      unwiring the integration is still caught, and over a clean fixture
      directory with one thing broken in it at a time (mutation-checked). The
      `Sitemap:` line points at `/sitemap-index.xml`: `@astrojs/sitemap` always
      writes an index plus numbered chunks and never a `sitemap.xml`
- [x] Staging serves `noindex` from both the header and robots.txt — the
      robots.txt half was done here and T-307 added the header, in the site
      block its build generates. Locally the header is unconditional (no local
      stack is production); in production it is a matcher on the *host in the
      request* rather than on the build — `@not_production not host <the
      PRODUCTION_ORIGIN host>` — which also covers the case a build-time
      decision gets wrong: a production build deployed to staging. Indexing is
      opt-in: a build is
      offered to crawlers only when `PUBLIC_SITE_URL` matches the
      `PRODUCTION_ORIGIN` declared in `src/config/indexing.ts`, and every other
      build serves `Disallow: /`, offers no sitemap, and says so in the build
      log. Note also that a page-level `noindex` meta was
      *deliberately* not added on staging builds: it is redundant behind
      `Disallow: /` (a crawler forbidden to fetch the page never reads its
      meta), and it would make T-306's Lighthouse SEO assertion fail on every
      local run

---

### T-305 `hreflang` per locale, locale in path

> needs: T-304

**Goal** Locale handling identical to the app's, so a user crossing from marketing to the product never watches the locale disappear and come back.

**Build**

- Match `apps/web`'s T-233 prefix policy **exactly** — read it before configuring `prefixDefaultLocale`. A user in `fi` clicking "Sign in" must land in `fi`.
- Every page emits `<link rel="alternate" hreflang="…">` for each **enabled** locale plus `x-default`. A locale disabled in `product.config.ts` gets no page and no alternate.
- Translated content lives at `src/content/blog/<locale>/<slug>.mdx`. **An untranslated post is not published in that locale** — it does not fall back to English under `/fi/`. Publishing English content at a Finnish URL with a `hreflang="fi"` annotation is precisely what hreflang exists to prevent; a missing page is the correct answer.
- UI strings come from `@forge/i18n` (the same locale JSON as the app), not from a marketing-only string file.
- RTL: `ar` pages set `dir="rtl"` and are asserted in the built HTML. **Do not add an `ar` visual-regression test here** — the existing `ar` RTL visual test is known-unpassable (see T-262's notes); adding a second instance of a broken check helps nobody. Assert the attribute and the logical-property CSS instead.

**Tests**

- Alternates on a page are exactly the enabled locales plus `x-default`, and each target returns 200.
- Disabling a locale in `product.config.ts` removes its pages and its alternates from the whole build.
- An untranslated post yields no page in that locale and no `hreflang` pointing at one.
- `ar` pages carry `dir="rtl"`.

**Done when**

- [x] Locale prefix policy is byte-identical to T-233's — every URL carries a
      locale segment, the default locale's included (`/en/pricing`, not
      `/pricing`), and a locale-less URL resolves one and redirects.
      `DEFAULT_LOCALE_IS_PREFIXED` in `src/lib/routing.ts` and
      `prefixDefaultLocale` in `astro.config.mjs` are the two halves of the one
      decision, and `tests/routes.test.ts` fails if only one of them moves.
      `appLink(path, locale)` takes the locale as a required argument, so the
      "Sign in" case the task names cannot regress silently.
      **One half of T-233 cannot be copied and is not:** the app resolves a
      locale-less URL from three inputs — the signed-in user's stored
      preference, an in-session choice, the browser's languages — and a static,
      session-less origin that (T-307) is not even the same origin as the app
      has only the last of those. `src/components/LocaleRedirect.astro` applies
      the same order minus the tiers that do not exist, on `/` and on the 404,
      which is the one file a static host can fall back to for `/pricing`. It
      is a 200 plus a client-side rewrite rather than a 301, exactly as T-233
      leaves the same note for an SSR layer. **T-307's Caddy block now issues
      the redirect at the edge** — a 302 with `Vary: Accept-Language`, not the
      301 this note asked for, because the target depends on a request header
      and a 301 is cached per-URL by the browser. The component stays as the
      fallback any other static host would use
- [x] No `hreflang` anywhere points at a URL that 404s or serves another
      language — as a build gate rather than a review item.
      `scripts/check-hreflang.mjs` runs on `astro:build:done` and fails on an
      annotation whose target was never built, is on another origin, or is
      written in a different language; on a cluster that is not confirmed both
      ways (which a crawler discards whole); on a page that annotates every
      locale but its own; on an annotation for a locale `product.config.ts`
      does not enable; and on `lang`/`dir` disagreeing with `@forge/i18n`'s
      registry or with the locale in the URL — which is where `ar` is held to
      `dir="rtl"` on every page rather than by the visual test this task rules
      out. `tests/hreflang.test.ts` runs it over `dist/` from outside the
      build, so unwiring the integration is still caught, and over a fixture
      directory with one thing broken at a time (mutation-checked both ways:
      dropping the annotations and announcing every locale regardless of
      translation each fail the build).
      Three kinds of page carry no annotation, and all three are `noindex`: the
      404, the locale-resolving `/`, and a legal document in a locale it has
      not been translated into. That last one is the exception T-301 documented
      paid for — the collection falls back rather than 404ing because a policy
      is linked from every footer, and the copy is therefore kept out of the
      index and out of the cluster instead of being annotated as a translation
      it is not. None of the three combines `noindex` with a canonical pointing
      elsewhere: Google applies the directive to whatever a page canonicalises
      to, which is how a site deindexes its own home page

---

### T-306 Lighthouse CI in the pipeline

> needs: T-304

**Goal** The performance budget is enforced by the build, not by a screenshot someone took once.

**Build**

- `@lhci/cli` against the built static output served by `http-server`. No Docker, no backend, no database — the site is static, so this job is fast and independent.
- Assertions, mobile preset, 3 runs, median: LCP < 2.5 s, CLS < 0.1, performance ≥ 0.9, SEO ≥ 0.95, accessibility ≥ 1.0 (hold marketing to the same bar T-221 holds the app to), best-practices ≥ 0.9.
- **On INP:** the plan's "INP < 200 ms" cannot be asserted here. INP is a field metric measured from real interactions; Lighthouse lab runs report Total Blocking Time instead. Assert **TBT < 200 ms** as the lab proxy and write the reason in the config next to it, so nobody later "fixes" the discrepancy by weakening something. Real INP needs CrUX or RUM data, which is a post-launch measurement, not a CI gate.
- Pages: `/`, `/pricing`, a real blog post, a real guides page.
- The usual budget killers, handled up front: self-hosted fonts with `font-display: swap` and a preload for the above-the-fold face; Astro `<Image>` with explicit width and height everywhere (CLS 0); no third-party script without an ADR.
- The job is skipped when `modules.marketing === false`.

**Tests** — the job is the test. Add one regression fixture: a page that hydrates an unnecessary island fails the budget, demonstrating that the gate has teeth.

**Done when**

- [x] Four pages pass every assertion in CI
- [x] The INP-vs-TBT substitution is documented where the thresholds live

---

### T-307 Serving marketing: Caddy, compose, headers

> needs: T-300, T-304
> Beyond the original table, and non-optional: without it the site exists only in `dist/`.

**Goal** Marketing and the app coexist without either becoming a second-class citizen, and without reopening the cross-origin session bug T-220 already fixed once.

**Build**

- **Separate hosts, not a path split.** Marketing at the apex (`example.com`), the app at `app.example.com`. A path split would mean either moving every `apps/web` route under `/app/*` — breaking TanStack Router's basepath, every Playwright spec, every invite link and every transactional email URL — or maintaining a brittle allowlist of marketing path prefixes in Caddy. Neither is worth it.
- **Locally and in CI, `localhost` stays the app.** Marketing gets `marketing.localhost`, with its own `tls internal` leaf. Every existing E2E spec, ZAP config, and load-test target keeps working untouched, which is the point of choosing this split over the alternative.
- **Cookies are the risk.** T-220 lost a session to a cross-origin redirect once already. Marketing is fully static and needs no session at all, so the session cookie must stay scoped to the app host — do **not** widen it to `.example.com` to make a "logged in" state visible on marketing. If marketing ever needs to know, that is a public, cacheable endpoint, not a cookie. Write this in the Caddyfile comment where the next person will look.
- `Caddyfile` / `Caddyfile.prod` / `docker-compose.yml` — a marketing site block serving `/srv/marketing` from a read-only bind mount of `apps/marketing/dist`, alongside the existing `/srv/web` mount. Hashed assets get `Cache-Control: public, max-age=31536000, immutable`; HTML gets a short max-age.
- Marketing gets its **own** security headers, not the app's: a simpler CSP that permits the JSON-LD blocks (T-303) with no inline application scripts, plus HSTS and the standard set.
- `X-Robots-Tag: noindex` on non-production hosts (T-304).
- **Module off:** the marketing block is absent from the generated config and `dist/` is empty. Assert that the app host is unaffected — the failure to avoid is a disabled marketing module 404ing the product.

**Tests**

- Both hosts serve their own app over TLS in the local stack.
- A marketing asset carries the immutable cache header; marketing HTML does not.
- The app's session cookie is not sent to the marketing host.
- With `marketing=False`, the stack boots, the app host is fully functional, and the marketing host does not resolve.

**Done when**

- [x] Existing Playwright, ZAP and k6 targets pass unchanged — by construction
      and then by assertion. The app keeps `localhost` and the marketing site
      gets `marketing.localhost` with its own `tls internal` leaf, so no
      existing target's URL moves; the app's site block is not edited at all.
      What is added to the repo-root `Caddyfile` is one line,
      `import /etc/caddy/conf.d/*.caddy`, and the marketing block itself is
      **generated** into `apps/marketing/caddy/{dev,prod}/` by that app's build
      (`scripts/caddy-site.mjs`) rather than committed — because its
      `Content-Security-Policy` names each inline script by SHA-256 digest and
      its locale redirects follow `product.config.ts`, so only a copy produced
      from the `dist/` being served can be right. A glob import that matches
      nothing is a warning in Caddy's log rather than a fatal config error,
      which is what makes "the module is off" and "this stack never built the
      frontend" (the ZAP baseline job, a fresh clone) the same, safe
      configuration. `e2e-web` proves it in the stack Playwright then runs
      against: both hosts over TLS, the CSP with hashes and no
      `'unsafe-inline'`, `immutable` on a content-addressed asset and five
      minutes on the HTML, `X-Robots-Tag` on this non-production build, no
      `Set-Cookie` from marketing at all, and a locale-less `/pricing/`
      answered with a 302 to `/de/pricing/` for a German visitor
- [x] `docs/runbooks/deploy-render.md` documents the second host and its DNS —
      a "The second host" section: the two records, the two certificates, why
      `MARKETING_DOMAIN` must never be blank (Caddy substitutes a set-but-empty
      env var as the empty string rather than using the `{$VAR:default}`, which
      leaves a site block with no address and stops the **whole edge**, app
      host included — measured against `caddy:2-alpine`, which is why
      `backend/.env.example` ships a `.localhost` name rather than an empty
      value), the build-before-boot ordering that keeps `dist/` and the block
      in step, the two settings that decide whether the site is indexable, and
      a `curl --resolve` recipe for checking a deploy before DNS reaches you

**Three decisions worth knowing, all in `scripts/caddy-site.mjs`'s comments**

- **`style-src` allows `'unsafe-inline'` and `script-src` does not.** Shiki
  emits a `style="color:#…"` attribute per token in every fenced code block
  (T-301's guides), and a CSP hash cannot cover a style *attribute* —
  `'unsafe-hashes'` needs one hash per distinct value, of which there are
  hundreds, and `style-src-attr` would scope the exemption correctly but
  Firefox does not implement it and falls back to `style-src`, so that spelling
  leaves every code block unstyled for a large minority of visitors. Inline CSS
  on a site with no user input and no session is the cheapest thing on the page
  to give up; inline *script* is not, so it is bought by hash.
- **The locale-less redirect is a 302, not the 301 T-305's note asked for.**
  The target depends on `Accept-Language`, and a 301 is cached per-URL by the
  browser — it would pin the first visitor's language onto everyone who used
  that machine afterwards. `Vary: Accept-Language` rides along. Two passes
  approximate `navigator.languages`: the visitor's first tag, then any tag
  further down their list, then the product's default. Neither reads `q=`
  values (Caddy's matchers cannot, and browsers emit tags in preference order
  anyway), and where the approximation is wrong the visitor lands on a language
  they did list.
- **`caddy/*/*.caddy` is a turbo output** (`apps/marketing/turbo.json`).
  Without that, a cache *hit* restores `dist/` and leaves the site blocks
  describing the build before it — a CSP whose hashes match nothing being
  served, which is a site with no working JavaScript and no error anybody sees.
  Same shape as the stale-generated-client bug next door.

---

### T-308 Analytics and cookie consent

> needs: T-307
> Beyond the original table, and the gap most likely to be discovered by a lawyer rather than a test: a marketing site is where tracking lands, and this repo already has a `gdpr` module with a `consent_record` table.

**Goal** Make the privacy-preserving option the default and the tracking option a deliberate, consented act — rather than leaving a `<script>` tag as an exercise for the reader.

**Build**

- **Default: no analytics wired, and therefore no cookie banner.** Ship the integration point, not a vendor. A template that defaults to cookieless (or to nothing) avoids the banner entirely, and a banner avoided is worth more than a banner implemented well — it is the CLS, the consent record, and the legal review that do not happen.
- `src/config/analytics.ts` with a documented shape and a `none` default. `docs/runbooks/` gains a short section covering the two paths a product takes: a cookieless, no-consent-required provider, or a cookie-setting one.
- **If a product picks the cookie-setting path**, the template must make the correct behaviour the easy one:
  - No tracking script loads before consent. Not "loads and waits" — does not load.
  - The banner is a static, pre-rendered element with reserved height. A banner injected by JavaScript after paint is a guaranteed CLS regression and will fail T-306.
  - Consent is written to the `gdpr` module's `consent_record` for signed-in users, and to a first-party cookie for anonymous visitors; the two reconcile at login.
  - Withdrawal is as easy as granting, from a permanent footer link.
- When `gdpr` is off, the banner and the consent endpoint are both absent — this task is gated on that module, not on `marketing` alone.
- Any third-party script needs the ADR §7 demands, and its own CSP entry (T-307). "Marketing asked for it" is not a justification.

**Tests**

- Default config: no cookies set on first visit, no third-party requests in the built output.
- Cookie path: no tracking request fires before consent; consent then loads it; withdrawal stops it and clears the cookies.
- Consent state survives login and lands in `consent_record`.
- Banner present in the pre-rendered HTML; T-306's CLS assertion still passes with it displayed.

**Done when**

- [ ] Default build sets zero cookies and makes zero third-party requests
- [ ] The consent path is documented well enough that a product owner can pick a provider without reading the code

---

## P4-B · Public API keys

> Module: `public_api` (default **off**). Scaffold with `make new-module NAME=api_keys`.

### T-310 `ApiKey` model — org-scoped, hashed, scoped, expiring

> needs: T-010, T-290

**Build**

- `backend/app/modules/api_keys/models.py`:
  - `org_id` (FK + index in the same migration, per §7), `name`, `prefix`, `key_hash`, `scopes: list[str]` (JSONB), `created_by`, `last_used_at`, `expires_at`, `revoked_at`.
  - The presented key is `fk_live_<prefix>_<secret>`: `prefix` is stored in clear, indexed and unique, and is what the lookup queries; `secret` is 32 bytes from `secrets.token_urlsafe` and only its digest is stored.
- **SHA-256, not argon2 — and the comment must say why.** This is a 256-bit random secret, not a human-chosen password: there is no dictionary to slow down, and the entropy already puts brute force out of reach. Argon2 here would put ~100 ms of CPU on the hot path of every API request, which is the exact failure T-223's load test caught blocking the event loop. Verify the hash with `hmac.compare_digest`.
- The full key is returned **once**, from the create endpoint. Never stored, never logged, never in an error message (§7).
- `key_hash` ends in `_hash`, so the `EncryptedString` Semgrep rule does not apply — but declare `RedactedReprMixin` with `__redacted_fields__` anyway. A model that appears in a traceback should not print a credential digest.
- Migration on the `public_api` alembic branch; `api_key` in `MODULE_TABLES`.
- The prefix→key lookup is cached; its key belongs in `app/core/cache_keys.py` (T-240 — a test fails the build on an inline one), and revoking a key busts it immediately. Add the create/revoke paths to T-240's invalidation matrix; a new write path that is not in that matrix is the cache bug this repo has already learned to prevent.
- **Lifecycle beyond the happy path**, and the same three questions apply to T-320's endpoints and T-331's connections:
  - Deleting an org cascades to its keys. A live credential outliving its tenant is the worst possible orphan.
  - GDPR export (T-037) and erasure (T-036) must account for `created_by`: a user's export lists the keys they minted, and erasing that user nulls the column rather than deleting an org's still-active key.
  - Deactivating a member does **not** revoke their keys automatically — but the offboarding runbook has to say so, because the natural assumption is the opposite.

**Tests**

- Create returns the full key exactly once; re-fetching never exposes it.
- Digest comparison rejects a key differing in one character.
- Expired, revoked, and other-org keys all fail lookup.
- No log line in the create/auth path contains the secret.
- Revocation invalidates the cache within the same request.

**Done when**

- [ ] Migration up and down clean on the `public_api` branch
- [ ] `mypy --strict` clean

---

### T-311 API key auth — a second kind of principal

> needs: T-310

**Goal** Third parties authenticate without a user session, and *nothing* is reachable with a key unless someone deliberately opened it.

**Build**

- `Authorization: Bearer fk_live_…` — the same header as JWT, dispatched on the key prefix. This is what every API consumer expects, and the JWT path is untouched for any token that does not start with the prefix.
- Introduce `CurrentPrincipal` (`actor_type: "user" | "api_key"`, `org_id`, `scopes`, `actor_id`). `get_current_user` stays exactly as it is for user-only endpoints — the two are not merged.
- **Opt-in per endpoint, always.** A route accepts a key only by declaring `require_scope("orgs:read")`. The default for every existing and future endpoint is "keys cannot call this". Never a blanket dependency swap.
- Interactions to honour, each of which already has a CI check behind it:
  - T-260's impersonation blocklist: a key must never reach an impersonation endpoint, and the sweep demanding a guard or an `# impersonation-ok:` comment applies to everything new here.
  - T-243's query budgets: every endpoint opened to keys needs its `BUDGETS` entry or the build fails.
  - Audit: every key-authenticated write logs `actor_type=api_key` and the key id, never the key.
- `last_used_at` is throttled through Redis to at most one write per key per minute. A write per request on a hot key is an N+1 wearing a different hat.
- **Disabled module:** the router is never registered, and a key minted while the module was on must not authenticate after it is turned off. The auth dependency checks `is_enabled("public_api")`, because a stale row in a table that still physically exists is exactly the situation `MODULE_TABLES` exists to tolerate.

**Tests**

- Valid key + correct scope → 200; wrong scope → 403; unknown key → 401.
- A JWT still authenticates on the same header, unchanged.
- An endpoint without `require_scope` rejects a valid key.
- An impersonation endpoint rejects a key regardless of scope.
- Module disabled → a previously valid key gets 401, and the routes 404.
- `last_used_at` writes once across ten rapid requests.

**Done when**

- [ ] Every key-callable endpoint is enumerable from the code and listed in the docs
- [ ] The T-260 sweep, T-243 budgets, T-049 cross-tenant sweep and T-050 Schemathesis run all cover the new endpoints
- [ ] `@forge/api-client` regenerated and rebuilt, so the new endpoints exist as typed symbols before T-313 consumes them

---

### T-312 Per-key rate limiting

> needs: T-311

**Build**

- Reuse T-223's limiter, keyed on `api_key:{id}` — not on IP (proxies collapse to one) and not on user (a key has none).
- Default budget in settings (`API_KEY_RATE_LIMIT_PER_MINUTE`), overridable per key by a nullable column, so one integration can be raised without raising everyone.
- **Read the budget through `get_settings()` at request time.** Do not bind it at import. This is the repo's recurring trap: middleware and limiters constructed at `app.main` import time read `backend/.env` before conftest applies `.env.test`, which bit T-223's rate limiter and `test_middleware.py` both.
- 429 carries `Retry-After` and `X-RateLimit-{Limit,Remaining,Reset}`, and the OpenAPI description documents them — an undocumented rate limit is a support ticket.

**Tests**

- Budget+1 requests on one key → 429 with correct headers; a second key in the same org is unaffected.
- A user session hitting the same endpoint is limited by the user limiter, not this one.
- Per-key override takes effect without a restart.
- A test overriding the limit via `.env.test` actually changes behaviour (the import-time-binding regression test).

**Done when**

- [ ] Limits are per key, verified under concurrency
- [ ] Headers documented in the OpenAPI schema

---

### T-313 API key management UI

> needs: T-311

**Build**

- `apps/web/src/features/settings/ApiKeysPage.tsx`, gated on `useModules().publicApi` **and** the org-manage permission. Nav entry and route both disappear when the module is off.
- Create dialog: name, scope checkboxes, optional expiry. On success, show the key once with a copy button and an unmissable "this will not be shown again". Hold it in component state — never in the react-query cache, which persists and would leak it into a later render.
- List: name, prefix, scopes, created, last used, expires, revoke (with a typed-confirmation dialog). Cap keys per org (50) so the list needs no virtualization — say so in a comment, since T-241 otherwise implies every list gets `VirtualList`.
- Form shape follows the admin form T-263 built; the two React lint rules that dictated its structure apply here identically.
- Every string through `@forge/i18n`, all enabled locales, and the `BUDGETS` entry for the list endpoint.

**Tests**

- Module off → no route, no nav entry (extend the existing modules-disabled layout test).
- Secret renders once and is gone after close and re-open.
- Revoke removes the row and the key stops working (integration).
- Non-admin member sees no page.
- axe clean (T-221); visual baseline added (T-224). Baselines are Linux-rendered and this repo is developed on Windows — use the Docker recipe in T-262's notes rather than regenerating them locally and committing Windows renders.

**Done when**

- [ ] `pnpm lint && pnpm type-check && pnpm test` clean
- [ ] Query budget green, route bundle budget not exceeded

---

### T-314 The public surface itself — scopes and published docs

> needs: T-311, T-313
> Beyond the original table: a "public API" with no defined surface is half a feature.

**Build**

- A scope vocabulary in one file — `orgs:read`, `members:read`, `members:write`, `files:read`, … — with a test that every scope referenced in a `require_scope` exists in it.
- Tag key-callable endpoints and emit a **separate public OpenAPI document** (`/api/v1/openapi-public.json`) containing only them. Internal endpoints are not a public contract and must not leak into one.
- A `/guides/api` page on the marketing site rendering that document, plus an authentication section covering key creation, the `Authorization` header, scopes, rate-limit headers, and error shapes. Skipped when `marketing` is off; the document is still served.
- Versioning and deprecation policy in the docs: what a breaking change is, and the notice period.

**Tests**

- Public OpenAPI contains exactly the tagged endpoints, no more.
- A scope used but not declared fails the build.
- The rendered docs page builds from the live document.

**Done when**

- [ ] A newcomer can create a key and make a successful call using only the published page

---

## P4-C · Outbound webhooks

> Module: `outbound_webhooks` (already in `product.config.py`, default off, requires `billing`).

### T-320 `WebhookEndpoint` + `WebhookDelivery`, HMAC signing

> needs: T-010

**Build**

- `WebhookEndpoint`: `org_id` (FK + index), `url`, `secret`, `event_types: list[str]`, `enabled`, `description`, `consecutive_failures`.
  - `secret` ends in `_secret`, so §7's Semgrep rule requires **`EncryptedString`**, plus `RedactedReprMixin` and `__redacted_fields__`. Not optional and mechanically checked.
  - `url` is validated through `validate_outbound_url()` from T-212's `url_guard` — Phase 3's A10 mapping built it specifically for this task and left a comment saying so.
- `WebhookDelivery`: endpoint id, event type, payload (JSONB), attempt count, status, response code, truncated response body, `next_attempt_at`, `delivered_at`, `delivery_id`.
- Signature: `X-Forge-Signature: t=<unix>,v1=<hex hmac-sha256 of "{t}.{raw_body}">`, alongside `X-Forge-Event` and `X-Forge-Delivery`. Sign the raw bytes, not a re-serialised dict — a consumer that re-serialises will never match, and neither will you. Document a 5-minute timestamp tolerance so consumers can reject replays.
- `app/modules/webhooks/events.py` — a typed registry mapping event name to payload schema. Emitting an unregistered event fails a test; the registry is also what the docs render from.
- **SSRF is a delivery-time problem, not a save-time one.** Validate the URL when it is saved *and* re-resolve at delivery, since a hostname that passed yesterday can point at `169.254.169.254` today. T-209's guard already does the resolved-IP check — call it from the job, not only from the form.

**Tests**

- Signature verifies against a known vector; a mutated body fails.
- A private-range, loopback, metadata-IP or non-http(s) URL is rejected at save and at delivery.
- A hostname re-resolving to a private IP between save and delivery is blocked.
- `secret` is absent from `repr()`, logs, and API responses.
- An unregistered event type raises.

**Done when**

- [ ] Semgrep, migration up/down, and `mypy --strict` clean

---

### T-321 Delivery job with retry, backoff, and a dead letter

> needs: T-320, T-034

**Build**

- ARQ job per delivery. Retries at 1 m, 5 m, 30 m, 2 h, 12 h, then dead-lettered.
- 10 s connect and read timeouts. One slow consumer must never occupy a worker; this is the difference between a degraded integration and a degraded product.
- Auto-disable an endpoint after N consecutive failures and notify org owners — guarded, because `notifications` may be off. Never assume a sibling module.
- Idempotency: `delivery_id` in the header so consumers can dedupe; retries reuse it, replays (T-322) get a new one.
- Per-endpoint circuit breaker so a dead consumer stops consuming queue capacity.
- **Metrics:** queue depth and failure rate are worker-side, and a worker-side Prometheus metric in this repo goes through Redis rather than a process-local registry — follow `forge_active_sessions` (T-245's notes explain why the in-process gauge is wrong here).
- Payload is captured at emit time and stored. A replay must send what happened then, not what the row says now.
- **Retention.** `webhook_delivery` is the fastest-growing table this template will ever add — one row per event per endpoint, with a stored payload. Add a scheduled prune job (APScheduler, alongside the T-036 purge job) with a `WEBHOOK_DELIVERY_RETENTION_DAYS` setting defaulting to 30, deleting in batches so it never takes a long lock. A delivery log with no retention policy is a disk-full incident with a date on it.

**Tests**

- Consumer returning 500 → retried on schedule (with a clock fixture, not sleeps) → dead-lettered after the last attempt.
- Consumer timing out does not exceed the timeout budget.
- N consecutive failures disable the endpoint; a success resets the counter.
- Retries carry the same `delivery_id`.
- Queue-depth metric is visible from the API process, not only inside the worker.

**Done when**

- [ ] A consumer down for an hour receives the event on schedule once it returns
- [ ] Grafana shows delivery rate, failure rate, and queue depth

---

### T-322 Webhook UI: endpoints, delivery log, replay

> needs: T-320

**Build**

- Endpoint CRUD gated on `useModules().outboundWebhooks` and the org-manage permission. Secret shown once on create, with rotate (old secret valid for a 24 h overlap so consumers can roll without downtime — say so in the UI).
- Event-type multi-select rendered from the T-320 registry, not a hardcoded list.
- Delivery log: status filter, response viewer, payload viewer. **Keyset pagination, never OFFSET** (§7), and `VirtualList` (T-241) — delivery logs are the longest list in the product.
- Replay creates a new delivery from the stored payload. It never mutates the original row; the log is an audit trail.
- `BUDGETS` entry, i18n keys in every enabled locale, axe pass, visual baseline, route bundle budget.

**Tests**

- Module off → no route, no nav.
- Replay creates a new row with a new `delivery_id` and leaves the original untouched.
- Secret rotation keeps the old secret valid for the overlap window and then rejects it.
- Log paginates by keyset under a fixed query budget with 10k rows.

**Done when**

- [ ] A developer can add an endpoint, see a delivery fail, fix their consumer, and replay it — without support

---

## P4-D · Enterprise SSO

> Module: `enterprise_sso` (default **off**, requires `oauth`).

### T-330 ADR: SAML vs OIDC priority, and the library

> needs: —

**Goal** Decide, in writing, whether T-332 happens at all.

**Build** — `docs/adr/0006-enterprise-sso.md`, following T-252's required sections. The recommendation to argue for and record:

- **OIDC first (T-331), SAML only on a named trigger.** authlib is already a dependency and OIDC needs no new native code. `python3-saml` pulls `xmlsec` and native `libxmlsec1` into the backend image, adds its own CVE cadence, and complicates every base-image bump. That cost is worth paying for a signed enterprise contract and not for a hypothesis.
- Write the trigger explicitly — e.g. "a customer under contract whose IdP cannot do OIDC" — so the next person knows whether T-332 is theirs to do.
- Also decide and record: per-org IdP config (yes — a global IdP is not multi-tenant); JIT provisioning and its default role; group-to-role mapping (define the shape now even if unimplemented); SCIM (out of scope, named as such so it is not silently assumed).
- Record the packaging decision T-332 depends on: SAML support ships as an **optional extra** (`pip install .[saml]`), so `enterprise_sso=False` means the native dependency is genuinely not installed. This is the concrete payoff of making SSO a module at all.

**Done when**

- [ ] ADR passes `backend/scripts/check_docs.py` and is `Accepted` with a date in `## Status`
- [ ] The SAML trigger is stated as an observable condition, not a feeling

---

### T-331 OIDC SP via authlib, per-org IdP config

> needs: T-025, T-330, T-290

**Build**

- `SsoConnection`: `org_id` (FK + index, unique), provider label, issuer, `client_id`, `client_secret` (**`EncryptedString`** — §7's rule again), discovery URL, `email_domain`, `jit_provisioning`, `default_role`, `enforced`, `enabled`. Migration on the `enterprise_sso` branch.
- Flow: `GET /auth/sso/{org_slug}/start` → discovery document (cached, refreshed on rotation) → authorize with `state` + `nonce` → `/auth/sso/callback` → validate `iss`, `aud`, `exp`, `nonce`, and signature against JWKS → match by email or JIT-create → issue the **normal** session through T-025's account linking.
  - The redirect chain is new surface: T-220 lost a session to exactly this shape of cross-origin hop. Cookie attributes on the callback need a Playwright test, not an argument.
- Domain-based routing on the login page: entering an email whose domain matches an enabled, enforced connection replaces the password field with "Continue with <IdP>". **This leaks whether a domain has an org.** Decide explicitly and record it — the acceptable version for a template is to make domain hinting opt-in per org (default off), with `/auth/sso/{org_slug}/start` as the always-available entry point.
- Enforcement: an enforced org rejects password login for its members, with a break-glass — the last remaining owner can always use a password, or an org can lock itself out permanently and the fix is a DBA.
- 2FA: honour an `amr`/`acr` MFA claim from the IdP rather than prompting a second time; with no claim, TOTP still applies.
- Rate-limit the start and callback endpoints; audit every SSO login, JIT provision, and connection change.
- **Scope: web only.** A native app cannot use this redirect flow as-is — it needs `ASWebAuthenticationSession` / Custom Tabs and a deep-link return, which is its own task on top of the deep-link infrastructure T-261 already built. State that in the task rather than discovering it when a customer asks: mobile SSO is a follow-up, not a footnote.

**Tests**

- Full flow against a mock IdP (discovery, JWKS, tokens): new user JIT-provisioned with the default role; existing user linked, not duplicated.
- Wrong `nonce`, wrong `aud`, expired `exp`, bad signature — four separate rejections.
- Enforced org: password login → 403; break-glass owner → allowed.
- `client_secret` never appears in a response, log, or `repr()`.
- Module disabled → routes 404 even with a row present.
- Session cookie survives the callback redirect (Playwright, both browsers).

**Done when**

- [ ] A test org signs in end to end against a real IdP once (Okta or Entra dev tenant), then the mock covers CI
- [ ] `docs/runbooks/sso-onboarding.md` walks an admin through connecting an IdP

---

### T-332 SAML 2.0 SP via `python3-saml`

> needs: T-330 — **do not start until T-330's trigger has actually fired.**

**Build**

- `python3-saml` as an **optional extra**, per T-330. `libxmlsec1-dev` and `pkg-config` enter the backend Dockerfile's build stage only for the SAML image variant; a product with the module off must not pay for either.
- SP metadata endpoint, ACS endpoint, SLO. Per-org IdP metadata upload with signature validation on the metadata itself.
- Security, all of it non-negotiable and testable:
  - Signed assertions **required**; encrypted assertions supported.
  - Replay cache on assertion ID with a TTL past the validity window.
  - Clock-skew tolerance, bounded and configurable.
  - `NameID` format handling and mapping to the user identity.
  - **XML Signature Wrapping test vectors** in the test suite. XSW is SAML's signature bug class and every SP gets to rediscover it; borrow the standard vectors rather than inventing them.
- Reuse T-331's session issuance, enforcement, JIT provisioning and audit paths verbatim. SAML is a second front door, not a second house.

**Tests**

- Round-trip against a mock IdP; unsigned assertion rejected; replayed assertion rejected; each XSW vector rejected; skew beyond tolerance rejected.
- Image built without the extra does not import the module, and the app boots.

**Done when**

- [ ] Every XSW vector fails closed
- [ ] The default backend image contains no `libxmlsec1`

---

## P4-E · Multi-tenancy upgrade path

### T-340 ADR: row-level (current) vs schema-per-tenant

> needs: —

**Goal** Answer the question once, with numbers, so it stops being reopened in every architecture conversation.

**Build** — `docs/adr/0007-tenancy-model.md`. It must contain measurable criteria, not preferences:

- Largest tenant's share of the biggest table; noisy-neighbour incidents in the last quarter; a contractual demand for physical isolation; a per-tenant point-in-time restore requirement; tenant count and growth rate.
- The costs to state plainly: schema-per-tenant makes migration time O(tenants) and degrades sharply past roughly 1–5k schemas; per-tenant restore becomes easy and every schema change becomes a scheduled operation.
- **Name the middle option the original brief omits: PostgreSQL row-level security.** If the motivation is isolation assurance rather than performance, RLS gets most of it for a fraction of the operational cost, and it composes with the current model instead of replacing it.
- The recommendation to argue for: **stay row-level.** The performance motivations are already addressed — T-246's read replicas, T-243's query budgets, T-240's cache layer, T-244's pgbouncer. Adopt RLS if and when isolation assurance is the actual requirement.
- Revisit triggers, each an observable threshold rather than a feeling.

**Done when**

- [ ] ADR is `Accepted` with a decision, not a survey
- [ ] Every revisit trigger is something a dashboard can show

---

### T-341 Schema-per-tenant tooling

> needs: T-340 — **only if T-340 chose it.** If T-340 chose row-level, close this task as won't-do and link the ADR; that is a completed outcome, not a skipped one.

**Build** (if reached)

- `search_path` session middleware, tenant provisioning and de-provisioning, a per-org migration runner with progress and resume, per-tenant backup and restore.
- **The blocker to resolve before anything else: pgbouncer runs in transaction mode (T-244), and a session-level `SET search_path` does not survive transaction pooling.** A connection returned to the pool mid-session can hand another tenant's request a search path pointing at the wrong schema — a cross-tenant data leak, not a bug report. The options are `SET LOCAL` inside every transaction, a schema-qualified query layer, or session pooling for these connections (which forfeits what T-244 bought). Decide this in the ADR, before writing the middleware.
- Migration runner must be resumable and checkpointed — T-245's key-rotation job is the pattern to copy, including its dry-run mode.

**Done when**

- [ ] The pooling interaction has a tested answer, demonstrated by a concurrency test that would fail without it
- [ ] A tenant can be provisioned, migrated, backed up and restored from a runbook

---

## §Phase-4 verification

### T-350 Phase 4 acceptance runbook

> needs: every Phase 4 task

**Goal** Document and execute the Phase 4 acceptance flow, the same way T-160 gates Phase 2. Phase 4 is the first phase whose output is visible to people who are not users — crawlers, integrators, IdP admins — so the runbook has to be executable by someone who did not build it.

**Build**

- `docs/runbooks/phase4-verification.md`, numbered steps, matching the shape of `phase1-`, `phase2-` and `phase3-verification.md`:
  1. `make dev`, `make seed`, `pnpm --filter marketing dev` — every surface up.
  2. **Marketing:** landing in `en` and in `fi` via the locale path; nav, pricing, features, a blog post and a guides page all render; footer legal links resolve.
  3. **SEO:** `check-seo.mjs` passes on the production build; `sitemap.xml` validates and every URL in it 200s; `robots.txt` correct for the production host and `Disallow: /` on staging; `hreflang` alternates resolve in both directions; Rich Results Test clean on landing, pricing and a post.
  4. **Performance:** Lighthouse CI locally against the built output — four pages, all assertions.
  5. **Consent:** first visit sets no cookies and makes no third-party request in the default configuration (T-308).
  6. **API keys:** create a key in org settings → the secret shows once → call a scoped endpoint with it → call an unscoped one and get 403 → exceed the budget and get 429 with headers → revoke → 401. Then set `public_api=False`, restart, and confirm the same key gets 401 and the routes 404.
  7. **Webhooks:** point an endpoint at a local receiver, emit an event, stop the receiver, watch the retry schedule advance, restart it, confirm delivery, then replay a delivered event and confirm the payload is byte-identical and the original row untouched.
  8. **SSO:** connect a dev-tenant IdP to a test org, sign in a new user (JIT provisioned with the default role), enable enforcement, confirm password login is refused, confirm the break-glass owner still works.
  9. **Module flips:** set `marketing`, `public_api` and `enterprise_sso` all `False`; `make migrate && make dev && pnpm build` — the product is intact, no route 404s that should not, and `/api/v1/config` reports all three off.
  10. **Security re-scan:** ZAP baseline extended to the marketing host and to every key-callable endpoint; T-049's cross-tenant sweep and T-050's Schemathesis run both extended to the new endpoints, including the public OpenAPI document.
  11. **Load:** the k6 suite (T-223) gains an API-key-authenticated scenario. Key auth is a different hot path from JWT — a per-request lookup plus a digest comparison — and is exactly the kind of path where the argon2-on-the-event-loop class of bug hides.
- Update `docs/runbooks/module-toggle-guide.md` and `TEMPLATE.md` so a product adopting the template sees the three new toggles documented at the same level as the existing eleven.

**Done when**

- [ ] The runbook executes start to finish without intervention or undocumented steps
- [ ] Every acceptance criterion below is ticked

---

## Phase 4 acceptance criteria

Each sub-phase is independently shippable and independently gated. A sub-phase whose module is disabled in `product/config.py` passes by having its absence verified — that is a pass, not a skip.

**P4-0 · Module system**

```
- [ ] Fourteen modules; T-066's in-process matrix asserts routes and tables for every one
- [ ] T-291's CI matrix green on default, everything-off, and Phase-4-modules-on — backend and frontend
- [ ] `enterprise_sso` without `oauth` fails at boot with a message naming both
- [ ] ModuleName, ModulesResponse, ProductConfig["modules"], DENY_BY_DEFAULT and toActiveModules() agree key-for-key, enforced by a test
- [ ] `marketing=False` → root `pnpm build` green, no dist, app host unaffected
- [ ] module-toggle-guide.md and TEMPLATE.md document all three, including the re-enable path for public_api
```

**P4-A · Marketing site + SEO**

```
- [ ] ADR-0004 Accepted, names Astro, answers all four of its constraints (in particular the "use client" boundary)
- [ ] @forge/ui renders on marketing as an island with packages/ui and apps/web unmodified
- [ ] Landing, pricing, features, blog, guides, legal and 404 build in every enabled locale
- [ ] check-seo.mjs passes on every built page; JSON-LD clean in Rich Results with no CSP violation
- [ ] sitemap + robots correct per environment; hreflang alternates all resolve; no locale serves another locale's content
- [ ] Lighthouse CI green on four pages (LCP, CLS, TBT-as-INP-proxy, SEO ≥ 0.95, a11y = 1.0)
- [ ] Both hosts serve over TLS; the app's session cookie never reaches the marketing host
- [ ] Default build sets zero cookies and makes zero third-party requests
- [ ] `/pricing` absent from build, nav and sitemap when `billing` is off
```

**P4-B · Public API keys**

```
- [ ] A key authenticates only on endpoints that declare a scope; every other endpoint refuses it
- [ ] Key is shown once, stored only as a digest, and appears in no log, response or repr
- [ ] Rate limiting is per key and survives a config change without a restart
- [ ] Revocation, expiry, org deletion and module disable each kill a live key
- [ ] Public OpenAPI contains exactly the tagged endpoints; T-049 and T-050 cover them
- [ ] A newcomer creates a key and makes a successful call using only the published docs
```

**P4-C · Outbound webhooks**

```
- [ ] Signature verifies against a fixed vector; a mutated body fails
- [ ] SSRF guard rejects at save and again at delivery, including a hostname that re-resolves private
- [ ] Retry schedule observed exactly; dead-letter and auto-disable both fire; a recovered consumer receives the event
- [ ] Replay produces a new delivery with a new id and leaves the original row untouched
- [ ] Delivery log paginates by keyset under budget at 10k rows, and the retention job prunes
- [ ] Endpoint secret is EncryptedString, redacted in repr, and rotatable with an overlap window
- [ ] Delivery rate, failure rate and queue depth visible in Grafana from outside the worker
```

**P4-D · Enterprise SSO**

```
- [ ] ADR-0006 Accepted, with the SAML trigger stated as an observable condition
- [ ] OIDC end to end against a real dev tenant once, then a mock in CI
- [ ] Four separate rejections proven: bad nonce, bad aud, expired, bad signature
- [ ] Enforcement blocks password login for members; break-glass owner is never locked out
- [ ] Session survives the callback redirect in both Playwright browsers
- [ ] client_secret encrypted at rest and absent from every response, log and repr
- [ ] Module disabled → routes 404 with rows present; default image carries no libxmlsec1
- [ ] Mobile SSO is explicitly recorded as out of scope, not silently missing
```

**P4-E · Multi-tenancy upgrade path**

```
- [ ] ADR-0007 Accepted with a decision, measurable revisit triggers, and RLS evaluated as the middle option
- [ ] If row-level: T-341 closed as won't-do with the ADR linked — a completed outcome
- [ ] If schema-per-tenant: the pgbouncer transaction-mode interaction has a tested answer before any middleware is written
```

**Phase 4 gate — all of the above, plus**

```
- [ ] §7's cross-cutting checklist passes for every task in this phase
- [ ] pytest --cov thresholds not regressed; new auth-adjacent code (api_keys, sso) at 100%
- [ ] ZAP baseline extended to the marketing host and the public API, and green
- [ ] Every new setting has a .env.example entry and a line in the deploy runbook
- [ ] ADRs 0004, 0006 and 0007 pass backend/scripts/check_docs.py, and docs/adr/README.md's index is regenerated rather than hand-edited
- [ ] docs/runbooks/phase4-verification.md executes clean on a fresh checkout
```

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
