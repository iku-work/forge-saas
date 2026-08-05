# Backup/restore runbook (T-225)

> **Status**: written, not yet executed against a live instance — see
> "Verification status" at the bottom. Whoever first deploys this template to
> a real Render account should run the restore drill once and update that
> section, rather than trusting this document blind.

Covers the two deployment paths this repo supports: Render (managed
Postgres/Redis, `docs/runbooks/deploy-render.md`) and self-hosted
(`docker-compose.prod.yml`, Postgres/Redis as containers on `forge-net`).

## Trigger

- Data loss or corruption: a bad migration, a `DELETE` without a `WHERE`, an
  application bug that wrote wrong rows for a known window.
- A destroyed or unbootable database instance.
- A restore **drill** — the reason the "Verification status" section at the
  bottom exists. Running this on purpose, on a schedule, is the only thing
  that makes it trustworthy in an emergency.

Not this runbook: Redis loss (see the note at the end of the Render section —
it holds no system of record), a single accidentally-deleted row where the
application has its own undo (a soft-deleted org, a GDPR export), or key
rotation, which is `encryption-key-rotation.md`.

## Pre-checks

- **Know which deployment path you are on.** Render-managed and self-hosted
  share nothing below except the verification step.
- **Know the target time**, and how much data loss it implies. Daily snapshot
  or PITR timestamp — write the number down before you start, because it is
  the number you will be asked for afterwards.
- **Credentials**: the Render dashboard (or `psql` plus the backup archive on
  the self-hosted path), and access to update `PROD_ENV_FILE` in GitHub
  Actions secrets for the cutover.
- **Confirm the backup exists and covers the target time** before touching the
  live instance. A restore procedure started against a backup that turns out
  not to be there is how a recoverable incident becomes an unrecoverable one.
- **Ciphertext needs its key.** Encrypted columns restore as ciphertext; if
  `ENCRYPTION_KEY` has rotated since the backup was taken, the key that was
  current *then* must still be available (`ENCRYPTION_KEY_OLD`), or those
  columns come back unreadable. See `encryption-key-rotation.md`.

## Steps

### Render (managed Postgres)

Render's managed PostgreSQL takes automatic daily backups and retains them
for a period set by the instance's plan (7 days on the cheapest paid tier at
the time of writing — confirm against the current plan on the Render
dashboard, this changes independently of this repo). Point-in-time recovery
(PITR) within the retention window is also available on paid plans.

**Restore procedure** (Dashboard → the Postgres instance → **Backups**):

1. Pick a backup (daily snapshot) or a specific timestamp (PITR, if enabled
   on the plan).
2. Render restores into a **new** Postgres instance — it does not overwrite
   the live one in place. This is deliberate: it gives you a chance to
   verify the restored data before cutting traffic over.
3. Verify the restored instance: connect with `psql` and spot-check recent
   rows in a few tables that change often (`session`, `audit_log`,
   `notification`), and run `alembic current` against it to confirm the
   schema version matches what's expected for that point in time.
4. Cut over: update `DATABASE_URL` (the `PROD_ENV_FILE` GitHub Actions
   secret, per `deploy-render.md`) to the restored instance's connection
   string, then redeploy the backend/worker services so they pick it up.
5. Decommission the old instance once the new one has been serving
   correctly for a reasonable soak period — don't delete it immediately
   after cutover.

Render's managed Redis (Key Value) is a cache/rate-limit/session-lookup
layer in this app, not a system of record — `Session`/`RefreshToken` rows
live in Postgres, and Redis only holds derived/ephemeral state (rate-limit
counters, cached counts, SSE fan-out). Losing it is a availability blip
(users get logged out, rate limits reset), not a data-loss event, so it's
intentionally not covered by a restore procedure here.

### Self-hosted (`docker-compose.prod.yml`)

The `postgres`/`redis` services use named Docker volumes (`postgres_data`,
`redis_data`). Back these up the same way you'd back up any Docker volume —
e.g. `docker run --rm -v postgres_data:/data -v $(pwd):/backup alpine tar
czf /backup/postgres_data.tar.gz /data` while the container is stopped (or
use `pg_basebackup`/`pg_dump` against a running instance for a consistent
online backup, which is the better option for anything beyond a one-off).
There's no automatic scheduling of this in the compose file — an operator
choosing the self-hosted path is expected to wire up their own backup
schedule (cron + off-host upload) against their actual retention
requirements, which will vary a lot more than Render's fixed offering.

**Restore procedure**: stop the `postgres`/`redis`/`backend`/`worker`
services, restore the volume contents from the backup archive, then bring
the stack back up (`docker compose -f docker-compose.prod.yml up -d`).
Verify with the same `psql`/`alembic current` spot-check as the Render path
above before resuming traffic.

## Verification

Both paths converge here, and none of it should be done against the live
instance's connection string — verify the *restored* database directly, before
any traffic points at it:

- `alembic current` returns the revision expected for that point in time. A
  restored database one migration behind the running code is a broken app, not
  a restored one; run `alembic upgrade head` deliberately if so, rather than
  discovering it from the first 500.
- Spot-check the tables that change most (`session`, `audit_log`,
  `notification`): recent rows exist, and the newest timestamp matches the
  restore target rather than being days older than expected.
