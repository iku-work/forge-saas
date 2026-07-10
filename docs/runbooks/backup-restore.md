# Backup/restore runbook (T-225)

> **Status**: written, not yet executed against a live instance — see
> "Verification status" at the bottom. Whoever first deploys this template to
> a real Render account should run the restore drill once and update that
> section, rather than trusting this document blind.

Covers the two deployment paths this repo supports: Render (managed
Postgres/Redis, `docs/runbooks/deploy-render.md`) and self-hosted
(`docker-compose.prod.yml`, Postgres/Redis as containers on `forge-net`).

## Render (managed Postgres)

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

## Self-hosted (`docker-compose.prod.yml`)

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

## Docker-network TLS (self-hosted path only)

`app.core.config.Settings._enforce_startup_invariants` (T-225) now requires
`sslmode=require` (or stricter) on `DATABASE_URL` and a `rediss://` scheme
on `REDIS_URL` outside `dev` — the app refuses to boot otherwise. Render's
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
3. Rotate these certs the same way you'd rotate any other cert in your
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
