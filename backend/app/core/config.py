from functools import lru_cache
from typing import Literal, Self

from cryptography.fernet import Fernet
from pydantic import PostgresDsn, RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.module_loader import is_enabled


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")

    # Database
    DATABASE_URL: PostgresDsn
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

    @model_validator(mode="after")
    def _enforce_startup_invariants(self) -> Self:
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
            raise RuntimeError(
                "DATABASE_URL must set sslmode=require (or stricter) outside dev"
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
