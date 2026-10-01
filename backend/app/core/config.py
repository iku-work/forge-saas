from functools import lru_cache
from typing import Literal, Self

from argon2.profiles import RFC_9106_LOW_MEMORY
from cryptography.fernet import Fernet
from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.module_loader import is_enabled

# VIRUS_SCANNER values that cannot establish a file is safe, and so must not
# be selected outside dev. "noop" returns clean without looking; "virustotal"
# is a reputation lookup that has no record of any novel file -- including,
# by definition, every targeted one -- so it fails closed there rather than
# guessing. See app.modules.storage.scanner's module docstring.
_NON_SCANNING_VIRUS_SCANNERS = frozenset({"noop", "virustotal"})

# The profile argon2-cffi's PasswordHasher() uses by default -- read, not
# retyped, so the settings' defaults and the floor below cannot drift from it.
_ARGON2_DEFAULTS = RFC_9106_LOW_MEMORY


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
    # The public half of the keypair being rotated *away* from. Access tokens
    # are verified against the current key first and this one second, so a
    # keypair rotation stops signing everyone out mid-request. Signing always
    # uses JWT_PRIVATE_KEY; there is deliberately no old *private* key,
    # because nothing should still be minting tokens with a retired one.
    #
    # Clear it once ACCESS_TOKEN_EXPIRE_MINUTES has elapsed -- after that no
    # token signed by the old key can still be valid.
    JWT_PUBLIC_KEY_OLD: str = ""

    # Signing secret for the tokens that travel in URLs and emails: email
    # verification, password reset, magic links, email-change confirm/cancel,
    # the OAuth `state` parameter and the 2FA challenge token.
    #
    # Blank means "use JWT_PRIVATE_KEY", which is what these tokens were
    # always signed with -- app.modules.auth.utils._url_token_serializer
    # borrowed it for want of a dedicated field. That coupling is why
    # rotating the JWT keypair silently invalidated every link a user had
    # been emailed, which is a far larger blast radius than the 15-minute
    # access tokens the name suggests.
    #
    # Setting it separates the two, so each can be rotated without touching
    # the other. See docs/runbooks/credential-rotation.md for the migration,
    # which is not automatic on purpose: switching to a dedicated secret
    # would otherwise invalidate every link in flight at the moment of
    # deploy.
    URL_TOKEN_SECRET: str = ""
    # The previous URL_TOKEN_SECRET. itsdangerous verifies against every key
    # it is given and signs with the last, so both stay valid during a
    # rotation. Keep it for at least the longest token TTL -- 24 hours, the
    # email-verification link -- then clear it.
    URL_TOKEN_SECRET_OLD: str = ""
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
    # Review R5: the combined budget of every person in one organization,
    # charged after membership is verified (app.middleware.budgets). 5000/hour
    # was decided 2026-09-29 in place of §0.6's original 1000/hour -- about 16
    # requests a minute for a whole org, which a few active users would spend
    # -- and is to be confirmed from a week of measured traffic (the plan's
    # D1). API keys are not counted here; they have their own budget.
    ORG_RATE_LIMIT: str = "5000/hour"
    # T-312: the per-minute budget an API key gets, before any per-key
    # override (ApiKey.rate_limit_per_minute). An integer rather than a
    # slowapi limit string because this limit is not applied by slowapi's
    # decorator -- app.modules.api_keys.rate_limit consumes it directly, from
    # a dependency that already knows which key is calling. Read through
    # get_settings() at request time, never bound at import: see that
    # module's docstring, and app/middleware/rate_limit.py's, for the trap.
    #
    # 600/minute rather than API_RATE_LIMIT's 100: a server-to-server
    # integration is not a browser, and a limit tuned for one person clicking
    # is the limit an integration hits on its first backfill.
    API_KEY_RATE_LIMIT_PER_MINUTE: int = 600

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
    # T-350: where the *worker* serves its own /metrics. Prometheus scrapes
    # the API and the worker separately because they are separate processes
    # with separate registries -- see metrics.start_worker_metrics_server.
    # Not published to the host by docker-compose: like the API's own
    # /metrics (T-202), this is an inside-the-network endpoint.
    WORKER_METRICS_PORT: int = 9100
    DOMAIN: str = "localhost"  # used by Caddyfile.prod ($DOMAIN) and link generation
    # T-307: the *marketing* host, which is a different one -- the apex, with
    # the app on `app.`. Read by Caddyfile.prod's generated marketing block
    # ($MARKETING_DOMAIN) and nothing in this process, but it has to be
    # declared here all the same: the caddy service loads backend/.env, and
    # Settings forbids extra keys, so an undeclared MARKETING_DOMAIN in that
    # file would stop the *backend* from booting.
    #
    # Never blank. Caddy substitutes an env var that is set-but-empty as the
    # empty string rather than falling back to the `{$MARKETING_DOMAIN:...}`
    # default, which leaves a site block with no address -- and Caddy then
    # reads its opening brace as a global-options block and refuses to start
    # the *whole* edge, app host included (measured, not assumed). The default
    # here is a `.localhost` name so that the failure mode of forgetting this
    # setting is a marketing host nobody can resolve.
    MARKETING_DOMAIN: str = "marketing.localhost"
    # The object-storage host, for a self-hosted deployment that runs MinIO on
    # the same box (docker-compose.prod.yml). Declared here for exactly the
    # reason MARKETING_DOMAIN above is -- Caddy reads it from backend/.env and
    # this process never does, but an undeclared key in that file would stop
    # the backend booting.
    #
    # It needs a public hostname rather than an internal one because there is
    # only one AWS_ENDPOINT_URL: the presigned upload and download URLs handed
    # to the *browser* are built from the same endpoint the backend uses
    # server-side, and a SigV4 download signature covers the Host header, so
    # the browser cannot be pointed somewhere else after the fact.
    #
    # Blank when storage is a managed service (S3, R2) -- then there is no
    # local MinIO, AWS_ENDPOINT_URL points at the provider, and the generated
    # site block below is simply not imported. Same never-empty rule as
    # MARKETING_DOMAIN applies when it *is* in use.
    STORAGE_DOMAIN: str = "storage.localhost"
    # The way out of app.core.storage_endpoint's boot refusal, for the
    # deployment where the object store genuinely is at a public address --
    # MinIO on a separate host, say. Off by default: the configuration it
    # permits is the one that silently loses a fraction of uploads when a CDN
    # sits in front of the domain.
    ALLOW_PUBLIC_STORAGE_ENDPOINT: bool = False
    FRONTEND_URL: str = "http://localhost:5173"  # base URL for links in transactional emails
    # T-261: the custom URL scheme the mobile app registers (apps/mobile/app.json's
    # `expo.scheme`). Only the impersonation handoff deep link is built from it
    # today, but it is the app's identity on the device, not that one feature's
    # setting — a product that renames the app renames this once.
    MOBILE_DEEP_LINK_SCHEME: str = "forge"

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

    # /metrics exposure. Until now the *only* thing that ever kept the
    # Prometheus endpoint off the public internet was `respond /metrics 404`
    # in both Caddyfiles -- app.core.metrics registered it with no
    # authentication at all. That holds exactly as long as every deployment
    # puts Caddy in front of the app, which a platform deployment (Render,
    # Fly, Cloud Run) does not: there the app *is* the edge, and /metrics
    # answers 200 to anyone with the URL, handing out route inventory,
    # traffic volumes, pool saturation and active session counts.
    #
    # So one of these two must be a deliberate answer outside dev, enforced
    # in _enforce_startup_invariants below:
    #   METRICS_TOKEN            -- require `Authorization: Bearer <token>`
    #                               on /metrics (Prometheus: `bearer_token`
    #                               in the scrape config).
    #   METRICS_BLOCKED_AT_EDGE  -- assert a reverse proxy already refuses
    #                               the route, which is the docker-compose
    #                               posture and is verified for those two
    #                               files by tests/test_caddy_config.py.
    METRICS_TOKEN: str | None = None
    METRICS_BLOCKED_AT_EDGE: bool = False

    # Which peer addresses are allowed to speak for a client via
    # X-Forwarded-For / X-Forwarded-Proto. Comma-separated; CIDR accepted.
    #
    # **Almost never "*".** X-Forwarded-For is a list that each proxy
    # *appends* to -- Caddy's reverse_proxy does, and so does Render's router,
    # which does not reset a client-supplied value. So a request arriving with
    # a forged header produces `<forged>, <real client>`. Given a real list of
    # trusted addresses, uvicorn walks the list from the **right** and takes
    # the first untrusted entry, which is the address the last proxy actually
    # observed. Given "*" it takes the **leftmost** entry instead -- the one
    # the caller supplied -- so every attacker picks their own rate-limit
    # bucket and writes whatever they like into the audit trail. Measured;
    # "*" is worse than leaving this unset, not better.
    #
    # The proxy reaches this app over a private network in every deployment
    # here (Render's router, Caddy on forge-net), so the private ranges are
    # the right answer and are what .env.example ships:
    #   10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
    # Add your CDN's published ranges too if one sits in front, or its egress
    # address becomes the rightmost untrusted entry and every caller behind it
    # shares a bucket again.
    #
    # Empty (the default) means the app believes the socket, which is right
    # for local dev and wrong behind *any* reverse proxy -- and this app is
    # never deployed without one. Getting it wrong is quiet and expensive:
    # `request.client.host` is what app.middleware.rate_limit keys an
    # unauthenticated limit on, so every caller on the internet collapses
    # into one shared bucket (one attacker locks out every user), and it is
    # also the `ip` recorded on every audit event (core/dependencies.py,
    # modules/auth/router.py, modules/admin/router.py) -- so the security
    # audit trail records the proxy for every action ever taken.
    #
    # uvicorn has its own --forwarded-allow-ips for this, defaulting to
    # 127.0.0.1, which no containerised proxy ever is. It is applied here
    # instead of in the start command so it travels with the configuration
    # rather than with however the process happened to be launched.
    TRUSTED_PROXY_IPS: str = ""

    # Whether REDIS_URL is allowed to be plain `redis://` outside dev.
    #
    # The rule it relaxes (ASVS V9.2.2, T-225) exists because Redis carries
    # sessions, rate-limit counters, the job queue and cached rows -- none of
    # which should cross an untrusted network in the clear. But "encrypted"
    # and "not on an untrusted network" are two ways of satisfying the same
    # requirement, and some managed offerings only give you the second:
    # Render's Key Value exposes `redis://red-xxx:6379` on its private network
    # and reserves TLS for the *external* URL, which costs an IP allowlist and
    # a public-internet round trip for every session lookup.
    #
    # Default false, so the strict behaviour is unchanged unless a deployment
    # says otherwise. Setting it true is a claim about the network, and it is
    # only true if nothing but your own services can route to that instance.
    # It does not relax the Postgres DSNs -- those keep `sslmode` regardless.
    REDIS_ON_PRIVATE_NETWORK: bool = False

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
    # The absolute per-file ceiling, enforced for every upload whether or
    # not the billing module is on. The plan's own `max_upload_mb` feature
    # is a *product* limit and only exists where there is a plan to read it
    # from; switching billing off used to remove the only cap in the
    # system, which made "no billing" mean "unbounded upload". A template
    # that lets anyone fill its disk the moment billing is disabled is a
    # template bug, not a product decision -- so this one is not negotiable
    # by module config. A product that wants larger uploads raises it here
    # deliberately, having thought about its disk.
    MAX_UPLOAD_BYTES: int = 100 * 1024 * 1024

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

    # Outbound webhooks (T-321). `webhook_delivery` is the fastest-growing
    # table this template adds — one row per event per endpoint, each carrying
    # a stored payload — so the retention window is a setting rather than a
    # decision deferred until the disk fills. The prune job
    # (app.workers.jobs.webhooks.prune_webhook_deliveries) deletes in batches.
    WEBHOOK_DELIVERY_RETENTION_DAYS: int = 30
    # Connect *and* read timeout for one delivery attempt. One slow consumer
    # must never occupy a worker; ten seconds is generous for a request whose
    # only job is to return 200 and enqueue something.
    WEBHOOK_TIMEOUT_SECONDS: float = 10.0
    # Review R6: the whole attempt -- DNS, connect, send, and reading the
    # response -- end to end. The timeout above is per operation, so a
    # receiver trickling a byte at a time never tripped it.
    WEBHOOK_DEADLINE_SECONDS: float = Field(default=30.0, gt=0)

    # Virus scanning (T-113) — "clamav" | "virustotal" | "noop". Only "clamav"
    # actually scans: "noop" (the dev default) marks every uploaded file
    # "clean" immediately, and "virustotal" is a hash lookup that fails closed
    # on anything it has not seen before. Both are refused outside dev by
    # _NON_SCANNING_VIRUS_SCANNERS below.
    VIRUS_SCANNER: Literal["clamav", "virustotal", "noop"] = "noop"
    CLAMAV_HOST: str = "localhost"
    CLAMAV_PORT: int = 3310
    VIRUSTOTAL_API_KEY: str = ""
    # Review R4: how many scans the scan worker runs at once. Each streams
    # rather than buffering, so memory per scan is a few chunks; this bounds
    # clamd connections and object-store reads, and how much of the scan
    # worker's memory budget a burst can claim.
    SCAN_MAX_CONCURRENCY: int = Field(default=4, ge=1)

    # Superadmin (T-140) — dedicated Loguru sink admin actions are written to,
    # in addition to the normal log stream.
    ADMIN_AUDIT_LOG_PATH: str = "admin-audit.log"

    # Argon2id cost. The defaults are argon2-cffi's own (RFC 9106's second
    # recommended profile) and the floor outside dev. Raising them migrates
    # stored hashes on each user's next login (check_password's needs_rehash);
    # lowering them is for the test suite, where every register, login and
    # 2FA enrollment would otherwise pay ~50 ms per hash.
    ARGON2_TIME_COST: int = _ARGON2_DEFAULTS.time_cost
    ARGON2_MEMORY_KIB: int = _ARGON2_DEFAULTS.memory_cost
    ARGON2_PARALLELISM: int = _ARGON2_DEFAULTS.parallelism

    # Password pepper (T-225/ASVS V2.4.5) — a secret mixed into every
    # password before it reaches Argon2 (see app.modules.auth.utils), on top
    # of Argon2's own per-hash salt. Blank in dev (matches every other
    # dev-optional secret here); required outside dev, enforced below.
    # Vault/HSM-held storage of this value is intentionally out of scope —
    # see docs/adr/0001-security-baseline.md's V2.4.5 row.
    PASSWORD_PEPPER: str = ""
    # The pepper being rotated *away* from. Set it for as long as it takes
    # logins to drain, then clear it.
    #
    # Named for its sibling ENCRYPTION_KEY_OLD, and it works the same way:
    # verification falls back to it, and every successful login re-hashes
    # that user's password with the current pepper (see
    # app.modules.auth.utils.check_password). The difference is that nothing
    # sweeps the table in the background -- a password cannot be re-hashed
    # without the plaintext, which only the user has, so rows migrate as
    # people sign in and never all at once. Accounts that never log in again
    # keep their old hash, which is why this is cleared on a judgement call
    # about login volume rather than on a completion signal.
    #
    # Blank is the normal state, and blank means the fallback is skipped
    # entirely: while it is set, a *wrong* password costs two Argon2
    # verifications instead of one.
    PASSWORD_PEPPER_OLD: str = ""

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

    def _non_dev_violations(self) -> list[str]:
        """What makes this configuration unfit outside dev, most important first.

        A table rather than a chain of ifs: adding a rule is adding a row, and
        the first violated rule is the one reported.
        """
        weak_argon2 = [
            name
            for name, value, floor in (
                ("ARGON2_TIME_COST", self.ARGON2_TIME_COST, _ARGON2_DEFAULTS.time_cost),
                ("ARGON2_MEMORY_KIB", self.ARGON2_MEMORY_KIB, _ARGON2_DEFAULTS.memory_cost),
                ("ARGON2_PARALLELISM", self.ARGON2_PARALLELISM, _ARGON2_DEFAULTS.parallelism),
            )
            if value < floor
        ]
        rules = (
            ("*" in self.ALLOWED_ORIGINS, "ALLOWED_ORIGINS must not contain '*' outside dev"),
            (not self.FORCE_HTTPS, "FORCE_HTTPS must be true in non-dev environments"),
            (
                self.VIRUS_SCANNER in _NON_SCANNING_VIRUS_SCANNERS,
                f"VIRUS_SCANNER={self.VIRUS_SCANNER!r} does not scan anything and must "
                "not be used outside dev. 'noop' marks every file clean; 'virustotal' "
                "only looks a hash up in a reputation database, which can report a "
                "file known-bad but can never establish that an unknown one is safe. "
                "Set VIRUS_SCANNER=clamav (docker-compose.prod.yml ships a clamav "
                "service behind the 'clamav' profile).",
            ),
            (not self.PASSWORD_PEPPER, "PASSWORD_PEPPER must be set outside dev"),
            (
                bool(weak_argon2),
                f"{', '.join(weak_argon2)} below argon2-cffi's defaults outside dev -- "
                "cheaper password hashes are for the test suite only",
            ),
            (
                not self.METRICS_TOKEN and not self.METRICS_BLOCKED_AT_EDGE,
                "/metrics would be publicly readable. Set METRICS_TOKEN to require a "
                "bearer token on it, or METRICS_BLOCKED_AT_EDGE=true if a reverse "
                "proxy in front of this app already refuses the route (both Caddyfiles "
                "in this repo do). See app/core/metrics.py.",
            ),
        )
        return [message for violated, message in rules if violated]

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
            violations = self._non_dev_violations()
            if violations:
                raise RuntimeError(violations[0])
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
        if self.REDIS_URL.scheme != "rediss" and not self.REDIS_ON_PRIVATE_NETWORK:
            raise RuntimeError(
                "REDIS_URL must use the rediss:// scheme outside dev. Set "
                "REDIS_ON_PRIVATE_NETWORK=true only if that instance is reachable from "
                "nothing but your own services -- see that field's docstring."
            )

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
