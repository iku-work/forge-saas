# Data classification

T-219. One row per PII-bearing (or otherwise security-relevant) column
across every module, closing ADR-0001's V1.8.1/V1.8.2/V6.1.1/V8.3.4/V8.3.8
gap ("only the TOTP secret is classified/encrypted"). See
[`docs/security/sdlc.md`](sdlc.md) §1 for when a new column needs a row
added here.

## 1. Classification levels

- **public** — safe to expose to anyone, including unauthenticated callers
  (e.g. a plan's marketing name).
- **internal** — not secret, but not meant for cross-tenant or public
  exposure; ordinary RBAC/tenant-isolation scoping is the control.
- **sensitive** — personal data (GDPR Article 4 "personal data": any
  information relating to an identified or identifiable natural person,
  which includes IP addresses and free-text names, not just obvious
  identifiers) or a credential/secret. Gets the strongest available
  control: encryption at rest for direct identifiers and secrets, scrubbing
  from logs, and either a retention/expiry schedule or a documented reason
  it's kept indefinitely.

"Current protection" cites the actual mechanism (encryption, hashing,
scrubbing) and file, not just the classification — see [T-215's key
inventory](key-management.md) for the encryption keys themselves.

### The product's level: `DATA_SENSITIVITY`

The levels above are per column. Separately, every deployment declares what
the product as a whole holds (`DATA_SENSITIVITY`,
[ADR-0009](../adr/0009-data-sensitivity.md)), and production refuses to start
without it:

- **`standard`** — the columns below and their like: accounts, names, emails,
  files users choose to upload. Sensitive *columns* in the sense above, in an
  ordinary product. The template itself is `standard`.
- **`high`** — the product's own data includes GDPR Article 9
  special-category data (health, genetic or biometric data, racial or ethnic
  origin, political opinions, religious beliefs, trade-union membership, sex
  life or orientation), financial account data, data about children, or
  secrets its customers entrust to it. `high` turns off impersonation and
  requires a backup key that cannot list or delete backups.

When unsure, it is `high`: the cost of the stricter level is one more account
and no impersonation, the cost of the wrong one is a breach report.

## 2. `app.modules.auth`

| Column | Classification | Current protection | Retention |
|---|---|---|---|
| `User.email` | sensitive | Plaintext. Deliberately **not** wrapped in `EncryptedString`: it's the unique, indexed login-lookup key, and `backend/app/core/encrypted_type.py`'s own docstring rules out filtering/equality lookups on an encrypted column (ciphertext differs per write). Accepted, reasoned scope decision, not a gap — the alternative (a separate SHA-256 lookup-hash column alongside an encrypted `email`) is real but out of scope for this pass; tracked as a documented gap here, not a silent one. | Soft-deleted immediately on `DELETE /me`, hard-purged after `GDPR_PURGE_GRACE_DAYS` (`backend/app/workers/jobs/gdpr.py`, `purge_user_data`). |
| `User.full_name` | sensitive | **Encrypted** via `EncryptedString` (T-219 — `backend/app/modules/auth/models.py`); redacted in `__repr__` via `RedactedReprMixin`. | Same as `email` (GDPR purge). |
| `User.avatar_url` | internal | Plaintext (a storage URL, not the image itself). | Same as `email` (GDPR purge). |
| `User.hashed_password` | sensitive (credential) | Argon2id hash, never plaintext (`backend/app/modules/auth/utils.py`). | Same as `email` (GDPR purge). |
| `User.failed_login_count`, `locked_until` | internal (security state) | Plaintext; not itself PII beyond being tied to the account. | Same as `email` (GDPR purge). |
| `User.notification_preferences` | internal | Plaintext JSONB. | Same as `email` (GDPR purge). |
| `Session.ip_address`, `user_agent` | sensitive (IP is personal data under GDPR) | Plaintext. | `cleanup_expired_sessions` (every 6h, `backend/app/workers/scheduler.py`) hard-deletes rows past `expires_at`. |
| `Session.refresh_token_hash` | sensitive (credential) | SHA-256 hex of the token; the plain token is never stored. | Same as above. |
| `TOTPSecret.secret_encrypted` | sensitive (credential secret) | **Encrypted** via `EncryptedString` (pre-existing, the one column T-219 started from). | Deleted with the user (GDPR purge cascade, `backend/app/workers/jobs/gdpr.py`, `_DELETE_BY_USER`). |
| `TOTPSecret.backup_codes_hash` | sensitive (credential) | Argon2-hashed codes, JSON-encoded. | Same as above. |
| `WebAuthnCredential.public_key` | internal | Plaintext by design — a public key, not a secret (see the model's own docstring). | Same as above. |
| `EmailVerificationToken`/`PasswordResetToken`/`MagicLinkToken`/`EmailChangeToken`.`token_hash` | sensitive (credential) | SHA-256 hex; plain token never stored. | `cleanup_expired_tokens` (hourly) hard-deletes rows past `expires_at`. |
| `EmailChangeToken.new_email` | sensitive | Plaintext. | Same as above (token TTL is ≤1h, so exposure window is short). |

## 3. `app.modules.tenancy`

| Column | Classification | Current protection | Retention |
|---|---|---|---|
| `Invitation.email` | sensitive | Plaintext — the invitee usually isn't a `User` row yet, so it can't ride the GDPR purge path. | **Gap, not covered by this pass**: no expiry-based deletion exists once `expires_at` has passed (unlike the auth-module tokens above, `InvitationRepository` has no `delete_expired`). Documented here as an accepted follow-up rather than silently missing; a future task should add the same `delete_expired` pattern `_TokenRepository` already has. |
| `Organization.stripe_customer_id` | internal | Plaintext — a Stripe-side reference, not a secret. | Tied to org lifetime. |
| `Membership`, `OrgFeatureFlag` | internal | No direct PII (FKs + role/flag state only). | Tied to org/membership lifetime. |

## 4. `app.modules.gdpr`

| Column | Classification | Current protection | Retention |
|---|---|---|---|
| `ConsentRecord.ip_address`, `user_agent` | sensitive | Plaintext. Append-only by design (§0.3 — consent history must not be editable), so age-based pruning would defeat its own purpose (it's the proof of what was agreed and when). | Deleted only via the GDPR purge cascade when the user themself is deleted; otherwise kept for the life of the account by design. |
| `DataExportJob.download_url` | sensitive (bearer capability — grants the full data export) | Plaintext while live. | `expire_data_export_urls` (hourly, `backend/app/workers/scheduler.py`) clears `download_url` to `NULL` once `expires_at` has passed — already met, not a T-219 change. |

## 5. `app.modules.notifications`

| Column | Classification | Current protection | Retention |
|---|---|---|---|
| `Notification.title`, `body` | internal/sensitive (may reference another user's name or email, e.g. a `member_invited` notification) | Plaintext. | **New in T-219**: `prune_old_notifications` (daily at 02:00 UTC, `backend/app/workers/scheduler.py`) hard-deletes rows older than `NOTIFICATION_RETENTION_DAYS` (default 90) via `NotificationRepository.prune_older_than`. This is the retention job the model's own docstring already promised ("notifications are pruned by a hard-delete retention job") but that didn't exist until this pass — the highest-value gap found once `email`/`full_name` and the security-event logging gaps (§7 below) were addressed. |
| `PushToken.token` | sensitive (device push credential) | Plaintext. Deactivated (`deleted_at` set) on unregister, but not purged. | **Gap, not covered by this pass**: no age-based hard-delete for deactivated tokens. Lower priority than `Notification` — one row per device, not one per event, so the accumulation rate is far smaller. |

## 6. `app.modules.storage`

| Column | Classification | Current protection | Retention |
|---|---|---|---|
| `StoredFile.filename` | internal/sensitive (user-chosen, may contain a name or other PII) | Plaintext. | Soft-deleted (`status="deleted"`); the underlying object-storage delete is handled by the storage module's own lifecycle, out of scope here. |
| `StoredFile.checksum_sha256`, `content_type`, `size_bytes` | internal | Not personal data. | Tied to file lifetime. |

## 7. `app.modules.billing`

| Column | Classification | Current protection | Retention |
|---|---|---|---|
| `WebhookEvent.payload` | sensitive (raw Stripe webhook body — may contain customer name/email/payment metadata) | Plaintext JSONB. | **Gap, not covered by this pass**: kept indefinitely for replay/audit purposes, no retention schedule. Common practice for webhook audit logs, but not yet a *documented* decision before this doc — now it is. |
| `Plan`, `Subscription`, `DunningCampaign` | internal | Business/billing state, not personal data beyond the `org_id`/Stripe reference IDs they carry. | Tied to org/subscription lifetime. |

## 8. `app.modules.audit`

| Column | Classification | Current protection | Retention |
|---|---|---|---|
| `AuditEvent.ip_address`, `user_agent`, `diff` | sensitive | Plaintext. Append-only and immutable by design (§0.3 — `AuditRepository` deliberately has no update/delete method, since this table is itself the evidence trail security incidents and compliance questions get answered from). | **Explicit accepted decision, not a gap**: no automatic age-based deletion. Pruning the audit trail on a timer would work against the log-security-events requirements this same task closes (§V7.1.3/V7.2.1/V7.2.2 below) — a shorter-lived audit trail is strictly worse for incident response. A future task could add a *long* (multi-year) compliance-driven retention window if storage cost becomes a real constraint; that is deliberately not this task. |

## 9. Security-event logging (V7.1.2, V7.1.3, V7.2.1, V7.2.2)

Two gaps closed by this pass, both in `backend/app/core/logging.py` and the
two call sites named below:

- **Value-aware-by-name scrubbing**: `_scrub()` previously only matched
  secret-*shaped* key names (`password`, `token`, `secret`, …) by substring.
  It now also redacts `email` and `full_name` by exact key name — so a log
  line built from `logger.bind(email=..., full_name=...)` at any call site,
  not just ones a developer remembered to name `*_token`, gets scrubbed
  automatically.
- **Missing security events**: `permission.denied` (raised in
  `require_permission`, `backend/app/core/dependencies.py`) and
  `user.login_failed`/`user.account_locked` (in `AuthService.login`,
  `backend/app/modules/auth/service.py`) now write an `AuditEvent` the same
  way `user.login_success` and `admin.access` already did.

## 10. Follow-ups this pass explicitly did not close

Named above at their respective rows, collected here for visibility:

1. `Invitation.email` has no expiry-based deletion (§3).
2. `PushToken.token` has no age-based purge for deactivated tokens (§5).
3. `WebhookEvent.payload` has no retention schedule (§7).
4. `User.email` stays plaintext for lookup reasons; a hash-column-based
   design that would allow encrypting it too is future work (§2).

None of these block the "done when" criteria for T-219 (every PII-bearing
column has a row here with a stated classification and reasoning), but
they're the concrete backlog the next data-handling pass should start from.