- Read one encrypted column end-to-end through the app (a profile name, a
  2FA-enabled login) — the check that catches the key mismatch from Pre-checks,
  which nothing else surfaces until a user hits it.
- After cutover: `/health/ready` returns 200, then a real login and one write.
  `/health` alone proves the process is up, not that it can reach the database.

## Rollback

The Render path is built to be rolled back: the restore lands in a **new**
instance and the old one keeps running, so "rollback" is pointing
`DATABASE_URL` back at the original and redeploying. That property is the
whole reason step 5 says not to decommission immediately — the soak period is
the rollback window, and deleting the old instance is the point of no return.

The self-hosted path has no such window by default: restoring a volume in
place overwrites it. **Copy the current volume aside before restoring over
it** (the same `tar` command above, into a differently-named archive), even
when the data in it is believed to be corrupt — it is the only copy of
whatever was written since the last backup.

Either way, a cutover that has been serving writes for a while is no longer
cleanly reversible: rolling back to the pre-restore database now loses those
writes. Decide how long the soak is before starting, not while deciding.

## Escalation

- **No backup covers the target time**, or the archive is corrupt: stop and
  escalate before improvising. Partial recovery from WAL, a logical export of
  what survives, or accepting a longer data-loss window are decisions for
  whoever owns the data, not for the operator running the restore.
- **The restored instance fails `alembic current`** or the schema doesn't
  match any known revision — likely a backup from a different environment.
  Verify before letting the application touch it; an `alembic upgrade` against
  the wrong database compounds the incident.
- **Encrypted columns won't decrypt after restore.** Do not run the rotation
  job to "fix" it — see `encryption-key-rotation.md`'s step 5 warning; the job
  would re-encrypt ciphertext it can't read. Find the old key first.
- **Any restore that was executed for real** — drill or incident — updates the
  "Verification status" checklist below with the date and who ran it. That is
  how the "written, not yet executed" caveat at the top of this file
  eventually goes away.

## Docker-network TLS (self-hosted path only)

`app.core.config.Settings._enforce_startup_invariants` (T-225) now requires
`sslmode=require` (or stricter) on `DATABASE_URL` and a `rediss://` scheme
on `REDIS_URL` outside `dev` — the app refuses to boot otherwise. Since
T-244 the same requirement covers `MIGRATIONS_DATABASE_URL` when it is set:
that is a second, independent hop to Postgres (it bypasses pgbouncer), so
`DATABASE_URL`'s own TLS setting says nothing about it. Render's
managed Postgres/Redis already satisfy this on their external connection
strings, so this is a no-op for the Render path. For the self-hosted
`docker-compose.prod.yml` path, an operator must additionally:

1. **Postgres**: generate a server cert/key (self-signed is fine for
   container-to-container traffic on a private bridge network — this isn't
   protecting against an untrusted network path the way the public
   Caddy-terminated hop is, it's closing the "cleartext on a shared Docker
   bridge" gap). Mount them into the `postgres` service and set
   `ssl = on`, `ssl_cert_file`, `ssl_key_file` in `postgresql.conf` (or via
   the image's `POSTGRES_*` cert env vars, depending on the Postgres image
   used). Add `sslmode=require` to `DATABASE_URL`'s query string.
2. **Redis**: Redis 7's built-in TLS support requires starting
   `redis-server` with `--tls-port 6379 --port 0` plus cert/key/CA file
   arguments, mounted the same way as Postgres's above. Change
   `REDIS_URL`'s scheme to `rediss://`.
3. **PgBouncer** (T-244): the app's `DATABASE_URL` now terminates at the
   pooler, not at Postgres, so that hop needs its own cert — set
   `CLIENT_TLS_SSLMODE=require` plus `CLIENT_TLS_KEY_FILE`/
   `CLIENT_TLS_CERT_FILE` in `.env.pgbouncer`, and mount the material into
   the `pgbouncer` service. The pooler's *upstream* hop to Postgres is a
   separate setting: `SERVER_TLS_SSLMODE=require` (plus
   `SERVER_TLS_CA_FILE`). Configuring only the client side leaves
   pgbouncer→Postgres in cleartext while `DATABASE_URL` still reads
   `sslmode=require`, which is the trap worth being explicit about. The
   direct `MIGRATIONS_DATABASE_URL` hop is covered by step 1 above.
4. Rotate these certs the same way you'd rotate any other cert in your
   environment — they aren't ACME-managed the way Caddy's public-facing
   cert is, so there's no automatic renewal here.

None of the above is wired into `docker-compose.prod.yml` by default: baking
in auto-generated self-signed certs would need its own rotation story this
template doesn't own, and the cert material itself shouldn't live in a
committed compose file. This section is the operator checklist for turning
it on, not a toggle.

## Verification status

- [ ] Render restore drill executed against a real (non-template) Render
      account, confirming the dashboard flow above matches Render's current
      UI/API and that a restored instance actually boots the app correctly.
- [ ] Self-hosted volume backup/restore exercised against a real
      `docker-compose.prod.yml` deployment.
- [ ] Docker-network TLS steps above exercised end-to-end on the self-hosted
      path (cert generation through a successful `alembic upgrade head`
      against the TLS-enabled connection).

None of these three are checked off as of T-225 — this repo is a template
with no live deployment yet (see `docs/runbooks/deploy-render.md`). Check
one off (with the date and who ran it) the first time each path is actually
exercised for real.
