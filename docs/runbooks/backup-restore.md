# Backup/restore runbook (T-225)

> **Status**: the procedures are written and the self-hosted path now ships
> working scripts (`scripts/backup-postgres.sh`, `scripts/restore-postgres.sh`),
> but **no restore has been executed against a live instance** — see
> "Verification status" at the bottom. Until that box is ticked this
> document describes an intention, not a capability. Run the drill once,
> record the timing, and tick it.

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
That paragraph used to end "an operator choosing the self-hosted path is
expected to wire up their own backup schedule". That is a fair thing to say
about *retention policy* and a bad thing to say about whether backups happen
at all: the 2026-09-24 audit found one dump, four days old, mode 0664, on
the same disk as the database, and no restore ever executed. Two scripts now
ship instead.

### `scripts/backup-postgres.sh`

`pg_dump -Fc` from inside the container, encrypted to an age public key,
written 0600, pruned on a retention window, optionally copied off-host with
rclone. It refuses to run rather than writing plaintext if the recipient or
`age` is missing.

**Set up the key first, and not on this box.** The point of public-key
encryption here is that the host can write backups and cannot read them, so
a compromise of the box does not hand over a copy of every row in the
product:

```sh
# on your laptop, NOT the server
age-keygen -o forge-backup-key.txt      # prints the public key
# store forge-backup-key.txt wherever you keep ENCRYPTION_KEY's recovery
# copy -- the two are needed together, see "Ciphertext needs its key" above
```

Then on the server, as a systemd timer (preferred — cron swallows output):

```ini
# /etc/systemd/system/forge-backup.service
[Service]
Type=oneshot
WorkingDirectory=/srv/forge
Environment=BACKUP_AGE_RECIPIENT=age1...        # the public key
Environment=BACKUP_DIR=/var/backups/forge
Environment=BACKUP_RETENTION_DAYS=14
Environment=BACKUP_REMOTE=b2:forge-backups      # omit for local-only
ExecStart=/srv/forge/scripts/backup-postgres.sh
```

```ini
# /etc/systemd/system/forge-backup.timer
[Timer]
OnCalendar=*-*-* 03:30:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
```

```sh
systemctl enable --now forge-backup.timer
systemctl list-timers forge-backup.timer      # confirm the next run
journalctl -u forge-backup.service            # confirm the last one
```

`Persistent=true` matters on a single box: it runs a missed backup after a
reboot rather than skipping the day silently.

### `scripts/restore-postgres.sh` — and the drill

```sh
# the drill: restores into a scratch database, times it, spot-checks it
AGE_IDENTITY=forge-backup-key.txt   ./scripts/restore-postgres.sh /var/backups/forge/forge-<stamp>.dump.age
```

It defaults to the scratch database `forge_restore_check` precisely so the
drill is the easy thing to run. Restoring over the live database needs an
explicit `--into forge`, and warns for ten seconds before it starts.

**Run the drill on a schedule and record the elapsed time below.** An
untested backup is a belief. The three things a drill catches that nothing
else does: an archive that decrypts but restores empty, a retention window
that quietly deleted everything usable, and an `ENCRYPTION_KEY` that rotated
without `ENCRYPTION_KEY_OLD` being kept — which restores every encrypted
column as unreadable ciphertext while every row count looks correct.

**Emergency restore**: stop `backend` and `worker` first (`docker compose -f
docker-compose.prod.yml stop backend worker`) so nothing writes underneath
the restore, then `--into` the live database name, then bring them back and
verify with the same `psql`/`alembic current` spot-check as the Render path
above before resuming traffic.

### Redis

Not backed up, deliberately, and this is unchanged: Redis holds caches,
rate-limit counters and the ARQ queue — no system of record. Losing it costs
queued jobs, which is a real cost and a different problem (see
`degraded-mode.md`), not a data-loss one.

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

`app.core.config.Settings._enforce_startup_invariants` (T-225) requires
`sslmode=require` (or stricter) on `DATABASE_URL` and a `rediss://` scheme on
`REDIS_URL` outside `dev` — the app refuses to boot otherwise. Since T-244 the
same requirement covers `MIGRATIONS_DATABASE_URL`: that is a second,
independent hop to Postgres (it bypasses pgbouncer), so `DATABASE_URL`'s own
TLS setting says nothing about it. Render's managed Postgres satisfies this on
its connection string, so this section is the self-hosted path only.

**This is now one command, not a manual checklist.**

```sh
make internal-certs      # writes ./certs/server.{crt,key}, gitignored
docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml up -d
```

`docker-compose.tls.yml` is an overlay rather than the default because the
certificate material must not live in a committed file and has a rotation
story this template does not own. What it turns on:

| Hop | Mechanism | Verified by |
|---|---|---|
| app → pgbouncer | `client_tls_sslmode=require` + cert/key | `SHOW CLIENTS` reports `TLSv1.3/TLS_AES_256_GCM_SHA384` |
| pgbouncer → Postgres | `server_tls_sslmode=verify-ca` + CA file | `pg_stat_ssl` reports `ssl=true TLSv1.3` |
| app/worker → Redis | `--tls-port 6379 --port 0`, CA passed on the DSN | `rediss://` ping succeeds; a plaintext connect is reset |
| Alembic → Postgres | direct DSN, step above | `python -m app.core.migrations` completes |

Run `make prod-compose-tls-check` to reproduce all four against the real
`docker-compose.prod.yml` with `ENVIRONMENT=prod`. It also asserts the pooling
contract still holds under TLS: 150 concurrent pooled queries return correct
results and leave **zero** rows in `pg_prepared_statements`.

