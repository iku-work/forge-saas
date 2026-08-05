from functools import lru_cache
from typing import Literal, Self

from cryptography.fernet import Fernet
from pydantic import PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.module_loader import is_enabled


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")

    # Database
    DATABASE_URL: PostgresDsn
    # T-244: where `alembic upgrade` connects, when that must not be the same
    # place the app connects. A transaction-mode pooler hands out a different
    # server connection per transaction, which is incompatible with the
    # session-scoped state migrations rely on -- Alembic takes a session-level
    # advisory lock around the version table, and a multi-statement DDL
    # migration expects its statements to land on one backend. None (the
    # default) means "same as DATABASE_URL", which is exactly right in dev and
    # on Render, where nothing sits between the app and Postgres. See
    # docs/adr/0002-pgbouncer.md.
    MIGRATIONS_DATABASE_URL: PostgresDsn | None = None
    # T-244: true when DATABASE_URL points at pgbouncer in transaction mode.
    # Turns off every layer of prepared-statement reuse in app.core.db (see
    # build_connect_args there) -- server-side prepared statements can't
    # survive a connection that gets re-assigned at every transaction
    # boundary.
    PGBOUNCER_ENABLED: bool = False
    # T-246: a read-only replica of DATABASE_URL's database, for the query
    # classes that tolerate replication lag. None (the default) means "no
    # replica" -- app.core.db then binds read_engine to write_engine, so every
    # `use_read_replica=True` read stays on the primary and behaviour is
    # identical to a deployment that has never heard of this setting. Nothing
    # routes here by HTTP verb or by default: see docs/adr/0003-read-replica.md
    # for which reads are eligible and what must never leave the primary.
    READ_REPLICA_URL: PostgresDsn | None = None
    REDIS_URL: RedisDsn

    # Auth
    JWT_PRIVATE_KEY: str
    JWT_PUBLIC_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    # T-218/ASVS V3.3.2: REFRESH_TOKEN_EXPIRE_DAYS is now the *absolute* cap on
    # a session chain's lifetime, anchored to the original login
    # (Session.auth_time) and never extended by refresh() -- see
    # AuthService.refresh()'s docstring. REFRESH_TOKEN_IDLE_TIMEOUT_MINUTES is
    # the separate idle cap: refresh() rejects a token whose session hasn't
    # been used (rotated) within this window, even if the absolute cap hasn't
    # been reached yet. Default is the ADR's "~12h" L2 target rather than the
    # stricter "30min" option, to keep normal same-day usage from forcing a
    # re-login -- set to 30 for the stricter posture.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    REFRESH_TOKEN_IDLE_TIMEOUT_MINUTES: int = 720

    # slowapi limit strings (app.middleware.rate_limit) -- defaults match the
    # values that module hardcoded before T-220. AUTH_RATE_LIMIT in
    # particular guards register/login/verify_email, all keyed by client IP
    # until a session exists; a single multi-account browser journey (T-220's
    # E2E suite registers+verifies+logs in *two* accounts from one runner IP)
    # legitimately needs more headroom than a real client ever would, so
    # backend/.env.e2e raises this one specifically rather than the
    # production-facing default here.
    AUTH_RATE_LIMIT: str = "5/minute"
    SENSITIVE_RATE_LIMIT: str = "3/minute"
    API_RATE_LIMIT: str = "100/minute"

    # App
    ENVIRONMENT: Literal["dev", "staging", "prod"] = "dev"
    # T-223: app.core.db's engine defaults SQLAlchemy's own `echo` to
    # ENVIRONMENT == "dev" -- convenient for local debugging, but it logs
    # every SQL statement (with parameters) synchronously to stdout on the
    # request path, which k6's mixed.js load test showed adds real,
    # concurrency-multiplying latency: 50 concurrent VUs pushed p(95) to
    # ~900ms purely from this, with zero errors (it's overhead, not a
    # capacity limit). None (the default) preserves today's ENVIRONMENT-tied
    # behavior everywhere; backend/.env.loadtest sets this to false
    # explicitly so the load-test stack measures real request latency
    # instead of debug-logging overhead no production deployment would have.
    DB_ECHO: bool | None = None
    # T-223: mixed.js's ramp to 50 concurrent VUs kept p(95) elevated (with
    # DB_ECHO already off, zero errors) even after that fix -- 20 total
    # connections (5 + 15 overflow) queuing requests behind Little's-Law-style
    # contention under sustained ~80 req/s is the difference between this and
    # the argon2/DB_ECHO findings: those were pure overhead with a clear
    # "shouldn't happen at all" fix, this is a real capacity number a
    # single un-scaled dev-mode replica needs sized to its expected
    # concurrency. Defaults match today's hardcoded values exactly (no
    # behavior change for any existing env file); backend/.env.loadtest
    # raises both so the load-test stack's numbers reflect request-handling
    # latency, not this replica's pool being undersized for k6's VU count.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 15
    ALLOWED_ORIGINS: list[str] = []
    LOG_LEVEL: str = "INFO"
    DOMAIN: str = "localhost"  # used by Caddyfile.prod ($DOMAIN) and link generation
    FRONTEND_URL: str = "http://localhost:5173"  # base URL for links in transactional emails

    # Integrations
    # Optional: only required when billing is enabled (validated below) --
    # T-065 lets billing-disabled products boot with no Stripe keys at all.
    STRIPE_SECRET_KEY: str | None = None
    STRIPE_WEBHOOK_SECRET: str | None = None
    RESEND_API_KEY: str
    EMAIL_FROM_ADDRESS: str  # "Name <addr>" on a Resend-verified domain — see T-035
    # "resend" (real Resend HTTP API) | "smtp" (e.g. MailHog in dev — see docker-compose.yml's
    # mailhog service). Defaults to "resend" so existing Resend-mocked tests are unaffected;
    # local dev opts into "smtp" via .env.
    EMAIL_BACKEND: Literal["resend", "smtp"] = "resend"
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # Social OAuth (T-023) — blank disables that provider's /auth/oauth/{provider} route
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""

    # Storage
    STORAGE_BACKEND: Literal["s3", "r2", "minio"] = "minio"
    AWS_BUCKET_NAME: str = ""
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_ENDPOINT_URL: str | None = None

    # Encryption (column-level Fernet, see EncryptedString in T-009.5)
    ENCRYPTION_KEY: str
    ENCRYPTION_KEY_OLD: str = ""

    # Key-rotation job (T-245, app.workers.jobs.encryption) — defaults for a
    # run that isn't given explicit overrides. The throttle is an inter-batch
    # sleep: at the default batch size it costs 0.1s per 500 rows scanned, and
    # buys back the pool headroom (§0.9: max=20 connections) that an
    # unthrottled multi-hour scan would otherwise hold against production
    # traffic. See docs/runbooks/encryption-key-rotation.md.
    ENCRYPTION_ROTATION_BATCH_SIZE: int = 500
    ENCRYPTION_ROTATION_THROTTLE_SECONDS: float = 0.1

    # TLS enforcement — must be true in staging/prod (§0.6)
    FORCE_HTTPS: bool = False

    # GDPR (T-033) — grace period between DELETE /me and purge_user_data (T-036)
    GDPR_PURGE_GRACE_DAYS: int = 30

    # Notifications (T-219) — age (from created_at) at which an in-app
    # Notification row is hard-deleted by the retention job (T-101's model
    # docstring promised this; app.workers.scheduler.prune_old_notifications
    # is what actually delivers it).
    NOTIFICATION_RETENTION_DAYS: int = 90

    # Notifications (T-103) — global cap on open SSE streams, to bound memory
    SSE_MAX_CONNECTIONS: int = 10000

    # Virus scanning (T-113) — "clamav" | "virustotal" | "noop". "noop" (the
    # dev default) marks every uploaded file "clean" immediately.
    VIRUS_SCANNER: Literal["clamav", "virustotal", "noop"] = "noop"
    CLAMAV_HOST: str = "localhost"
    CLAMAV_PORT: int = 3310
    VIRUSTOTAL_API_KEY: str = ""

    # Superadmin (T-140) — dedicated Loguru sink admin actions are written to,
    # in addition to the normal log stream.
    ADMIN_AUDIT_LOG_PATH: str = "admin-audit.log"

    # Password pepper (T-225/ASVS V2.4.5) — an HMAC key mixed into every
    # password before it reaches Argon2 (see app.modules.auth.utils), on top
    # of Argon2's own per-hash salt. Blank in dev (matches every other
    # dev-optional secret here); required outside dev, enforced below.
    # Vault/HSM-held storage of this value is intentionally out of scope —
    # see docs/adr/0001-security-baseline.md's V2.4.5 row.
    PASSWORD_PEPPER: str = ""

    @field_validator("MIGRATIONS_DATABASE_URL", "READ_REPLICA_URL", mode="before")
    @classmethod
    def _blank_optional_dsn_means_unset(cls, value: object) -> object:
        # `MIGRATIONS_DATABASE_URL=` (and, since T-246, `READ_REPLICA_URL=`) is
        # how every other optional key in .env.example spells "not set"
        # (SENTRY_DSN, ENCRYPTION_KEY_OLD, the OAuth pairs), and those all
        # happen to be `str`-typed so pydantic takes the empty string as-is.
        # PostgresDsn doesn't -- without this, copying .env.example verbatim
        # fails startup with a URL parse error on a key the copier never meant
        # to set.
        return None if value == "" else value

    @property
    def migrations_database_url(self) -> str:
        """The DSN `alembic upgrade` connects with — never the pooler.

        Falls back to DATABASE_URL so every existing env file (dev, .env.test,
        Render) keeps its current single-DSN behaviour untouched; only a
        deployment that actually puts a transaction-mode pooler in front of
        Postgres has to set MIGRATIONS_DATABASE_URL, and _enforce_startup_
        invariants below makes that non-optional once PGBOUNCER_ENABLED is on.
        """
        return str(self.MIGRATIONS_DATABASE_URL or self.DATABASE_URL)

    @model_validator(mode="after")
    def _enforce_startup_invariants(self) -> Self:
        # T-244: not gated on ENVIRONMENT — running migrations through a
        # transaction-mode pooler is broken everywhere, not just in prod, and
        # the failure mode (an advisory lock silently taken on a connection
        # the next statement no longer holds) is far cheaper to catch at
        # startup than halfway through a DDL migration.
        if self.PGBOUNCER_ENABLED:
            if self.MIGRATIONS_DATABASE_URL is None:
                raise RuntimeError(
                    "MIGRATIONS_DATABASE_URL must be set when PGBOUNCER_ENABLED is true — "
                    "migrations must connect directly to Postgres, not through the pooler "
                    "(see docs/adr/0002-pgbouncer.md)"
                )
            if self.MIGRATIONS_DATABASE_URL == self.DATABASE_URL:
                raise RuntimeError(
                    "MIGRATIONS_DATABASE_URL is identical to DATABASE_URL while "
                    "PGBOUNCER_ENABLED is true — it must point directly at Postgres, "
                    "bypassing pgbouncer (see docs/adr/0002-pgbouncer.md)"
                )
        if self.ENVIRONMENT != "dev":
            if "*" in self.ALLOWED_ORIGINS:
                raise RuntimeError("ALLOWED_ORIGINS must not contain '*' outside dev")
            if not self.FORCE_HTTPS:
                raise RuntimeError("FORCE_HTTPS must be true in non-dev environments")
            if self.VIRUS_SCANNER == "noop":
                raise RuntimeError(
                    "VIRUS_SCANNER must not be 'noop' outside dev — uploaded files would "
                    "never actually be scanned. Set it to 'clamav' or 'virustotal'."
                )
            if not self.PASSWORD_PEPPER:
                raise RuntimeError("PASSWORD_PEPPER must be set outside dev")
            self._validate_transport_tls()
        self._validate_encryption_key()
        return self

    def _validate_transport_tls(self) -> None:
        # T-225/ASVS V1.9.1, V9.2.2: outside dev, DB/Redis traffic must be
        # encrypted end-to-end, not just the client-facing hop Caddy
        # terminates. Render's managed Postgres/Redis already require this on
        # their external connection strings; self-hosted deployments
        # (docker-compose.prod.yml) must be configured to match — see
        # docs/runbooks/backup-restore.md's "Docker-network TLS" section.
        database_query = self.DATABASE_URL.query or ""
        if "sslmode=require" not in database_query and "sslmode=verify" not in database_query:
            raise RuntimeError("DATABASE_URL must set sslmode=require (or stricter) outside dev")
        # T-244: the migration DSN is a second, independent hop to Postgres —
        # it bypasses the pooler entirely, so DATABASE_URL's own sslmode says
        # nothing about it.
        if self.MIGRATIONS_DATABASE_URL is not None:
            migrations_query = self.MIGRATIONS_DATABASE_URL.query or ""
            if (
                "sslmode=require" not in migrations_query
                and "sslmode=verify" not in migrations_query
            ):
                raise RuntimeError(
                    "MIGRATIONS_DATABASE_URL must set sslmode=require (or stricter) outside dev"
                )
        # T-246: and so is the replica -- a third independent hop, carrying
        # exactly the same rows as the primary.
        if self.READ_REPLICA_URL is not None:
            replica_query = self.READ_REPLICA_URL.query or ""
            if "sslmode=require" not in replica_query and "sslmode=verify" not in replica_query:
                raise RuntimeError(
                    "READ_REPLICA_URL must set sslmode=require (or stricter) outside dev"
                )
        if self.REDIS_URL.scheme != "rediss":
            raise RuntimeError("REDIS_URL must use the rediss:// scheme outside dev")

    def _validate_encryption_key(self) -> None:
        try:
            Fernet(self.ENCRYPTION_KEY.encode("ascii"))
        except ValueError as exc:
            raise OSError(
                "ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_stripe_keys(settings: Settings) -> None:
    """
    Startup check (T-065), deliberately *not* part of Settings' own
    model_validator: that validator runs on every Settings() construction,
    including incidental ones in tests/lifespan, each of which would
    eagerly resolve+cache module_loader.get_modules() -- poisoning that
    cache with whatever product.config.MODULES happened to be active at
    the time, ahead of a test's own monkeypatch. Calling this once,
    explicitly, from app.main's module scope (alongside get_modules()'s own
    validation) avoids that.
    """
    if is_enabled("billing") and (
        settings.STRIPE_SECRET_KEY is None or settings.STRIPE_WEBHOOK_SECRET is None
    ):
        raise RuntimeError(
            "STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET are required when billing is "
            "enabled. Set both, or disable billing in backend/product/config.py."
        )
