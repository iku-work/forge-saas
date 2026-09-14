# Credential rotation runbook

Replacing any secret this application holds: which ones are a straight swap,
which need a specific order, and which cannot currently be rotated at all
without user-visible damage. The policy behind holding them in environment
variables at all — rather than a vault — is ADR-0001's V1.6.2/V6.4.1 rows,
which record that deferral as deliberate.

`ENCRYPTION_KEY` has its own runbook, `encryption-key-rotation.md`, because it
is the one credential with real rotation machinery: a second key, a batched
resumable re-encryption job, and a verification pass. Everything else is
here.

## Trigger

Rotate on an **event**, not on a calendar:

- someone with access leaves, or changes role
- a credential was pasted somewhere it should not have been — a ticket, a
  chat, a screenshot, a log
- a provider reports a breach, or rotates on your behalf
- a laptop holding `.env` is lost
- an audit requires it

Deliberately **not** a fixed schedule for machine credentials. Calendar
rotation of a service key mostly buys churn and outage risk; NIST dropped the
equivalent advice for passwords years ago for the same reason. The two
exceptions worth scheduling are `ENCRYPTION_KEY` annually — because the
machinery exists and rotating it is how you find out it still works — and
anything a human has ever seen in plaintext.

Not this runbook: a *user's* API key (`api-keys.md`), a compromised user
account (`account-recovery.md`), or `ENCRYPTION_KEY`
(`encryption-key-rotation.md`).

## Pre-checks

- **Know how the value reaches production.** Every secret here is one line
  of `PROD_ENV_FILE`, a single GitHub secret holding the whole
  `backend/.env`. Rotation is therefore: change it at the provider, update
  that secret, redeploy. There is no SSH step and no file to hand-edit —
  hand-editing on the box is how `CADDY_TRUSTED_PROXIES` came to be missing
  for a week.
- **Keep the old credential alive until the new one is serving.** Every
  procedure below is add-then-remove, never remove-then-add. The revoke is
  the last step, after verification.
- **Check `Settings` will accept the file.** It validates every required
  field at import, so a truncated paste fails the deploy's migration step
  rather than the first request — but only if you let the deploy run.
- **Deploys are the rotation mechanism**, so confirm CI can deploy before you
  start. See `deploy-pipeline.md`.

## Steps

### 1. Classify what you are rotating

Read this table first. The third column is the one that matters — it is what
breaks if you skip the ordering.

| Credential | Rotation | What depends on it |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | straight swap | nothing stored |
| `RESEND_API_KEY` | straight swap | nothing stored |
| `SENTRY_DSN` | straight swap | nothing stored |
| `METRICS_TOKEN` | straight swap | whatever scrapes `/metrics` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | swap, then re-issue links | presigned URLs already handed out |
| `GOOGLE_CLIENT_SECRET` / `GITHUB_CLIENT_SECRET` | swap | in-flight OAuth redirects only |
| `STRIPE_WEBHOOK_SECRET` | overlap at Stripe | signature verification, see step 4 |
| `DATABASE_URL` password | ordered, see step 5 | pgbouncer's SCRAM userlist |
| `REDIS_URL` password | ordered | every process holding a pool |
| `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` | **breaking**, see step 6 | access tokens **and every emailed link** |
| `PASSWORD_PEPPER` | **cannot be rotated**, see step 7 | every stored password |
| `ENCRYPTION_KEY` | supported — `encryption-key-rotation.md` | encrypted columns |
| `SSH_KEY` (deploy) | swap | the box's `authorized_keys` |

### 2. Rotate a straight-swap credential

```bash
# 1. Create the new credential at the provider. Do not revoke the old one.
# 2. Edit your local copy of the env file, then:
gh secret set PROD_ENV_FILE < prod.env
# 3. Redeploy the currently-live commit -- no code change is involved.
gh workflow run deploy-hetzner.yml -f image-tag=<current sha>
```