### Things that cost time here, so you do not rediscover them

- **`server_tls_sslmode` is not implied by `client_tls_sslmode`.** Setting only
  the client side leaves pgbouncer → Postgres in cleartext while every DSN
  still reads `sslmode=require`. Nothing in the application can detect this;
  `pg_stat_ssl` on the Postgres side is the only honest check.
- **The pgbouncer image runs as uid 70** and pgbouncer refuses a key file that
  is group- or world-readable. The overlay starts as root, places the material
  with the right owner and mode, then drops back. It uses BusyBox `su` —
  `su-exec` and `gosu` are not in that image, and its `setpriv` has no
  `--reuid`.
- **`--port 0` on Redis matters.** Without it the cleartext listener stays open
  alongside the TLS one, so `rediss://` satisfies the startup invariant while
  anything on `forge-net` can still connect in the clear.
- **Never write these commands as a YAML `>` folded scalar.** Continuation
  lines indented further than the first keep their newlines, so
  `exec redis-server 
 --tls-port ...` runs `redis-server` with no arguments
  at all. The stack then comes up healthy with TLS entirely off — which is
  exactly the failure this section exists to prevent, arriving disguised as
  success.
- **`asyncpg` does not accept `sslmode`.** It takes `ssl=`, and SQLAlchemy
  passes URL query parameters through verbatim, so `app.core.db.split_ssl_params`
  translates it. Removing that helper makes every DSN above fail with
  `TypeError` before a byte reaches Postgres.
- **`asyncpg` does not accept `sslmode`** — covered above.

### Rotation

```sh
make check-certs             # days left; non-zero exit inside WARN_DAYS (30)
make rotate-internal-certs   # replace the material on a running stack
```

There is no ACME here. Caddy's public certificate renews itself; these do not,
and when they lapse the stack goes down for a reason that looks nothing like a
certificate problem from outside — the public certificate is healthy, so the
obvious suspect is innocent. **Put `check-cert-expiry.sh` in cron**; it exits
non-zero when anything is inside the warning window, so cron mails you:

```
0 6 * * *  cd /srv/forge && ./scripts/check-cert-expiry.sh >/dev/null
```

**Why rotation is three phases and not "regenerate, restart".** Two hops here
*verify* rather than merely encrypt: pgbouncer checks Postgres against a CA,
and the app and worker check Redis against one. Replace a server's certificate
before its verifier trusts the new issuer and that hop fails closed until the
verifier catches up. So trust widens first and narrows last:

| Phase | `ca.crt` holds | Restarts | Invariant |
|---|---|---|---|
| 1 | old + new | pgbouncer, backend, worker | verifiers accept both |
| 2 | old + new | postgres, redis, pgbouncer | new certificate served, still trusted |
| 3 | new | pgbouncer, backend, worker | old issuer no longer accepted |

At no point does a service present something a verifier does not already
accept. This removes the *failure* window, not the restarts — connections
still drop as containers recreate, and Caddy will 502 briefly.

This is also why `server.crt` and `ca.crt` are separate files even though they
start out identical. Point trust and identity at one path and the trust set
cannot be widened without simultaneously changing what is served, which is
precisely the window the phases exist to remove.

`--force-recreate` rather than `restart`: each container copies the material
in at start, so a restarted container reuses its stale copy.

## Verification status

- [ ] Render restore drill executed against a real (non-template) Render
      account, confirming the dashboard flow above matches Render's current
      UI/API and that a restored instance actually boots the app correctly.
- [ ] Self-hosted backup/restore exercised against a real
      `docker-compose.prod.yml` deployment: `scripts/backup-postgres.sh` on
      its timer, then `scripts/restore-postgres.sh` into the scratch
      database. **Record the elapsed restore time and the archive size**
      when this is ticked -- a restore that works but takes four hours is a
      different answer to "are we covered?" than one that takes four
      minutes, and only the drill distinguishes them.
- [x] **Docker-network TLS exercised end-to-end on the self-hosted path
      (2026-09-03).** `make prod-compose-tls-check` against the real
      `docker-compose.prod.yml` with `ENVIRONMENT=prod`: certificate
      generation, `python -m app.core.migrations` over the direct TLS hop,
      `/health/ready` 200 through the pooler, and all three hops confirmed
      encrypted from the far side — `pg_stat_ssl` reporting `ssl=true
      TLSv1.3` for pgbouncer→Postgres, `SHOW CLIENTS` reporting
      `TLSv1.3/TLS_AES_256_GCM_SHA384` for app→pgbouncer, and a plaintext
      Redis connect reset. 150 concurrent pooled queries returned correct
      results leaving zero rows in `pg_prepared_statements`, so ADR-0002's
      contract holds under TLS.

- [x] **Certificate rotation exercised on a running stack (2026-09-03).**
      `make rotate-internal-certs` against the live TLS stack: the served
      fingerprint changed, `/health/ready` returned 200 afterwards, both
      Postgres hops still reported TLSv1.3, the trust bundle was pruned back
      to exactly one certificate matching the new server cert, and a
      deliberately foreign CA was still rejected with
      `CERTIFICATE_VERIFY_FAILED` — so verification is genuinely enforced and
      the phasing is load-bearing rather than decorative. The expiry checker
      was exercised on its ok, expiring and two-certificate-bundle paths.

      Not covered: any certificate other than the self-signed one the
      generator writes, and rotation under sustained load (the restarts drop
      connections; only the verification window is closed).

The first two are not checked off — this repo is a template with no live
deployment yet (see `docs/runbooks/deploy-render.md`). Check one off (with the
date and who ran it) the first time each path is actually exercised for real.
