# Key management

T-215. Lifecycle for this template's three key materials —
`ENCRYPTION_KEY`, `JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY` — covering
generation, storage, rotation, and who holds them. Closes ADR-0001's
V1.6.1 gap (a written key-lifecycle policy; V1.6.2/V6.4.1/V6.4.2's
vault-vs-environment-variable gap is tracked separately as T-225).

## 1. Inventory

| Key | Purpose | Settings field(s) | Algorithm |
|---|---|---|---|
| Fernet encryption key | Encrypts sensitive DB columns at rest (`TOTPSecret.secret_encrypted`, `User.full_name` — see [`docs/security/data-classification.md`](data-classification.md) for the rest of the inventory) | `ENCRYPTION_KEY`, `ENCRYPTION_KEY_OLD` | Fernet (AES-128-CBC + HMAC-SHA256), via `backend/app/core/encrypted_type.py` |
| JWT keypair | Signs/verifies access tokens and `itsdangerous` URL tokens (magic-link, password reset) | `JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY` | RS256, via `backend/app/modules/auth/utils.py` |

Refresh tokens are **not** on this list — they're opaque, CSPRNG-generated
secrets (`secrets.token_urlsafe(32)`) whose hash is stored in Postgres, not
key-derived material. Rotating the JWT keypair has no effect on them (§3).

## 2. Generation

Both keys are generated with standard, vetted tooling — no custom key
derivation:

```bash
# Fernet key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# RS256 keypair (JWT_PRIVATE_KEY / JWT_PUBLIC_KEY)
openssl genrsa -out private.pem 2048
openssl rsa -in private.pem -pubout -out public.pem
```

`backend/.env.test`'s keys are structurally-valid dummy fixtures, excluded
from the trufflehog/gitleaks CI scans by name
(`.trufflehog-exclude.txt`, `.gitleaks.toml`) — they are never deployed
anywhere real. Every other environment gets its own freshly generated
keys; `Settings` (`backend/app/core/config.py`) requires both fields with
no default, so the app refuses to boot without them.

## 3. Storage and holders

- Keys are loaded from environment variables through
  `backend/app/core/config.py`'s `Settings` — never read from `os.environ`
  directly elsewhere in the codebase (enforced by the semgrep
  `no-os-environ` custom rule).
- In staging/prod they live in the hosting platform's secret store (see
  [`docs/runbooks/deploy-render.md`](../runbooks/deploy-render.md)), not
  in a dedicated vault/KMS — that gap is T-225's, not this document's.
- Holders: whoever has access to the hosting platform's secret-store UI/
  API. This template doesn't prescribe a specific access-control model
  for that platform-level access — it's the deploying team's own IAM
  policy.

## 4. Rotation

### Fernet encryption key

Supports zero-downtime rotation because `MultiFernet` can decrypt with
any of several keys while only ever encrypting with the first
(`backend/app/core/encrypted_type.py`):

1. Generate a new key (§2).
2. Set `ENCRYPTION_KEY=<new>`, `ENCRYPTION_KEY_OLD=<current>`. Deploy.
   New writes use the new key; existing rows still decrypt via the old
   one — no read/write interruption.
3. Run the re-encryption job (`app.workers.jobs.encryption`,
   `rotate_encryption_keys_job`) to migrate existing rows onto the new
   key. It discovers every `EncryptedString` column itself, checkpoints
   into a `JobRecord` so a killed worker resumes where it stopped, and
   supports a dry run that only counts the work.
4. Once every row is confirmed migrated, remove `ENCRYPTION_KEY_OLD` and
   deploy again. Skipping this step leaves the old key valid indefinitely
   as a decryption fallback, which defeats the point of rotating.

The step-by-step operational procedure — commands, what each step should
print, how to recover from a mid-run crash, and the rollback at every
step — lives in
[`docs/runbooks/encryption-key-rotation.md`](../runbooks/encryption-key-rotation.md).

`ENCRYPTION_KEY_OLD` accepts a comma-separated list, so more than one
prior key can be in flight during a multi-step migration if needed
(`_load_fernet` in `backend/app/core/encrypted_type.py`).

### JWT keypair

No equivalent multi-key mechanism exists — `JWT_PRIVATE_KEY`/
`JWT_PUBLIC_KEY` are a single active pair. This is deliberately lower-
ceremony than Fernet rotation because of what these keys actually sign:

- **Access tokens** expire in 15 minutes. Rotating the keypair means any
  access token issued under the old key stops verifying immediately, but
  the client's next refresh call (which doesn't depend on the JWT keypair
  at all — refresh tokens are opaque, §1) transparently mints a new
  access token under the new key. Worst case is one failed request
  retried after a refresh, not a mass logout.
- **`itsdangerous` URL tokens** (magic-link, password-reset — reuses
  `JWT_PRIVATE_KEY` as the signing secret, `backend/app/modules/auth/
  utils.py:113-117`) are already short-lived (15 min–1 h) and single-use.
  A key rotation mid-flight invalidates any outstanding unused link,
  which is an acceptable, rare edge case — the user requests a new one.

Rotation procedure: generate a new keypair (§2), set both env vars,
deploy. No coordination window or dual-key step is needed.

### Backup codes / TOTP secrets

Not independently keyed — TOTP secrets are protected by the Fernet
encryption key above, so they rotate along with it. There is no separate
rotation schedule for individual users' TOTP secrets; a user who suspects
theirs is compromised disables and re-enrolls 2FA.

## 5. Compromise response

If a key is suspected compromised (leaked in a log, committed to source,
exposed via a misconfigured secret store):

1. Generate a replacement immediately (§2) and deploy following the
   rotation procedure above — do not wait for confirmation of actual
   misuse.
2. For the Fernet key: treat the old key as permanently untrustworthy for
   *decryption trust*, not just replace-for-new-writes — anyone who held
   it could have decrypted every row it ever protected while it was
   valid. This is a disclosure event for whatever data was encrypted
   under it, independent of the technical rotation.
3. For the JWT keypair: the 15-minute access-token TTL bounds the blast
   radius automatically once the new key is deployed.
4. Audit trail: check `admin-audit.log` and the audit-event table
   (`backend/app/modules/audit/`) for the window the key may have been
   exposed.
