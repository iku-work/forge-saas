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
| `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` | overlap via `JWT_PUBLIC_KEY_OLD`, see step 6 | access tokens; **also emailed links until `URL_TOKEN_SECRET` is set** |
| `URL_TOKEN_SECRET` | overlap via `URL_TOKEN_SECRET_OLD`, see step 6b | every emailed and redirect-carried link |
| `PASSWORD_PEPPER` | supported since 2026-09-14, see step 7 | every stored password |
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

Access tokens are verified against the current public key first and
`JWT_PUBLIC_KEY_OLD` second, so a rotation does not reject every token
minted in the preceding 15 minutes at once.

1. Generate a new RSA keypair.
2. In `PROD_ENV_FILE`: move the current `JWT_PUBLIC_KEY` to
   `JWT_PUBLIC_KEY_OLD`, and set the new private/public pair.
3. Redeploy.
4. After `ACCESS_TOKEN_EXPIRE_MINUTES` (15 by default), clear
   `JWT_PUBLIC_KEY_OLD` and redeploy again.

**Expected:** no change visible to users. No sign-out, and no burst of
simultaneous refreshes.

There is deliberately no old *private* key: signing always uses the current
one, because nothing should still be minting tokens with a retired key.

**Before doing this, check whether `URL_TOKEN_SECRET` is set.** If it is
blank, `JWT_PRIVATE_KEY` is *also* the signing secret for every emailed link
-- password reset, email verification, magic links, email-change
confirm/cancel, the OAuth `state` parameter, the 2FA challenge token -- and
rotating it invalidates all of them, up to 24 hours' worth. Do step 6b first
and this becomes an ordinary keypair rotation.

### 6b. Give URL tokens their own secret

A one-time migration, worth doing before the first JWT rotation. It exists
because these two were coupled by accident: `_url_token_serializer` borrowed
`JWT_PRIVATE_KEY` for want of a dedicated field.

1. Generate a secret:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`
2. In `PROD_ENV_FILE`, set `URL_TOKEN_SECRET` to it and
   `URL_TOKEN_SECRET_OLD` to the **current `JWT_PRIVATE_KEY`**, verbatim.
   Both at once -- that is what keeps links already in flight working.
3. Redeploy.
4. Wait 24 hours: the longest URL-token TTL is the email-verification link.
5. Clear `URL_TOKEN_SECRET_OLD` and redeploy.

**Expected:** a password-reset link emailed before step 3 still works after
it. itsdangerous verifies against every key it is given and signs with the
last, so both are live during the overlap.

Thereafter `URL_TOKEN_SECRET` rotates the same way: new value in
`URL_TOKEN_SECRET`, previous in `URL_TOKEN_SECRET_OLD`, wait 24 hours,
clear.

### 7. Rotate the password pepper

Supported since 2026-09-14, and worth reading before starting, because it is
the one rotation with no completion signal.

`PASSWORD_PEPPER` is HMAC'd into every password before Argon2, so a hash
cannot be rewritten without the plaintext -- and only the user has that.
There is therefore no background sweep, as there is for `ENCRYPTION_KEY`.
Rows migrate one successful login at a time.

1. Generate the new pepper:
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`
2. In `PROD_ENV_FILE`, move the current value to `PASSWORD_PEPPER_OLD` and
   put the new one in `PASSWORD_PEPPER`. **Both set, at once.**
3. Redeploy.

**Expected:** logins keep working, unchanged, for everyone. Verification
falls back to the old pepper, and each success re-hashes that user's
password with the new one (`app.modules.auth.utils.check_password` ->
`AuthService.login`).

4. Leave both set while logins drain. How long is a judgement call about
   your login volume, not a status you can query: an account that never
   signs in again keeps its old hash forever.
5. Clear `PASSWORD_PEPPER_OLD` and redeploy.

**Expected:** logins still work for everyone who has signed in since step 3.
Anyone who has not is now locked out and must use password reset — which is
the trade being made, and the reason step 4 is not "wait an hour".

Two things to know while both are set:

- A **wrong** password costs two Argon2 verifications instead of one, and
  Argon2 is memory-hard on purpose. The login rate limit (5/minute per IP)
  bounds it, but do not leave the old pepper set indefinitely "just in case".
- The same mechanism migrates hashes when Argon2's own cost parameters are
  raised, via `check_needs_rehash` — so that is a free ride on this path.

**If the pepper leaked**, rotating is now a real option rather than a forced
reset for everyone. Note it is still defence in depth *on top of* Argon2's
per-hash salt: a leaked pepper alone does not make hashes crackable. Rotate,
but do not panic-force resets unless the hashes leaked too.

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

- **`PASSWORD_PEPPER`**, once `PASSWORD_PEPPER_OLD` has been cleared. Until
  then rollback is free — put the old value back as the current one. After
  it, any password re-hashed under the new pepper needs the new pepper, so
  going back strands exactly the users who have logged in most recently.
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