**Expected:** a green run, ending in the smoke test. Then verify the new
credential is actually the one in use (step "Verification" below) *before*
revoking the old one at the provider.

### 3. Rotate the object-storage keys

Same as step 2, with one extra consequence: **presigned URLs already handed
to browsers are signed with the old key and stop working the moment it is
revoked.** Three different lifetimes are in play, so the wait is set by the
longest:

| Link | Constant | Lifetime |
| --- | --- | --- |
| Avatar | `AVATAR_URL_EXPIRES_IN_SECONDS` | 5 minutes |
| Upload (POST policy) | `presign_upload`'s `expires_in` default | 10 minutes |
| File download | `_DOWNLOAD_EXPIRES_IN_SECONDS` | **1 hour** |

So leave the old key valid for **an hour** after deploying the new one, or
accept that downloads handed out in the previous hour break. An in-flight
*upload* is the worse failure — the browser POSTs to a policy signed with a
revoked key, the object never lands, and the `stored_file` row stays
`pending` — so avoid rotating these during a busy upload window.

### 4. Rotate the Stripe webhook secret

The backend verifies against exactly one `STRIPE_WEBHOOK_SECRET`, so there is
a window where one of the two secrets is wrong regardless of ordering.

1. In Stripe, roll the endpoint's signing secret. Stripe keeps the previous
   one valid for a grace period — read the expiry it shows you.
2. Update `PROD_ENV_FILE` and redeploy **within that period**.

**Expected:** `select count(*) from webhook_event where received_at > now() -
interval '10 minutes'` keeps increasing, and none of those rows has an
`error`.

Events rejected during the window are not lost: Stripe retries, and the
hourly reconciliation sweep repairs anything that never landed. Confirm with
`billing-drift.md` if in doubt.

### 5. Rotate the database password

Order matters, and getting it wrong drops every connection.

```sql
-- 1. On the database, add the new password (this replaces it atomically;
--    existing connections keep working, new ones need the new password).
ALTER ROLE <user> WITH PASSWORD '<new>';
```

2. Regenerate pgbouncer's userlist so it can still authenticate. pgbouncer
   uses SCRAM here (`AUTH_TYPE=scram-sha-256`), so the userlist holds a
   SCRAM verifier, not the plaintext — copy it from
   `SELECT rolname, rolpassword FROM pg_authid WHERE rolname = '<user>'`.
3. Update `DATABASE_URL` (and `MIGRATIONS_DATABASE_URL`, and
   `READ_REPLICA_URL` if set — they are separate values and all three must
   change together) in `PROD_ENV_FILE`.
4. Redeploy.

**Expected:** `/health/ready` passes, and `docker compose logs pgbouncer`
shows no `password authentication failed`.

**There is a gap between steps 1 and 4** during which the running app holds
pooled connections opened with the old password. Those keep working;
anything that opens a *new* connection fails. Do this in a quiet window, and
have the previous password to hand.

### 6. Rotate the JWT keypair

This is more breaking than it looks, and the reason is not obvious from the
name. `JWT_PRIVATE_KEY` signs:

- every access token (15 minutes, so self-healing), **and**
- every emailed and redirect-carried link, because
  `app.modules.auth.utils._url_token_serializer` reuses it as the
  `itsdangerous` secret — email verification, password reset, magic links,
  email-change confirm/cancel, the OAuth `state` parameter, and the 2FA
  challenge token

So rotating it invalidates **every outstanding link a user has been emailed**,
not just their session. Someone mid password-reset gets an invalid-token
page.

There is no `JWT_PUBLIC_KEY_OLD` and no `kid` header, so no overlap is
possible today. Until there is:

1. Announce it, or do it when link volume is lowest.
2. Rotate and redeploy.
3. Expect a burst of `/auth/refresh` as every access token is rejected at
   once. Sessions survive — refresh tokens are stored as plain SHA-256 in
   the `session` table and do not involve this key.

**Expected:** a spike in refresh calls, then normal traffic. Users are not
signed out; users holding an emailed link are.

