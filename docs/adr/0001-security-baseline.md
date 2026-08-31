# ADR-0001: Security Baseline — OWASP ASVS 4.0 Level 2

## Status

Accepted

**Date:** 2026-07-08

## Context

[T-212](../../agents.md) made "every OWASP Top 10 risk has a test proving its
mitigation" a machine-checked claim (`docs/security/owasp-top10.md`). That
table is necessarily coarse — ten categories can't represent everything a
security review or an enterprise questionnaire needs to know. This document
is the finer-grained baseline underneath it: every applicable requirement
from [OWASP ASVS 4.0.3](https://github.com/OWASP/ASVS), Level 2, across the
chapters that apply to a web/API backend (V1–V9, V12–V14 — V10 Malicious
Code and V11 Business Logic are excluded: they're either not meaningfully
verifiable by inspection or are product-specific and out of a template's
scope).

Level 2 (not Level 1) was chosen as the target because this template
handles multi-tenant customer data, authentication, and payments —
Level 1 is the bar for applications with no sensitive data; Level 3 is for
high-assurance / life-critical systems, which this is not.

This is a point-in-time snapshot verified against the codebase at the time
of writing. Requirement text below is paraphrased for brevity; the ID is
the authoritative pointer to the full ASVS 4.0.3 wording.

## Decision

Adopt the checklist below as the project's security baseline. Rows are
evidence-linked: `met` rows link the code, test, or config that satisfies
the requirement; `partial` rows name what's missing and a follow-up task;
`n/a` rows explain why the requirement doesn't apply to this template.

This document is referenced by [T-214](../../agents.md)'s pentest scope and
is written for an external reader (auditor, enterprise security
questionnaire) — evidence links point at real files, not at task IDs.

### V1 — Architecture, Design and Threat Modeling

#### V1.1 Secure Software Development Lifecycle

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.1.1 | Secure SDLC addressing security at every stage | met | `docs/security/sdlc.md` names where security fits in planning/review/CI/release, referencing the same CI gates (`.github/workflows/ci.yml` `security` job: bandit, semgrep, trivy, pip-audit, gitleaks, trufflehog). |
| V1.1.2 | Threat modeling per design change / sprint | met | `docs/security/sdlc.md` §1 requires `docs/security/threat-model.md` to be updated in the same PR as any change touching auth/crypto/tenant isolation/a new remote service; full-ADR review on a slower, named cadence. |
| V1.1.3 | User stories carry functional security constraints (e.g. "can't view others' data") | met | `backend/tests/security/test_tenant_isolation.py` encodes exactly this constraint as an executable, parametrized spec across 13 endpoints. |
| V1.1.4 | Trust boundaries, components, data flows documented | met | `docs/security/threat-model.md` — dedicated data-flow diagram and a trust-boundary table (client/Caddy/backend/worker/Postgres/Redis/object storage/third parties). |
| V1.1.5 | High-level architecture + connected remote services security-analyzed | met | `docs/security/threat-model.md` §3 — per-component trust analysis of Stripe, Resend, OAuth providers, and object storage. |
| V1.1.6 | Centralized, reusable security controls (not duplicated per-route) | met | `backend/app/core/dependencies.py` (`require_permission`, `require_superadmin`), `backend/app/middleware/security_headers.py`, `backend/app/core/url_guard.py`, `backend/app/core/encrypted_type.py` — each is a single implementation every route/model shares. |
| V1.1.7 | Secure coding checklist/policy available to developers | met | `docs/security/sdlc.md` §2–§3 (secure-coding reference + reviewer checklist), alongside the existing `docs/security/owasp-top10.md`. |

#### V1.2 Authentication Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.2.1 | Unique, low-privilege OS accounts per component | met | `backend/Dockerfile` creates a dedicated non-root `app` system user with no shell/home (`useradd --system --gid app --no-create-home --shell /usr/sbin/nologin app`), owns `/app`, and `USER app` runs the process. |
| V1.2.2 | Inter-component communication authenticated, least-privilege | met | Backend↔Postgres/Redis via credentialed DSNs (`backend/app/core/config.py` `DATABASE_URL`, `REDIS_URL`, required with no default). Monolith architecture — no internal service-to-service API surface requiring separate authentication. |
| V1.2.3 | Single vetted auth mechanism, extensible, sufficiently logged | met | One JWT+refresh-token mechanism (`backend/app/modules/auth/service.py`) extended with TOTP 2FA and OAuth; login events audited via `backend/app/modules/audit/service.py`. |
| V1.2.4 | Consistent auth strength across all pathways | met | Password login, OAuth, and magic-link all converge on the same session-issuance path (`create_access_token`/`generate_refresh_token` in `backend/app/modules/auth/utils.py`) — no weaker alternate token type. |

#### V1.4 Access Control Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.4.1 | Enforcement only on a trusted server layer, never the client | met | `backend/app/core/dependencies.py` `require_permission`/`get_current_org` — server-side FastAPI dependencies, no client-side gating. |
| V1.4.4 | Single, well-vetted access-control mechanism for all requests | met | `backend/app/modules/auth/permissions.py` docstring: "`require_permission` is the only place that should consult `ROLE_PERMISSIONS`." |
| V1.4.5 | Attribute/feature-based checks, not role alone | met | Org-scoping is the attribute check: `backend/app/modules/tenancy/service.py` explicitly re-checks `target.org_id != actor_membership.org_id` before every mutation, on top of role-based `ROLE_PERMISSIONS`. |

#### V1.5 Input and Output Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.5.1 | Input/output handling requirements defined per data type/policy | met | `docs/security/sdlc.md` §2 "Input/output handling policy" writes down the one-policy-per-type rule already enforced in code (`backend/app/core/schemas.py`'s `AppBaseModel`, the null-byte guard, the `Envelope` output shape). |
| V1.5.2 | No insecure deserialization of untrusted data | met | All wire format is JSON via Pydantic/FastAPI; no `pickle` or similarly unsafe deserialization anywhere in request handling. |
| V1.5.3 | Input validation enforced on a trusted service layer | met | `backend/app/core/schemas.py` `AppBaseModel` validates every request body before a handler runs (FastAPI dependency injection). |
| V1.5.4 | Output encoding close to the interpreter | met | JSON-only API via the `Envelope` shape (`backend/app/core/handlers.py`); one serialization boundary, not scattered per-route encoding. |

#### V1.6 Cryptographic Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.6.1 | Explicit key-management policy following a lifecycle standard | met | `docs/security/key-management.md` — generation, storage/holders, rotation procedure (including the existing `ENCRYPTION_KEY_OLD`/`MultiFernet` support, `backend/app/core/encrypted_type.py`), and compromise response for every key material in use. |
| V1.6.2 | Key material protected via vault/API, not app-held secrets | partial | Keys load from environment variables (hosting-platform secret store), not a dedicated vault/KMS. T-225 evaluated this and deliberately deferred it: moving `ENCRYPTION_KEY`/`JWT_PRIVATE_KEY` into a specific vault (HashiCorp Vault, AWS KMS, etc.) is a vendor/infra decision a template shouldn't force on every deployer — env-var-via-hosting-secret-store is the intentional baseline here. |
| V1.6.3 | Keys/passwords replaceable, part of a defined re-encryption process | met | `ENCRYPTION_KEY_OLD` + `MultiFernet` (`backend/app/core/encrypted_type.py:33,50-86`) support rotation without downtime; `backend/app/workers/jobs/encryption.py` re-encrypts. |
| V1.6.4 | Client-side secrets never used to protect sensitive data | met | All cryptography (Fernet, JWT signing, password hashing) happens server-side only, in `backend/app/core/encrypted_type.py` and `backend/app/modules/auth/utils.py`. |

#### V1.7 Errors, Logging and Auditing Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.7.1 | Common logging format/approach across the system | met | `backend/app/core/logging.py` — single Loguru-based structured JSON config; `_InterceptHandler` forwards stdlib `logging` into the same sinks. |
| V1.7.2 | Logs securely transmitted to a remote system for analysis | partial | `docs/runbooks/log-shipping.md` documents what's actually true today (exceptions reach Sentry over TLS, traces reach an OTLP collector over TLS if configured) and gives the reasoned non-plan for full stdout-log aggregation: it's a hosting-platform-specific choice, not a repo default, and is deliberately deferred to whoever deploys this template rather than tracked as an open task here. |

#### V1.8 Data Protection and Privacy Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.8.1 | All sensitive data identified and classified | met | `docs/security/data-classification.md` (T-219) — one row per PII-bearing column across every module, with a classification (public/internal/sensitive) and current protection. |
| V1.8.2 | Protection levels have associated requirements, applied in the architecture | met | Same doc, "Current protection" and "Retention" columns state the concrete requirement per classification (encryption for direct identifiers/secrets, scrubbing from logs, a retention/expiry schedule or a documented reason it's kept indefinitely) and whether it's met, per row. |

#### V1.9 Communications Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.9.1 | Encrypted communication between components across containers/sites | met | Client-facing traffic is TLS-terminated at Caddy (`Caddyfile.prod`). `Settings._enforce_startup_invariants` (`backend/app/core/config.py`, T-225) now refuses to boot outside dev unless `DATABASE_URL` sets `sslmode=require`+ and `REDIS_URL` uses `rediss://`. Render's managed Postgres/Redis satisfy this by default; self-hosted `docker-compose.prod.yml` deployers must complete the cert setup in `docs/runbooks/backup-restore.md`'s "Docker-network TLS" section or the app won't start. |
| V1.9.2 | Components verify authenticity of each side of a communication link | met | Outbound calls to Stripe/Resend/OAuth providers use standard HTTP-client TLS certificate verification (no `verify=False` found anywhere in the codebase). |

#### V1.10 Malicious Software Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.10.1 | Source control with access control and traceable check-ins | met | Git history on GitHub, per-user commits, CI-gated PRs (`.github/workflows/ci.yml`). |

#### V1.11 Business Logic Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.11.1 | Application components documented by business/security function | met | `agents.md` documents each module (auth, billing, tenancy, gdpr, storage, admin, notifications, feature_flags) and its responsibilities. |
| V1.11.2 | Auth/session/access-control flows don't share unsynchronized state | met | All three flow through the same Postgres-backed `Session`/`Membership`/`User` rows; the Redis user-cache is explicitly invalidated on writes (`backend/app/core/dependencies.py:35,73-107`), not left to drift. |

#### V1.12 Secure File Upload Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.12.2 | Uploaded files served via octet-stream/unrelated domain + CSP | met | Files are served directly from S3/R2/MinIO via presigned URLs — never proxied or rendered by the backend. `backend/app/modules/storage/service.py`, `backend/app/modules/storage/backends/`. |

#### V1.14 Configuration Architecture

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V1.14.1 | Segregation of differing trust levels via gateway/reverse proxy | met | Caddy reverse-proxies to the backend and blocks `/metrics` externally (`Caddyfile.prod`). |
| V1.14.2 | Binary signatures/trusted connections for remote device deployment | n/a | Template ships a web backend only; no remote/IoT device deployment exists. |
| V1.14.3 | Build pipeline warns of out-of-date/insecure components | met | `pip-audit` + `pnpm audit` + `trivy` gate the `build` job in `.github/workflows/ci.yml` (not `continue-on-error`). |
| V1.14.4 | Build pipeline verifies secure deployment of software-defined infra | met | CI builds/pushes the Docker image and scans it with trivy. The `deploy` job's "Guard against VIRUS_SCANNER=noop in production" step (`.github/workflows/ci.yml`, T-225) additionally fails the deploy before it ships if the production env file would leave uploaded files unscanned. |
| V1.14.5 | Deployments sandboxed/containerized/isolated | met | Docker containerization, non-root user, separate backend/worker services in `docker-compose.yml`. |
| V1.14.6 | No unsupported/deprecated client-side tech (Flash, ActiveX, etc.) | met | `apps/web/package.json` — React 18 + Vite 6 + TypeScript SPA; no legacy plugin technology in the stack. |

### V2 — Authentication

#### V2.1 Password Security

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V2.1.1 | Minimum 12-character passwords | met | `backend/app/modules/auth/schemas.py` — `Field(min_length=12)` on `RegisterRequest.password` and the reset-confirm `new_password`. |
| V2.1.2 | Allow ≥64 characters, deny >128 | met | `backend/app/modules/auth/schemas.py` — `_PASSWORD_MAX_LENGTH = 128` applied via `Field(max_length=...)` to every password-accepting field (`RegisterRequest`, `LoginRequest`, `PasswordResetConfirmRequest`, `ChangePasswordRequest`, `Disable2FARequest`). Closed by T-216. |
| V2.1.3 | No password truncation | met | Argon2 (`backend/app/modules/auth/utils.py`) hashes the full password; no truncation logic present (unlike bcrypt's 72-byte limit). |
| V2.1.4 | Any printable Unicode, including emoji, permitted | met | Pydantic `str` fields accept full Unicode by default; composition-rule restriction removed by T-216 (see V2.1.9). |
| V2.1.5 | Users can change their password | met | `POST /api/v1/auth/change-password` (`backend/app/modules/auth/router.py`, `AuthService.change_password`) — an authenticated change-password endpoint alongside the existing forgot-password reset flow. Closed by T-216. |
| V2.1.6 | Password change requires current + new password | met | `ChangePasswordRequest(current_password, new_password)` — `AuthService.change_password` re-verifies `current_password` via Argon2 before accepting `new_password`. Closed by T-216. |
| V2.1.7 | Passwords checked against a breached-password list | met | `backend/app/modules/auth/schemas.py` `_COMMON_PASSWORDS` (~1000 entries) with trailing-noise stripping, checked on registration, password reset, and change-password. |
| V2.1.8 | Password strength meter shown to users | met | `packages/ui/src/components/password-strength-meter.tsx` (`PasswordStrengthMeter`), wired into `RegisterPage`, `ResetPasswordPage`, and Settings > Security's `ChangePasswordCard`. Closed by T-216. |
| V2.1.9 | No password composition rules (no forced upper/lower/digit/special) | met | `_validate_password_strength` (`backend/app/modules/auth/schemas.py`) dropped the upper/lower/digit composition check per ASVS 4.0.3's recommendation against composition rules (NIST SP 800-63B) — length (`min_length=12`, `max_length=128`) + the breached-password denylist are now the whole policy. Mirrored on the frontend in `packages/validation/src/index.ts`'s `passwordSchema`. Closed by T-216. |
| V2.1.10 | No periodic credential rotation or password-history requirement | met | No password-history table or rotation policy exists in `backend/app/modules/auth/models.py` or `backend/app/core/config.py`. |
| V2.1.11 | Paste and password managers permitted | met | `backend/app/modules/auth/schemas.py` declares `password` as a plain `str` field — no client-side restriction is possible from a JSON API; nothing blocks paste or autofill. |
| V2.1.12 | User can temporarily reveal a masked password | met | `packages/ui/src/components/password-input.tsx` (`PasswordInput`) — a reveal-toggle button, wired into every password field (login, register, reset, change-password). Closed by T-216. |

#### V2.2 General Authenticator Security

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V2.2.1 | Anti-automation: lockout/rate-limiting, ≤100 failed attempts/hour | met | Account lockout after 10 failed attempts within a 15-minute window (`backend/app/modules/auth/service.py:49-50,143-162`) plus `5/minute` rate limit on the login route (`backend/app/middleware/rate_limit.py:35`) — well under the 100/hour ceiling. |
| V2.2.2 | Weak authenticators (SMS/email) limited to secondary use | met | `backend/app/modules/auth/service.py` — 2FA (`enable_2fa`/`verify_2fa`) uses TOTP, not SMS; magic-link email is an alternate primary login path, not represented as a "stronger" factor. |
| V2.2.3 | Secure notification sent after auth-detail changes | met | `AuthService._enqueue_security_notification_email` sends the `security_notification` email (`packages/email-templates/src/templates/SecurityNotification.tsx`) on password change, 2FA enable/disable, and OAuth link/unlink. Closed by T-216. |

#### V2.3 Authenticator Lifecycle

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V2.3.1 | System-generated secrets are random, short-lived, can't become long-term | met | Magic-link/reset tokens and TOTP backup codes use `secrets.token_urlsafe`/`token_hex` with TTLs (15 min–24 h) and are single-use. |
| V2.3.2 | Enrollment of hardware authenticators (U2F/FIDO) supported | met | `POST /auth/webauthn/register/options` + `/register` (`backend/app/modules/auth/webauthn.py`, `AuthService.begin_webauthn_registration`/`complete_webauthn_registration`) register a `WebAuthnCredential` as an alternative to TOTP at the same 2FA gate in `login()`. Closed by T-217. |
| V2.3.3 | Renewal instructions sent for time-bound authenticators | n/a | TOTP secrets don't expire; backup codes are consumed, not renewed. |

#### V2.4 Credential Storage

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V2.4.1 | Passwords salted+hashed with an approved KDF | met | Argon2id via `argon2-cffi` (`backend/app/modules/auth/utils.py:16,26`). |
| V2.4.2 | Unique salt ≥32 bits per credential | met | Argon2's `PasswordHasher` generates a unique random salt per hash automatically. |
| V2.4.3 | PBKDF2 iteration count | n/a | PBKDF2 is not used — Argon2 is used instead. |
| V2.4.4 | bcrypt work factor | n/a | bcrypt is not used. |
| V2.4.5 | Additional server-side secret pepper (HSM-protected) | partial | An HMAC-SHA256 pepper (`PASSWORD_PEPPER`) is now mixed into every password before Argon2, on top of its own per-hash salt (`backend/app/modules/auth/utils.py`, `_peppered`, T-225) — required outside dev. Still `partial`: the pepper itself lives in an env var, not an HSM/vault, deferred for the same vault/KMS vendor decision as V1.6.2/V6.4.1/V6.4.2. |

#### V2.5 Credential Recovery

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V2.5.1 | Initial recovery secret not sent in clear text | met | `backend/app/modules/auth/service.py` `request_password_reset`/`request_magic_link` — single-use, short-TTL (15 min–1 h) signed tokens delivered over TLS; standard, accepted practice for email-based recovery links. |
| V2.5.2 | No security questions | met | No "secret question" field exists in `backend/app/modules/auth/models.py` or anywhere else in the codebase. |
| V2.5.3 | Recovery never reveals the current password | met | `backend/app/modules/auth/service.py` `confirm_password_reset` sets a new password; the old one is never returned or displayed. |
| V2.5.4 | No shared/default accounts | met | No hardcoded default admin/root credentials found; `backend/app/core/seed.py` is dev-only seed data. |
| V2.5.5 | User notified if an auth factor is changed/replaced | met | Same fix as V2.2.3 — `_enqueue_security_notification_email` fires on 2FA enable/disable and OAuth link/unlink, not just password change. Closed by T-216. |
| V2.5.6 | Recovery uses a secure mechanism (TOTP, offline, signed link) | met | Reset/magic-link uses `itsdangerous`-signed, single-use, time-limited tokens (`backend/app/modules/auth/utils.py:111-117`). |
| V2.5.7 | Lost-MFA recovery re-verifies identity at enrollment level | met | `disable_2fa` (self-service) still requires password + code. For the actual lost-everything case, `POST /admin/users/{id}/disable-totp` (`AdminAccountRecoveryService.disable_totp_for_user`, `backend/app/modules/admin/service.py`) is a mandatory-reason, fully-audited (`severity="elevated"`) superadmin action, documented end-to-end (identity-verification steps, limitations) in `docs/runbooks/account-recovery.md`. Closed by T-218. |

#### V2.6 Look-up Secret Verifier

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V2.6.1 | Lookup secrets (backup codes) usable only once | met | `_consume_backup_code` removes the code from the list on use (`backend/app/modules/auth/service.py:470-479`). |
| V2.6.2 | Sufficient entropy, or salted+hashed | met | Each backup code (`secrets.token_hex(5)`) is individually Argon2-hashed at rest — satisfies the salted-and-hashed alternative. |
| V2.6.3 | Resistant to offline attacks (non-predictable) | met | Generated via the `secrets` module (CSPRNG), Argon2-hashed at rest. |

#### V2.7 Out of Band Verifier

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V2.7.1 | Weak OOB channels (SMS/PSTN) not offered by default | n/a | Template implements no out-of-band (SMS/push) authenticator at all — 2FA is TOTP (see V2.8), not OOB. |
| V2.7.2 | OOB requests expire after 10 minutes | n/a | No OOB authenticator implemented. |
| V2.7.3 | OOB codes usable once, for the original request only | n/a | No OOB authenticator implemented. |
| V2.7.4 | OOB authenticator and verifier use a secure independent channel | n/a | No OOB authenticator implemented. |
| V2.7.5 | OOB verifier retains only a hashed version of the code | n/a | No OOB authenticator implemented. |
| V2.7.6 | OOB initial code has ≥20 bits of entropy | n/a | No OOB authenticator implemented. |

#### V2.8 One Time Verifier

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V2.8.1 | Time-based OTP has a defined lifetime | met | `pyotp.TOTP(...).verify(code, valid_window=1)` — standard 30-second step, ±1 window (`backend/app/modules/auth/service.py`). |
| V2.8.2 | Symmetric keys for OTP verification highly protected | met | TOTP secret stored via `EncryptedString` (Fernet, encrypted-at-rest) — an appropriate protection level for L2; HSM-backed storage is an L3 expectation. |
| V2.8.3 | Approved crypto algorithms for OTP generation/verification | met | `pyotp` implements RFC 6238 TOTP (HMAC-SHA1), an industry-standard library. |
| V2.8.4 | Time-based OTP usable only once within its validity window | met | `TOTPSecret.last_consumed_step` (added by T-218) tracks the wall-clock step of the last code actually consumed; `AuthService._verify_totp_and_track_replay` rejects any code matching that step or earlier even though it's still inside pyotp's own `valid_window=1` tolerance. Applied uniformly at every verification call site (`verify_2fa`, `disable_2fa`, `confirm_login_2fa`). Closed by T-218. |
| V2.8.5 | Reused time-based OTP is logged, rejected, and notified | met | Same mechanism as V2.8.4 — a rejected replay is logged (`logger.warning`) and the user is emailed a `totp_replay_rejected` security-notification event, not just silently rejected. Closed by T-218. |
| V2.8.6 | Physical OTP generator revocable, effective across sessions immediately | met | TOTP is software-based, not physical; `disable_2fa` immediately revokes, and `backend/app/core/token_blocklist.py` supports immediate session-wide invalidation. |

#### V2.9 Cryptographic Verifier

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V2.9.1 | Verification keys stored securely (TPM/HSM) | met | WebAuthn's own model: the private key never leaves the authenticator's TPM/Secure Enclave/security key. `WebAuthnCredential.public_key` (`backend/app/modules/auth/models.py`) stores only the public COSE key handed back by `verify_registration_response` — the RP never sees, transmits, or stores private key material. Closed by T-217. |
| V2.9.2 | Challenge nonce ≥64 bits, unique | met | `webauthn.generate_registration_options`/`generate_authentication_options` default to `helpers.generate_challenge()` — 64 random bytes (512 bits) via `secrets.token_bytes`, freshly generated per ceremony in `begin_webauthn_registration`/`begin_webauthn_login` (`backend/app/modules/auth/service.py`). Closed by T-217. |
| V2.9.3 | Approved crypto algorithms for challenge generation/verification | met | `py_webauthn`'s default `supported_pub_key_algs` (EdDSA, ES256, RS256 — all FIDO/NIST-approved COSE algorithms) gate `verify_registration_response`/`verify_authentication_response`; the challenge itself is CSPRNG-generated (see V2.9.2). Closed by T-217. |

#### V2.10 Service Authentication

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V2.10.1 | Intra-service secrets aren't unchanging passwords/shared accounts | partial | `DATABASE_URL`/`REDIS_URL` are long-lived static credentials (standard for managed Postgres/Redis) without automated rotation. T-225 evaluated this: automated rotation requires the same vault/KMS vendor decision as V1.6.2/V6.4.1/V6.4.2, deliberately deferred rather than built against one vendor's API. |
| V2.10.2 | Service account isn't a default credential | met | `DATABASE_URL`/`REDIS_URL` are required fields with no default value (`backend/app/core/config.py`) — the app cannot boot with a "root/root"-style default. |
| V2.10.3 | Service passwords protected against offline recovery | partial | Credentials live in environment variables / the hosting platform's secret store, not independently hardened beyond that — same vault/KMS deferral as V2.10.1. |
| V2.10.4 | Secrets not committed to source, secure key store recommended | met | All secrets load via `Settings`/env (`backend/app/core/config.py`); `ENCRYPTION_KEY`/JWT keys are required fields with no defaults; trufflehog + gitleaks CI gates catch accidental commits. |

### V3 — Session Management

#### V3.1 Fundamental Session Management Security

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V3.1.1 | Session tokens never in URL parameters | met | Refresh token lives in an httpOnly cookie; access token in the `Authorization` header — neither in a URL. `backend/app/modules/auth/router.py`. |

#### V3.2 Session Binding

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V3.2.1 | New session token generated on authentication | met | `login()`/OAuth/2FA-confirm each call `create_access_token`+`generate_refresh_token` fresh (`backend/app/modules/auth/service.py`). |
| V3.2.2 | Session tokens have ≥64 bits of entropy | met | `secrets.token_urlsafe(32)` = 256 bits (`backend/app/modules/auth/utils.py:103`). |
| V3.2.3 | Tokens stored in the browser via secure methods | met | Refresh token is a secure httpOnly cookie; access token lives only in a module-scoped in-memory store (`packages/api-client/src/client.ts` — "In-memory only -- the access token must never touch localStorage/cookies"), read by `packages/hooks/src/useAuth.ts`. Verified by T-216; no code change needed, `localStorage`/`sessionStorage` grepped clean for token usage. |
| V3.2.4 | Session tokens generated via approved crypto algorithms | met | JWT RS256 for the access token; `secrets.token_urlsafe` (CSPRNG) for the refresh token. |

#### V3.3 Session Termination

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V3.3.1 | Logout/expiry invalidates the token, no back-button resume | met | `logout()` revokes the session row; access tokens expire in 15 minutes; refresh-token reuse triggers full revocation (`backend/app/modules/auth/service.py:202-237`). |
| V3.3.2 | Periodic re-authentication (L2 target: ~12h or 30min idle) | met | `Session.auth_time` (added by T-218) anchors a session chain to its original login and is never extended by `refresh()`'s rotation, so `REFRESH_TOKEN_EXPIRE_DAYS` is now a true absolute cap rather than a sliding one — a session forces a fresh login after 30 days regardless of activity. A separate `REFRESH_TOKEN_IDLE_TIMEOUT_MINUTES` (default 720, i.e. 12h — the ADR's own cited L2 target) rejects a refresh whose session hasn't been used within that window; set to 30 for the stricter alternative target. `require_recent_auth` additionally gates specific sensitive actions on a 5-minute step-up window (see V3.7.1). Closed by T-218. |
| V3.3.3 | Option to terminate all other sessions after password change | met | `confirm_password_reset` revokes all sessions (`backend/app/modules/auth/service.py:273`); `logout_all()` available on demand. |
| V3.3.4 | Users can view/log out of all active sessions | met | `GET /api/v1/auth/sessions` + `DELETE /api/v1/auth/sessions/{id}` (`backend/app/modules/auth/router.py`), scoped to the caller and revocable individually; surfaced in Settings > Security's `SessionsCard` (`apps/web/src/features/settings/SecuritySettingsPage.tsx`). `logout_all()` remains available for "sign out everywhere". |

#### V3.4 Cookie-based Session Management

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V3.4.1 | `Secure` attribute set | met | `backend/app/modules/auth/router.py` `_set_refresh_cookie()` — `secure=True`. |
| V3.4.2 | `HttpOnly` attribute set | met | Same location — `httponly=True`. |
| V3.4.3 | `SameSite` attribute limits CSRF exposure | met | Same location — `samesite="strict"`. |
| V3.4.4 | `__Host-` cookie-name prefix used | met | Refresh cookie renamed to `__Host-refresh_token` (`backend/app/modules/auth/router.py`). Closed by T-216. |
| V3.4.5 | Cookie `path` attribute scoped as precisely as possible | met | `path="/"` (`backend/app/modules/auth/router.py`) — widened from `/api/v1/auth` because the `__Host-` prefix (V3.4.4) requires `Path=/`; still scoped by `httponly`+`secure`+`samesite="strict"`. |

#### V3.5 Token-based Session Management

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V3.5.1 | Users can revoke OAuth tokens linked to their account | met | `POST /api/v1/auth/oauth/{provider}/unlink` (`backend/app/modules/auth/oauth.py`, `AuthService.unlink_oauth`) — blocked only if it's the account's sole authentication method (no password, no other linked provider). Surfaced in Settings > Security's `LinkedAccountsCard`. Closed by T-216. |
| V3.5.2 | Session tokens used, not static API secrets | met | `backend/app/modules/auth/service.py` — rotating session tokens throughout; no static API-key auth mechanism exists yet (planned separately as T-310/T-311). |
| V3.5.3 | Stateless tokens protected against tampering/replay | met | JWT is RS256-signed; `itsdangerous`-signed URL tokens for reset/magic-link/OAuth state. |

#### V3.7 Defenses Against Session Management Exploits

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V3.7.1 | Full valid session or re-auth required before sensitive actions | met | `disable_2fa` still requires password re-entry directly. The new `require_recent_auth(max_age_minutes=5)` dependency (`backend/app/core/dependencies.py`) additionally gates `POST /auth/change-email`, `POST /billing/checkout-session`, and `DELETE /orgs/{org_id}/members/{membership_id}` on the caller's session having a recent enough `auth_time` (a real login/2FA/WebAuthn event, never advanced by silent access-token refresh) — a stale session gets `403 step_up_auth_required` instead of silently succeeding. Closed by T-218. |

### V4 — Access Control

#### V4.1 General Access Control Design

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V4.1.1 | Access control enforced on a trusted server layer | met | `backend/app/core/dependencies.py`. |
| V4.1.2 | Access-control attributes/policy data can't be client-manipulated | met | Role comes from the DB-controlled `Membership.role`; the `X-Org-ID` header alone grants nothing without a matching `Membership` row (`backend/app/core/dependencies.py:126-140`). |
| V4.1.3 | Principle of least privilege | met | `ROLE_PERMISSIONS` scoping (`backend/app/modules/auth/permissions.py`) + the cross-tenant sweep in `backend/tests/security/test_tenant_isolation.py`. |
| V4.1.5 | Access controls fail securely, including on exception | met | `require_permission`/`get_current_org` raise `PermissionDeniedError` (403) rather than defaulting open. |

#### V4.2 Operation Level Access Control

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V4.2.1 | Protection against IDOR (create/read/update/delete) | met | `backend/tests/security/test_tenant_isolation.py` sweeps 13 org-scoped endpoints; `backend/app/modules/tenancy/service.py` explicitly re-checks `org_id` before mutating a caller-supplied membership. |
| V4.2.2 | Strong anti-CSRF for authenticated actions, anti-automation for unauthenticated | met | `SameSite=Strict` refresh cookie + bearer-token API auth (requires a custom header a cross-site form can't set) + rate limiting (`backend/app/middleware/rate_limit.py`). |

#### V4.3 Other Access Control Considerations

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V4.3.1 | Administrative interfaces require MFA | met | `require_superadmin` (`backend/app/core/dependencies.py`) now also rejects a superadmin caller with neither `totp_enabled` nor a registered WebAuthn credential (`errors.superadmin_2fa_required`, 403) before the admin-audit log line is even written. Closed by T-218. |
| V4.3.2 | No directory browsing/metadata disclosure | met | Backend is a containerized app served through Caddy; no static directory listing is configured, and `.git`/build artifacts aren't part of the runtime image. |
| V4.3.3 | Step-up/adaptive auth or segregation of duties for high-value actions | met | Admin impersonation retains its own strong controls (superadmin-only, 60-minute cap, fully audited — `backend/app/modules/admin/service.py:576-648`). General step-up auth for other high-value actions now exists too — see V3.7.1's `require_recent_auth` evidence. Closed by T-218. |

### V5 — Validation, Sanitization and Encoding

#### V5.1 Input Validation

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V5.1.1 | Defenses against HTTP parameter pollution | met | `backend/app/core/schemas.py` — FastAPI/Pydantic binds each declared field once from its designated source; no ambiguous multi-source parameter merging. |
| V5.1.2 | Protection against mass parameter assignment | met | Request schemas explicitly whitelist fields (structural allow-list); Pydantic v2's default `extra="ignore"` silently drops anything not declared, so unknown fields never reach a model or the DB. |
| V5.1.3 | All input validated via positive validation (allow-lists) | met | Pydantic typed schemas throughout (`backend/app/core/schemas.py`) plus null-byte rejection (`_reject_null_bytes`, lines 40-45). |
| V5.1.4 | Structured data strongly typed against a defined schema | met | `backend/app/core/schemas.py` — Pydantic field types/constraints on every request DTO. |
| V5.1.5 | Redirects/forwards only to allow-listed destinations | n/a | No user-controlled redirect/forward parameter exists anywhere in the API. |

#### V5.2 Sanitization and Sandboxing

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V5.2.1 | Untrusted HTML from WYSIWYG sanitized | n/a | No WYSIWYG/rich-text HTML input feature exists in the product. |
| V5.2.2 | Unstructured data sanitized (allowed characters/length) | met | `backend/app/core/schemas.py` — Pydantic length constraints + the null-byte scrubber (`_reject_null_bytes`, lines 40-45). |
| V5.2.3 | Input sanitized before mail systems (SMTP/IMAP injection) | met | `backend/app/workers/jobs/email.py` — prod path sends via the Resend HTTP API (`_send_via_resend`); dev path (`EMAIL_BACKEND=smtp`, MailHog) uses `smtplib.SMTP.send_message` with Python's `email` library building the MIME message, not raw string concatenation into SMTP commands. Neither path is injectable by user-controlled header values. |
| V5.2.4 | No `eval()`/dynamic code execution on user input | met | No `eval`/`exec` use in application code; enforced by the bandit CI gate. |
| V5.2.5 | Template injection protection | n/a | No server-side HTML templating engine renders user input (JSON-only API). |
| V5.2.6 | SSRF protection via allow-listed protocols/domains/ports | met | `backend/app/core/url_guard.py` `validate_outbound_url()` rejects private/loopback/reserved IPs, DNS rebinding and non-http(s) schemes. T-320 gave it its first caller: the outbound-webhook module calls it when an endpoint is saved *and* again immediately before each delivery, because a hostname that resolved publicly yesterday can resolve to the metadata address today. |
| V5.2.7 | User-supplied SVG sanitized/sandboxed | n/a | `image/svg+xml` is not in the upload content-type allow-list (`backend/app/modules/storage/service.py:39-53`); no SVG rendering surface exists. |
| V5.2.8 | User-supplied scriptable template content (Markdown/CSS/XSL) sandboxed | n/a | No Markdown/CSS/XSL rendering of user-submitted content in the backend. |

#### V5.3 Output Encoding and Injection Prevention

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V5.3.1 | Context-relevant output encoding | met | JSON-only responses via FastAPI's `jsonable_encoder`; no other output context exists. |
| V5.3.2 | Output encoding preserves the user's charset/Unicode | met | `backend/app/core/handlers.py` — responses serialized via FastAPI's `jsonable_encoder`, JSON + UTF-8 throughout. |
| V5.3.3 | Context-aware escaping against XSS | n/a | No HTML rendering surface in the backend (this ADR covers the backend; the frontend SPA's JSX-escaping is a separate concern outside this document's scope). |
| V5.3.4 | Parameterized queries / ORM used | met | SQLAlchemy ORM throughout, no raw string SQL — enforced by a Semgrep rule (`docs/security/owasp-top10.md`, A03 row). |
| V5.3.5 | Context-specific encoding where parameterization is unavailable | n/a | No raw-SQL fallback path exists that would need this. |
| V5.3.6 | Protection against JSON injection/eval | met | `backend/app/core/handlers.py` — standard library/Pydantic JSON serialization; no eval-based JSON parsing anywhere in the codebase. |
| V5.3.7 | LDAP injection protection | n/a | No LDAP integration exists. |
| V5.3.8 | OS command injection protection | met | No `subprocess`/`os.system` calls built from user input; enforced by the bandit CI gate. |
| V5.3.9 | LFI/RFI protection | met | Storage keys are server-generated UUIDs (`backend/app/modules/storage/service.py:105`) — no file path is ever built from user input. |
| V5.3.10 | XPath/XML injection protection | n/a | No XML/XPath processing exists (JSON-only API). |

#### V5.4 Memory, String, and Unmanaged Code

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V5.4.1 | Memory-safe string/pointer handling | n/a | Python is memory-managed; no unmanaged/native-pointer code in the application layer. |
| V5.4.2 | Format strings don't take hostile input | n/a | No C-style format-string functions used. |
| V5.4.3 | Integer-overflow prevention | n/a | Python integers are arbitrary-precision; no manual fixed-width arithmetic in application code. |

#### V5.5 Deserialization Prevention

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V5.5.1 | Serialized objects integrity-checked/encrypted | met | `EncryptedString` (Fernet — authenticated encryption) for the TOTP secret; JWTs are signed. No pickle-based serialization of untrusted data exists. |
| V5.5.2 | XML parsers hardened against XXE | n/a | No XML parsing of untrusted input (JSON-only API). |
| V5.5.3 | Deserialization of untrusted data avoided/protected | met | Only JSON deserialization via Pydantic; no `pickle`/`yaml.load` of untrusted input. |
| V5.5.4 | `JSON.parse` used, not `eval()`, for JSON parsing | n/a | Backend is Python; this requirement targets browser/JS-based parsing, which is a frontend concern outside this document. |

### V6 — Stored Cryptography

#### V6.1 Data Classification

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V6.1.1 | Regulated PII stored encrypted at rest | partial | `TOTPSecret.secret_encrypted` and, as of T-219, `User.full_name` (`backend/app/modules/auth/models.py:30`) are encrypted via `EncryptedString`. `User.email` stays plaintext — a documented, reasoned exception (unique lookup key; see `docs/security/data-classification.md` §2) rather than a silent gap, which is why this row stays `partial` rather than flipping to `met`. |
| V6.1.2 | Regulated health data stored encrypted | n/a | Template stores no health data by default — product-specific data classification is each deployment's own responsibility. |
| V6.1.3 | Regulated financial data stored encrypted | n/a | Card/financial data never touches the backend — Stripe-hosted Checkout/Billing Portal; only non-sensitive references (customer ID, subscription ID, plan slug) are stored locally. |

#### V6.2 Algorithms

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V6.2.1 | Crypto modules fail securely, no padding-oracle risk | met | Fernet (`backend/app/core/encrypted_type.py`) is authenticated encryption — a tampered/invalid token raises a clean `ValueError` (lines 216-234), not a distinguishable padding error. |
| V6.2.2 | Industry-approved algorithms/libraries, no custom crypto | met | `cryptography` (Fernet/AES), `argon2-cffi` (Argon2id), RS256 JWT — all vetted libraries, no home-rolled cryptography. |
| V6.2.3 | IV/cipher configuration per current best practice | met | `backend/app/core/encrypted_type.py` — Fernet generates its IV internally per its fixed, vetted construction; no manual IV handling in application code. |
| V6.2.4 | Algorithms/keys reconfigurable/upgradable | met | `backend/app/core/encrypted_type.py:33,50-86` — `MultiFernet` + `ENCRYPTION_KEY_OLD` support key rotation without a hardcoded single key. |
| V6.2.5 | No insecure block modes / weak hashes (ECB, MD5, SHA1) | met | `backend/app/core/encrypted_type.py` (Fernet: AES-CBC+HMAC-SHA256), `backend/app/modules/auth/utils.py` (Argon2); SHA-256 is used only for non-secret refresh-token lookup hashing. |
| V6.2.6 | Nonces/IVs never reused with the same key | met | `backend/app/core/encrypted_type.py` — delegated to Fernet's internal per-encryption random IV generation. |

#### V6.3 Random Values

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V6.3.1 | CSPRNG used for random values/filenames/GUIDs | met | `secrets.token_urlsafe`/`token_hex`, `uuid4()` used throughout for tokens/keys/storage object IDs. |
| V6.3.2 | Random GUIDs use v4 + CSPRNG | met | `uuid4()` used for storage keys and other identifiers. |

#### V6.4 Secret Management

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V6.4.1 | Secrets-management solution (vault) for key lifecycle | partial | Keys load from environment variables (hosting-platform secret store), not a dedicated vault (e.g. HashiCorp Vault, AWS KMS). Same deliberate T-225 deferral as V1.6.2 — a specific vault/KMS integration is a vendor decision, not a template default. |
| V6.4.2 | Key material isolated from the app, vault-mediated crypto ops | partial | `ENCRYPTION_KEY` loads directly into app memory, not vault-mediated — same deferral as V6.4.1. |

### V7 — Error Handling and Logging

#### V7.1 Log Content

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V7.1.1 | No credentials/payment details logged; session tokens only hashed | met | `_scrub()` redacts keys matching `password`/`token`/`secret`/`api_key`/`authorization`/`cookie` (`backend/app/core/logging.py:31-52`), applied to every log record. |
| V7.1.2 | No other privacy-law-sensitive data logged | met | T-219 extended `_scrub()`/`_is_sensitive_key()` to also redact by exact PII field name (`email`, `full_name`), not just secret-shaped substrings (`backend/app/core/logging.py:39-52`) — a `logger.bind(email=...)` call site no longer has to remember to name the key `*_token` to get scrubbed. |
| V7.1.3 | Security-relevant events logged (auth success/failure, access-control failures, validation failures) | met | Login success/failure and account lockout are audited (`user.login_success`, `user.login_failed`, `user.account_locked` — `backend/app/modules/auth/service.py`); admin access is logged (`admin.access`); permission-denied (403) from `require_permission` now writes a `permission.denied` `AuditEvent` (T-219, `backend/app/core/dependencies.py`). |
| V7.1.4 | Each log event carries enough context for investigation | met | Structured JSON logs carry `request_id`/`actor_id`/timestamps; audit rows carry `actor_id`/`org_id`/`resource_id`/`diff`/`ip`/`ua`. |

#### V7.2 Log Processing

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V7.2.1 | All authentication decisions logged, no sensitive tokens | met | T-219: a wrong-password attempt now writes a `user.login_failed` `AuditEvent`, and the 10th attempt that triggers a lockout writes a separate `severity="elevated"` `user.account_locked` event, alongside the pre-existing `user.login_success` (`backend/app/modules/auth/service.py`). No token/password value is ever included — only actor/ip/ua. |
| V7.2.2 | All access-control decisions loggable, failures logged | met | Same `permission.denied` `AuditEvent` as V7.1.3 (`backend/app/core/dependencies.py`); `require_superadmin`'s pre-existing `admin.access` log line covers the platform-admin gate. |

#### V7.3 Log Protection

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V7.3.1 | Logging encodes data to prevent log injection | met | `backend/app/core/logging.py` — structured JSON logging (Loguru); values are JSON-encoded fields, not concatenated free text. |
| V7.3.3 | Security logs protected from unauthorized access/modification | met | `admin-audit.log` is a separate file sink; `AuditEvent` has "no repository method for UPDATE or DELETE" (`backend/app/modules/audit/models.py` docstring) — append-only by design. |
| V7.3.4 | Time sources synchronized, UTC logging | met | `utcnow()` mixin used throughout models (`backend/app/models/_mixins.py`); containerized deploy keeps consistent UTC clocks. |

#### V7.4 Error Handling

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V7.4.1 | Generic message on unexpected/sensitive error, with a correlation ID | met | `unhandled_exception_handler` (`backend/app/core/handlers.py:59-65`) returns a generic `errors.internal_error` message with no stack trace; `backend/app/middleware/request_id.py` provides a correlation ID. |
| V7.4.2 | Exception handling used across the codebase | met | `DomainError` taxonomy (`backend/app/core/exceptions.py`) + FastAPI exception handlers cover expected and unexpected cases uniformly. |
| V7.4.3 | Last-resort catch-all handler defined | met | `unhandled_exception_handler` catches bare `Exception` (`backend/app/core/handlers.py:59-65`). |

### V8 — Data Protection

#### V8.1 General Data Protection

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V8.1.1 | Sensitive data not cached in server components (load balancers, caches) | met | `Cache-Control: no-store` set by default on every response (`backend/app/middleware/security_headers.py:30`). |
| V8.1.2 | Cached/temp server-side copies purged after use | met | `no-store` prevents proxy caching; presigned download URLs expire after 1 hour (`backend/app/modules/storage/service.py`, `_DOWNLOAD_EXPIRES_IN_SECONDS`). |
| V8.1.3 | Minimal parameters per request (no hidden fields) | met | `backend/app/core/schemas.py` — JSON API with explicitly declared schema fields only, no hidden-form-field pattern. |
| V8.1.4 | Detect/alert on abnormal request volume | met | `RequestVolumeAnomaly` (`docs/grafana/alerts.yml`, T-225) pages when a route's rate-limit-rejection rate (`http_requests_total{status="429"}`, already produced by `prometheus-fastapi-instrumentator`) sustains over 1/sec for 5 minutes. Deliberately per-route, not per-user/per-IP — true per-IP cardinality in Prometheus is itself a metrics-storage/PII risk. |
| V8.1.5 | Regular backups with tested restores | partial | `docs/runbooks/backup-restore.md` (T-225) documents the restore procedure for both the Render-managed and self-hosted paths in detail. Still `partial`: the "tested" half requires executing a restore against a live instance, which doesn't exist for this template yet — the runbook's own "Verification status" checklist tracks this explicitly rather than claiming it silently. |
| V8.1.6 | Backups stored securely | partial | Same runbook as V8.1.5 — Render's managed backups are provider-secured by default; the self-hosted path's operator-owned backup archive isn't independently verified here. |

#### V8.2 Client-side Data Protection

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V8.2.1 | Sufficient anti-caching headers so browsers don't cache sensitive data | met | `Cache-Control: no-store` applies to all responses (`backend/app/middleware/security_headers.py:30`). |
| V8.2.2 | Sensitive data absent from browser storage (localStorage etc.) | met | Refresh token is httpOnly (never script-readable); access token lives only in a module-scoped in-memory variable (`packages/api-client/src/client.ts`), never `localStorage`/`sessionStorage`. Verified by T-216. |
| V8.2.3 | Authenticated data cleared from client storage after session end | met | `useAuth().logout()`/`onSessionExpired` (`packages/hooks/src/useAuth.ts`) call `setAccessToken(null)`, clearing the in-memory token; nothing was ever written to persistent client storage to begin with. Verified by T-216. |

#### V8.3 Sensitive Private Data

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V8.3.1 | Sensitive data sent in body/headers, never query strings | met | All mutating endpoints use body payloads; the bearer token travels in the `Authorization` header, never a URL. |
| V8.3.2 | Users can export/remove their data on demand | met | `backend/app/modules/gdpr/service.py` `request_export()`/`delete_me()`; `backend/app/modules/gdpr/router.py` `GET /me/export`, `DELETE /me`. |
| V8.3.3 | Clear consent language, opt-in before data use | n/a | Consent-copy/UX is a product-specific, legally-reviewed concern; the template provides only the backend primitive (`record_consent`/`list_consents`, `backend/app/modules/gdpr/service.py:61-74`) that a product's consent UI is built on. |
| V8.3.4 | Sensitive data identified with a handling policy | met | Same as V1.8.1/V1.8.2 — `docs/security/data-classification.md`. |
| V8.3.5 | Access to sensitive data audited, without logging the data itself | met | `AuditService.log()` records action/resource metadata and a diff, not raw field values (`backend/app/modules/audit/service.py:32-63`). |
| V8.3.6 | Sensitive data in memory overwritten after use | n/a | CPython offers no reliable memory-zeroing guarantee for a garbage-collected language — an inherent limitation shared by all Python applications, not a gap specific to this codebase. |
| V8.3.7 | Sensitive data encrypted with algorithms providing confidentiality+integrity | met | `backend/app/core/encrypted_type.py` — where encryption is applied (TOTP secret), Fernet provides both (encrypt-then-MAC). Coverage breadth is tracked separately under V6.1.1. |
| V8.3.8 | Sensitive data subject to a retention/auto-deletion schedule | partial | GDPR purge (`GDPR_PURGE_GRACE_DAYS`), expired-session pruning, and expired data-export-URL clearing exist. T-219 added `prune_old_notifications` (`backend/app/workers/scheduler.py`, `NOTIFICATION_RETENTION_DAYS`) as the next-highest-value gap. Still not a schedule for *every* data class — `docs/security/data-classification.md` §10 names the remaining ones (`Invitation.email`, `PushToken.token`, `WebhookEvent.payload`) as an explicit, prioritized backlog rather than a silent gap, which is why this row stays `partial`. |

### V9 — Communication

#### V9.1 Client Communication Security

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V9.1.1 | TLS for all client connectivity, no plaintext fallback | met | Caddy HTTP→HTTPS redirect (`Caddyfile.prod`); `FORCE_HTTPS` enforced outside `dev` (`backend/app/core/config.py:90-91`). |
| V9.1.2 | Only strong cipher suites enabled, strongest preferred | partial | A `testssl.sh` step (`.github/workflows/zap-full.yml`, T-225) now scans `STAGING_URL`'s actual negotiated cipher suites nightly, same dormant-until-`STAGING_URL`-is-configured status as the ZAP job it sits alongside. Still `partial`, not `met`: the scan is wired up but hasn't actually run against a live staging deployment yet, so Caddy's default cipher configuration remains independently *unverified* rather than independently *confirmed*. |
| V9.1.3 | Only current TLS versions (1.2/1.3) enabled | partial | Same `testssl.sh` step and same caveat as V9.1.2 — wired up, not yet executed against a live target. |

#### V9.2 Server Communication Security

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V9.2.1 | Trusted TLS certs between servers, restrict self-signed | met | Caddy auto-obtains Let's Encrypt certificates (`Caddyfile.prod`); outbound calls to Stripe/Resend/OAuth providers use their publicly-trusted CA certs. |
| V9.2.2 | Encrypted connections for all inbound/outbound (incl. DB, mgmt) | met | Same enforcement as V1.9.1 — `Settings._enforce_startup_invariants` (T-225) requires `sslmode=require`+ on `DATABASE_URL` and `rediss://` on `REDIS_URL` outside dev. |
| V9.2.3 | Encrypted external connections carrying sensitive data are authenticated | met | `backend/app/core/config.py` — `STRIPE_SECRET_KEY`, `RESEND_API_KEY`, `GOOGLE_CLIENT_SECRET`/`GITHUB_CLIENT_SECRET` are required for their respective clients; every encrypted external call is also credential-authenticated. |
| V9.2.4 | Certificate revocation checking (OCSP stapling) configured | met | Caddy enables OCSP stapling by default for ACME-issued certs — no explicit config is needed to turn it on. T-225 confirmed this is Caddy's documented default behavior rather than adding a no-op config line. |

### V12 — Files and Resources

#### V12.1 File Upload

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V12.1.1 | Reject large files that could fill storage / cause DoS | met | Plan-quota check (`_check_quota`) + declared-vs-actual size cross-check at confirm time (`backend/app/modules/storage/service.py:135-152,167-173`). |
| V12.1.2 | Compressed files checked against max uncompressed size/file count before unzipping | n/a | The backend never decompresses uploaded files server-side (they're stored/virus-scanned as opaque byte streams) — if a future feature adds server-side unzip of e.g. `.docx`, this control must be added at that point. |
| V12.1.3 | File-size quota and max-files-per-user enforced | met | A storage-byte quota already existed (`_check_quota`); T-225 added a `max_files`-per-org check alongside it, backed by the already-present (previously unused) `StoredFileRepository.count_files_for_org()` (`backend/app/modules/storage/repository.py`). |

#### V12.2 File Integrity

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V12.2.1 | File type validated from actual content, not just declared type | met | `StorageService._verify_content_sniff` (`backend/app/modules/storage/service.py`, T-225) reads the first 2KB of a confirmed upload via a new `StorageBackend.read_prefix` and compares libmagic's (`python-magic`) detected MIME type against the declared one, deleting the object on mismatch. `text/plain`/`text/csv` stay declared-type-only — no reliable magic-byte signature exists for either. |

#### V12.3 File Execution

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V12.3.1 | Filename metadata not used directly for filesystem access | met | Storage key is server-generated (`uuid4()`); filename only feeds a slugified human-readable tail (`backend/app/modules/storage/service.py:63-69,105`). |
| V12.3.2 | Filename metadata validated/ignored to prevent LFI | met | `backend/app/modules/storage/service.py:63-69,105` — same mechanism as V12.3.1. |
| V12.3.3 | Filename metadata validated/ignored to prevent RFI/SSRF | met | `backend/app/modules/storage/service.py` — filename is never used to construct a fetchable URL. |
| V12.3.4 | Reflective File Download protection (fixed Content-Disposition, text/plain) | met | `_content_disposition` (`backend/app/modules/storage/backends/s3.py`, T-225) strips CR/LF and path separators from the filename before it reaches `ResponseContentDisposition`, and RFC-5987-encodes it (`filename*=UTF-8''...`) rather than interpolating it raw into the quoted-string form of the header. |
| V12.3.5 | Untrusted file metadata not passed to system APIs (OS command injection) | met | `backend/app/modules/storage/service.py` — no shell/subprocess call is built from a filename; enforced by the bandit CI gate. |
| V12.3.6 | No execution of untrusted third-party content | met | `backend/app/modules/storage/service.py` — uploaded files are stored as opaque objects, never executed or included as code by the backend. |

#### V12.4 File Storage

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V12.4.1 | Untrusted files stored outside the web root, limited permissions | met | `backend/app/modules/storage/backends/` (`s3.py`, `r2.py`, `minio.py`) — files live in object storage, not on the app's filesystem, inherently outside any served directory. |
| V12.4.2 | Untrusted files scanned by antivirus | met | `ClamAVScanner` is a real implementation (`backend/app/modules/storage/scanner.py:74-137`); `VIRUS_SCANNER=noop` still defaults in dev, but `Settings._enforce_startup_invariants` (`backend/app/core/config.py`, T-225) now refuses to boot outside dev with that setting, and the CI `deploy` job independently fails fast if the production env file would set it (`.github/workflows/ci.yml`). |

#### V12.5 File Download

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V12.5.1 | Web tier serves only specific extensions, blocks backup/temp files | met | `Caddyfile.prod` — no static file server exposes a directory tree; Caddy only proxies to the app. Object storage backends don't serve arbitrary filesystem paths either. |
| V12.5.2 | Direct requests to uploaded files never executed as HTML/JS | met | `backend/app/modules/storage/backends/` — files are served from a different origin (S3/R2/MinIO) than the app itself, via presigned URLs. |

#### V12.6 SSRF Protection

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V12.6.1 | Server configured with an allow-list for outbound resource requests | met | As V5.2.6 — the guard is called at save time and at delivery time by `backend/app/workers/jobs/webhooks.py`, which is the only place this product fetches a URL somebody else chose. |

### V13 — API and Web Service

#### V13.1 Generic Web Service Security

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V13.1.1 | Consistent encodings/parsers across components | met | `backend/app/main.py` — single FastAPI/Pydantic stack, one JSON parser, no mixed multi-parser surface. |
| V13.1.3 | API URLs don't expose sensitive info | met | Bearer token in `Authorization` header, refresh token in cookie — never in a URL (ties to V3.1.1). |
| V13.1.4 | Authorization checked at both the URI/controller level and the resource level | met | `require_permission` at the router/dependency level + explicit `org_id` re-check inside services (e.g. `backend/app/modules/tenancy/service.py:137,171`). |
| V13.1.5 | Unexpected/missing content types rejected with 406/415 | met | `SecurityHeadersMiddleware` (`backend/app/middleware/security_headers.py`, T-225) 415s any `POST`/`PUT`/`PATCH` request with a non-empty body whose `Content-Type` isn't `application/json` — every mutating endpoint in this API is JSON-only (uploads go client→storage-backend directly via a presigned POST, never through the backend). 406 doesn't apply: this API doesn't do `Accept`-based content negotiation. |

#### V13.2 RESTful Web Service

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V13.2.1 | Enabled HTTP methods valid for the caller's role | met | Mutating routes (`DELETE`/`PUT`) are gated by `require_permission`; a `MEMBER`-role user calling `DELETE` gets 403. |
| V13.2.2 | JSON schema validation before accepting input | met | `backend/app/core/schemas.py` — Pydantic validates before any handler logic runs (FastAPI request-body binding). |
| V13.2.3 | CSRF protection for cookie-based REST services | met | `SameSite=Strict` on the refresh cookie (ties to V4.2.2); mutating calls also require a custom `Authorization` header a simple cross-site form can't set. |
| V13.2.5 | Incoming `Content-Type` explicitly checked | met | Same check as V13.1.5. |
| V13.2.6 | Headers/payload integrity in transit | met | TLS-only outside `dev` (`FORCE_HTTPS`) provides transport-level integrity; ASVS explicitly calls this sufficient for most cases. |

#### V13.3 SOAP Web Service

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V13.3.1 | XSD schema validation for SOAP | n/a | Template has no SOAP web services (JSON/REST only). |
| V13.3.2 | SOAP payload signed via WS-Security | n/a | No SOAP. |

#### V13.4 GraphQL

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V13.4.1 | Query depth/cost limiting for GraphQL | n/a | Template has no GraphQL API (REST/JSON only). |
| V13.4.2 | GraphQL authorization enforced at the business-logic layer | n/a | No GraphQL. |

### V14 — Configuration

#### V14.1 Build and Deploy

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V14.1.1 | Build/deploy secure and repeatable (CI/CD) | met | Full GitHub Actions pipeline — lint, test, security, build, deploy jobs (`.github/workflows/ci.yml`). |
| V14.1.2 | Compiler flags for buffer-overflow protection | n/a | Python is interpreted/memory-managed; no compiler flags apply to application code. |
| V14.1.3 | Server configuration hardened per vendor recommendations | met | Non-root Docker user, minimal `python:3.12-slim` base image, hardened Caddy headers (`backend/Dockerfile:61-75`). |
| V14.1.4 | App + dependencies + config redeployable via automated scripts | met | `backend/Dockerfile` + `docker-compose.yml` + the CI `deploy` job (Render trigger). |
| V14.2.1 | All components kept up to date via a dependency checker | met | `pip-audit` + `pnpm audit` gate the `build` job in CI. |
| V14.2.2 | Unneeded features/docs/sample apps/configs removed | met | Production Docker image is a minimal runtime stage (multi-stage build strips builder tooling; only `nodejs` retained for email-template rendering). |
| V14.2.3 | CDN-hosted assets use Subresource Integrity | n/a | No third-party CDN-hosted assets — the frontend build is self-hosted/bundled. |
| V14.2.4 | Third-party components from trusted, maintained repositories | met | Dependencies sourced from the official PyPI/npm registries via `pyproject.toml`/`package.json`. |
| V14.2.5 | SBOM maintained for all third-party libraries | met | `.github/workflows/ci.yml`'s `build` job (T-225) generates a CycloneDX SBOM of the exact shipped image via `anchore/sbom-action` and uploads it as a workflow artifact on every build. |
| V14.2.6 | Attack surface reduced by encapsulating third-party libraries | met | `backend/app/core/module_loader.py` already encapsulates optional third-party integrations (Stripe, OAuth providers, storage backends) behind feature flags — disabled modules' third-party code never loads. |

#### V14.3 Unintended Security Disclosure

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V14.3.2 | Debug mode disabled in production | met | This was a real gap, not just an unconfirmed one: FastAPI's interactive `/docs`/`/redoc`/`/openapi.json` were live in every environment including `prod`. `backend/app/main.py` (T-225) now passes `docs_url`/`redoc_url`/`openapi_url` as `None` unless `ENVIRONMENT == "dev"`. |
| V14.3.3 | HTTP headers don't expose detailed version info | met | `Caddyfile.prod`'s `header` block (T-225) now includes `-Server`, stripping Uvicorn's default `Server: uvicorn` response header at the edge. |

#### V14.4 HTTP Security Headers

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V14.4.1 | Every response has a `Content-Type` with a safe charset | met | FastAPI sets `application/json; charset=utf-8` by default. |
| V14.4.2 | API responses carry a `Content-Disposition: attachment` header | met | `SecurityHeadersMiddleware` (`backend/app/middleware/security_headers.py`, T-225) sets `Content-Disposition: attachment` (via `setdefault`) on every `/api/*` response. Scoped to the API prefix, not every response — `/docs`/`/redoc` (dev only) are meant to render as HTML. |
| V14.4.3 | Content-Security-Policy header present | met | `default-src 'self'` (`backend/app/middleware/security_headers.py:13`). |
| V14.4.4 | `X-Content-Type-Options: nosniff` on all responses | met | `backend/app/middleware/security_headers.py:10`. |
| V14.4.5 | `Strict-Transport-Security` on all responses/subdomains | met | Set at both the app layer (`backend/app/middleware/security_headers.py:8`) and the edge (`Caddyfile.prod`, with `preload`). |
| V14.4.6 | Suitable `Referrer-Policy` header | met | `strict-origin-when-cross-origin` (`backend/app/middleware/security_headers.py:11`). |
| V14.4.7 | Content can't be framed by third-party sites by default | met | `X-Frame-Options: DENY` (`backend/app/middleware/security_headers.py:9`). |

#### V14.5 HTTP Request Header Validation

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| V14.5.1 | Only in-use HTTP methods accepted, invalid attempts logged | met | FastAPI already 405s undeclared methods per route by default; `http_exception_handler` (`backend/app/core/handlers.py`, T-225) now logs a warning (method/path/request-id) on every 405 before falling through to the default response. |
| V14.5.2 | `Origin` header not used for authentication/access-control decisions | met | Authorization decisions are based on the bearer token/session, never the `Origin` header (`backend/app/core/dependencies.py`). |
| V14.5.3 | CORS allow-list is strict, no `null` origin support | met | `ALLOWED_ORIGINS` explicit list + wildcard-forbidden startup invariant (`backend/app/core/config.py:88-89`); no special-casing of a `null` origin found. |
| V14.5.4 | Headers from a trusted proxy/SSO are authenticated by the app | n/a | Template doesn't rely on proxy-injected identity headers (e.g. `X-Forwarded-User`) — all authentication is via app-issued JWT/session. |

## Consequences

- **What this buys us**: an evidence-linked answer to "are you ASVS L2
  compliant" for an enterprise security questionnaire, and a concrete scope
  document for [T-214](../../agents.md)'s pentest ("here's our own claimed
  baseline — try to break it").
- **What it costs**: this document goes stale the moment code changes
  without an update. The CI link-check (`backend/tests/security/test_security_baseline.py`)
  only verifies evidence *paths* still exist — it cannot verify the
  *claims* are still true. Status changes must be made in the same PR as
  the code change that causes them.
- **Honest gaps, not zero gaps**: this started at roughly a third of the
  applicable rows sitting `partial`, the point of doing this exercise for
  real instead of writing a table that says "met" everywhere — several
  concrete, previously-undocumented gaps surfaced while building this (no
  authenticated change-password endpoint, TOTP replay-within-window not
  tracked, `url_guard.py` has no caller yet, no SBOM, superadmin doesn't
  require 2FA). Those became the follow-up tasks below rather than being
  quietly noted and dropped. T-225 closed most of its batch outright; the
  rows still `partial` after it are the ones that turned out to need a real
  infrastructure/vendor decision (a specific vault/KMS, an executed backup
  restore, a completed `testssl.sh` run against a live staging deployment)
  rather than more application code — deferred with a named reason each,
  not silently left as before.

## Alternatives considered

- **ASVS Level 1.** The bar for applications with no sensitive data. This one
  holds multi-tenant customer records, credentials, and payment references —
  L1 would let a real gap pass as compliant, which is worse than no baseline.
- **ASVS Level 3.** Written for high-assurance and life-critical systems. Its
  additional requirements are largely HSM-backed key storage, full transaction
  logging, and segregation controls that a template cannot make true for every
  deployer; claiming L3 and sitting `partial` on half of it says less than
  claiming L2 and meeting it.
- **SOC 2 / ISO 27001 as the framework instead.** Both are organizational
  audits — they certify that a company has processes, not that a codebase has
  controls. Neither maps to a file path, which is what makes the evidence
  column of this document checkable at all. They are complementary to this,
  not a substitute for it, and a deployer pursuing either will find this
  document is the technical half of the evidence they need.
- **The OWASP Top 10 alone** (`docs/security/owasp-top10.md`, T-212). Already
  in place, and deliberately kept — but ten risk categories can't answer an
  enterprise security questionnaire line by line, which is the gap this
  document exists to close. See the Context section.

## Follow-up tasks filed by this document

| ID | Scope | Rows it closes |
|---|---|---|
| T-215 | Written secure-SDLC docs: threat-modeling process, trust-boundary/data-flow diagram, secure-coding checklist, key-management policy, IaC deploy-verification CI step, remote log shipping to a SIEM. | V1.1.1, V1.1.2, V1.1.4, V1.1.5, V1.1.7, V1.5.1, V1.6.1, V1.7.2, V1.14.4 |
| T-216 | Auth/session UX gaps: authenticated change-password endpoint (current+new password), password `max_length=128`, reconsider the upper/lower/digit composition rule against NIST SP 800-63B, frontend password-strength meter and reveal toggle, security-notification email on credential change, session-list UI, OAuth-unlink self-service, `__Host-` cookie prefix, confirm frontend access-token storage avoids `localStorage`. | V2.1.2, V2.1.5, V2.1.6, V2.1.8, V2.1.9, V2.1.12, V2.2.3, V2.5.5, V3.2.3, V3.3.4, V3.4.4, V3.5.1, V8.2.2, V8.2.3 |
| T-217 | WebAuthn/FIDO2 phishing-resistant second factor (current 2FA is TOTP-only). | V2.3.2, V2.9.1, V2.9.2, V2.9.3 |
| T-218 | Mandatory 2FA enrollment for superadmin accounts; step-up re-auth before high-value actions; idle/periodic session re-authentication; TOTP same-window replay tracking; admin-assisted lost-2FA recovery path. | V2.5.7, V2.8.4, V2.8.5, V3.3.2, V3.7.1, V4.3.1, V4.3.3 |
| T-219 | Data-classification pass across all modules; expand column-level encryption to regulated PII beyond the TOTP secret; formalize a per-data-type retention schedule; extend log scrubbing to known PII field names; log permission-denied and failed-login events as discrete security events. | V1.8.1, V1.8.2, V6.1.1, V7.1.2, V7.1.3, V7.2.1, V7.2.2, V8.3.4, V8.3.8 |
| T-225 | Build/infra hardening batch: zip-bomb-style decompression guard if server-side unzip is ever added, content-sniffing file-type validation, SBOM generation in CI, confirm `/docs` disabled outside dev, confirm the `Server` header is stripped/replaced at the edge, DB/Redis TLS (`sslmode`) confirmation, OCSP stapling confirmation, TLS cipher/version verification tooling in CI, documented+tested backup/restore runbook, secrets moved to a dedicated vault/KMS, `Content-Disposition`/`406`/`415` handling on the API, request-anomaly alerting, file-count quota, Reflective-File-Download header handling, fail prod deploys where `VIRUS_SCANNER=noop`. | V1.6.2, V1.9.1, V1.14.4 (shared with T-215), V2.4.5, V2.10.1, V2.10.3, V6.4.1, V6.4.2, V8.1.4, V8.1.5, V8.1.6, V9.1.2, V9.1.3, V9.2.2, V9.2.4, V12.1.3, V12.2.1, V12.3.4, V12.4.2, V13.1.5, V13.2.5, V14.2.5, V14.3.2, V14.3.3, V14.4.2, V14.5.1 |

## References

- [OWASP ASVS 4.0.3](https://github.com/OWASP/ASVS)
- [T-212 OWASP Top 10 mapping](../security/owasp-top10.md)
- [T-214 Pentest readiness runbook](../../agents.md)
