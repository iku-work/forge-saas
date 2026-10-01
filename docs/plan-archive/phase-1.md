<!-- Archived from AGENTS.md on 2026-09-30. Task specs and their completion
notes, kept verbatim as the record of what each task asked for and what
was found doing it. The laws in AGENTS.md §0 still bind. -->

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