### 7. `PASSWORD_PEPPER` — read before touching

**It cannot be rotated.** `hash_password` computes
`argon2(sha256(pepper + ":" + plaintext))`, and `verify_password` recomputes
the same thing. There is no second pepper to fall back to and no version
marker on the stored hash, so changing this value makes **every stored
password fail to verify** — with no way to tell a wrong password from a
wrong pepper.

If the pepper leaks, the options are:

- **Leave it.** The pepper is defence in depth *on top of* Argon2's own
  per-hash salt. A leaked pepper alone does not make hashes crackable; it
  removes one layer, and the remaining one is the one doing the work.
- **Force a reset for everyone.** Change the pepper, then treat every
  password as unusable: expire all sessions and require password reset by
  email. Only justified if the password hashes leaked too.

Do not change it "to be safe". The failure mode is total and immediate.

The proper fix is a versioned pepper — store which pepper hashed each row and
rehash on next successful login. That is not built; it is the top item in
this repo's rotation backlog and the reason this section exists rather than a
procedure.

### 8. Rotate the deploy SSH key

1. Generate a new keypair.
2. Append the new public key to the box's `~/.ssh/authorized_keys` — append,
   do not replace, or a failed deploy locks you out of the fix.
3. Update the `SSH_KEY` secret; redeploy.
4. Only once a deploy has succeeded, remove the old public key.

## Verification

Prove the new credential is in use, not merely configured. Configuration is
what you just changed; use is what you have not yet observed.

```bash
# The deploy shipped what you think it did
curl -sf https://<domain>/health/ready
```

Then, per credential:

- **Stripe:** trigger a test webhook from the dashboard and confirm a new
  `webhook_event` row with `processed_at` set and no `error`.
- **Object storage:** upload a file through the app and load its avatar or
  download link — a presigned URL signed with the new key.
- **Database / Redis:** restart one process and watch it come back healthy.
  Pooled connections hide a wrong password until something reconnects, which
  is exactly how this bites later rather than now.
- **Resend:** trigger a password reset to an address you control.
- **JWT:** sign in, then confirm an emailed link (a fresh password reset)
  works.

The negative check — the one that still looks fine if the rotation silently
did nothing: **revoke the old credential and confirm the app keeps working.**
Until you do that, you have proven the new credential exists, not that it is
the one being used.

## Rollback

Up to and including the deploy, rollback is: put the old value back in
`PROD_ENV_FILE` and redeploy. The old credential is still valid, because
nothing above revokes it until verification passes.

**Revocation is the point of no return.** After you revoke at the provider,
there is no putting it back — you can only issue another new credential and
repeat. So revoke last, and only after the Verification section, not after
the deploy goes green.

Two exceptions where even the deploy is not reversible:

- **`PASSWORD_PEPPER`**, as above. Restoring the old value restores the
  passwords, so keep it until you are certain — but any password *set* while
  the new pepper was live is then broken instead.
- **`ENCRYPTION_KEY` once its rotation job has run.** Rows re-encrypted with
  the new key need it. See that runbook.

## Escalation

Stop and escalate when:

- **`/health/ready` fails after a credential deploy.** Roll back the env
  first (the old credential still works), then diagnose. Do not start
  revoking things to "clean up".
- **Authentication fails for everyone at once.** Suspect `PASSWORD_PEPPER` or
  the database password, in that order. Both are recoverable by restoring
  the previous value — provided you still have it, which is the argument for
  keeping the outgoing `.env` until verification passes.
- **You revoked before verifying and the app is down.** Issue a fresh
  credential immediately rather than trying to un-revoke; most providers
  cannot un-revoke, and the time spent asking is downtime.
- **A credential leaked publicly** (a commit, an issue, a screenshot). Revoke
  *first* and accept the outage — this is the one case where the ordering in
  this runbook is wrong. Then rotate properly. Check
  `docs/runbooks/pentest-prep.md` for the disclosure expectations.
