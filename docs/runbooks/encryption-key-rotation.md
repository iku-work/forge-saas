# Encryption key rotation runbook (T-245)

Rotating `ENCRYPTION_KEY` — the Fernet key behind every `EncryptedString`
column (`backend/app/core/encrypted_type.py`) — with no downtime, no read
errors, and no window in which a row is unreadable.

The policy side of this (who holds the key, how it's generated, what a
compromise means) is [`docs/security/key-management.md`](../security/key-management.md).
This document is the operation.

## Trigger

- Scheduled rotation (the key-management policy's cadence).
- Suspected key compromise — leaked in a log, committed to a repo, exposed
  via a misconfigured secret store. **Rotating does not undo the disclosure**
  of anything encrypted under the old key; see §5 of the key-management doc.
- A one-off backfill: a column that has just started using `EncryptedString`
  still holds legacy plaintext rows. Same job, same steps, no key change —
  run it with `--only <table>.<column>`.

## Pre-checks

1. **Know the size of the job.** The dry run in step 2 tells you exactly, but
   get a rough row count first — a rotation of a few thousand rows is a
   coffee break; tens of millions is a scheduled operation.
2. **Confirm the current key is actually current.** If `ENCRYPTION_KEY_OLD`
   is already set from a previous rotation that never finished step 5, finish
   *that* one first (run the job, confirm zero rows move, drop the old key)
   rather than stacking a second key on top. The job handles a three-key
   chain correctly, but the operator usually doesn't.
3. **Have a place to run a long foreground process**: a one-off container/
   shell on the same network as the database (`docker compose exec worker …`
   locally; a one-off job or shell on the platform in staging/prod).
4. **Backups.** Confirm the most recent backup restored cleanly (see
   [`backup-restore.md`](backup-restore.md)). This procedure is safe at every
   step, but "the ciphertext is fine and the key is gone" is not a recoverable
   state, so the backup matters more than usual here.

## Steps

### Step 1 — Add the new key, demote the current one

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set both variables in the platform's secret store and deploy:

```
ENCRYPTION_KEY=<the new key>
ENCRYPTION_KEY_OLD=<the key that was ENCRYPTION_KEY until now>
```

`ENCRYPTION_KEY_OLD` is comma-separated, so a key already in that list stays
in it — append, don't overwrite, if a previous rotation is still in flight.

**Expected after deploy:** nothing user-visible changes. `MultiFernet`
decrypts with either key, so every existing row still reads; every *new*
write is encrypted with the new key from this moment on.

**Verify:** load a page that reads an encrypted column (a user's profile
name, a 2FA-enabled login). A `ValueError: EncryptedString: failed to
decrypt…` in the logs at this point means the wrong key went into
`ENCRYPTION_KEY_OLD` — go straight to the rollback below.

**Rollback:** put the previous key back as `ENCRYPTION_KEY` and clear
`ENCRYPTION_KEY_OLD`, deploy. Safe as long as no rows have been rotated yet
(step 3 hasn't run). If some rows *have* been rotated, roll back by swapping
instead: `ENCRYPTION_KEY=<old>`, `ENCRYPTION_KEY_OLD=<new>` — both keys stay
present, so both the rotated and unrotated rows keep reading.

### Step 2 — Dry run

```bash
docker compose exec worker python -m app.workers.jobs.encryption --dry-run
```

Reads every encrypted column and counts what would move. It writes nothing at
all — no row updates, and no `JobRecord` either — so it is safe to run against
production at any time, including before step 1.

**Expected output** (a JSON report on stdout):

```json
{
  "dry_run": true,
  "resumed": false,
  "job_record_id": null,
  "columns": [
    {"table": "totp_secret", "column": "secret_encrypted", "scanned": 812, "rotated": 812},
    {"table": "user", "column": "full_name", "scanned": 41902, "rotated": 41902}
  ],
  "scanned_total": 42714,
  "rotated_total": 42714
}
```

`rotated` in a dry run is the *estimate*: rows that would be rewritten.
`scanned` is every row the job would read.

**Read it for two things.** The column list should match the schema's
encrypted columns — it is discovered from `SQLModel.metadata`, so a missing
entry means the column isn't declared `EncryptedString`. And `rotated_total`
should be roughly the whole table right after step 1; a much smaller number
means most rows are already on the new key (a rotation someone already ran).

**Rollback:** none needed — a dry run changes nothing.

### Step 3 — Run the rotation

```bash
docker compose exec worker python -m app.workers.jobs.encryption \
  --batch-size 500 --throttle 0.1
```

Both flags default to `ENCRYPTION_ROTATION_BATCH_SIZE` /
`ENCRYPTION_ROTATION_THROTTLE_SECONDS` (500 and 0.1s). The throttle is what
keeps a multi-hour scan from monopolising the connection pool (§0.9: 20
connections); raise it if the rotation runs alongside peak traffic, drop it to
`0` inside a maintenance window.

For the first rotation on a large database, do one column at a time:

```bash
… python -m app.workers.jobs.encryption --only user.full_name
```

**Why a one-off process and not the queue:** `WorkerSettings.job_timeout` is
300s, and a full scan of a large table isn't a five-minute job. Enqueuing
`rotate_encryption_keys_job` works and resumes correctly if it is killed at
the timeout, but a foreground process is what you can watch.

#### Watching it

- **Prometheus:** `forge_reencrypt_remaining` — rows not yet scanned in the
  current run, counting down to 0. The job publishes it to Redis and the
  backend's `/metrics` serves it (the job's own process isn't scraped), the
  same split `forge_active_sessions` uses. `rate(forge_reencrypt_rows_total[5m])`
  is the throughput; `forge_reencrypt_batch_seconds` is per-batch latency.
- **The log line per batch**, which carries the same numbers plus the column.
- **The checkpoint**, which is authoritative:

  ```sql
  SELECT status, attempts, started_at, completed_at,
         result->'cursor' AS cursor,
         result->'scanned' AS scanned,
         result->'rotated' AS rotated
  FROM job_record
  WHERE job_type = 'rotate_encryption_keys'
  ORDER BY created_at DESC
  LIMIT 1;
  ```

**Expected on completion:** `status = 'done'`, `cursor` null, `completed_at`
set, and the same report on stdout with `"dry_run": false`.

#### If it dies mid-run (crash, redeploy, timeout, lost shell)

Nothing is lost and nothing is half-written: each batch commits its rows and
its checkpoint in one transaction, so the last completed batch is exactly what
`cursor` points at. The record stays `status = 'running'` — that *is* the
signal of an interrupted run.

Re-run the identical command. The job picks up the newest `running` record,
increments `attempts`, and continues from the cursor. Verify afterwards that
the report says `"resumed": true` and that `attempts` on the record went up.

To deliberately start over instead of continuing (e.g. the interrupted run
used the wrong `--only`), pass `--no-resume`. Re-scanning already-rotated rows
is safe — they're recognised as current and skipped — it just costs a scan.

#### Rollback during the run

Stop the process. The database is in a mixed state — some rows on the new key,
some on the old — and **that state is fine**: both keys are configured, so
every read succeeds. There is no "undo the rotated rows" step and no reason to
want one.

If you need to abandon the rotation entirely, apply the step 1 swap rollback
(`ENCRYPTION_KEY=<old>`, `ENCRYPTION_KEY_OLD=<new>`). Do **not** simply remove
one of the two keys while rows exist under it — that is the one genuinely
destructive move available here (see step 4).

### Step 4 — Verify no row is left on the old key

Run the job once more, with both keys still configured:

```bash
docker compose exec worker python -m app.workers.jobs.encryption
```

**Expected:** a full scan with `"rotated_total": 0`. That is the proof — every
row was read and none needed rewriting. `scanned_total` should match the
dry run's, modulo rows created since.

> **Do not skip this to save a scan.** If a row is still on the old key when
> `ENCRYPTION_KEY_OLD` is removed, the job can no longer decrypt it and will
> treat its ciphertext as legacy plaintext — encrypting the ciphertext *again*
> under the new key — and the application's read path will raise
> `ValueError: EncryptedString: failed to decrypt…` for that row until it's
> repaired from the old key. The verification scan is what makes step 5 a
> non-event.

### Step 5 — Remove the old key

Delete `ENCRYPTION_KEY_OLD` from the secret store (or remove just the rotated
key from the comma-separated list) and deploy.

**Expected:** no change in behaviour. Every row is on the new key.

**Verify:** the same read path as step 1 (a profile name, a 2FA login), plus
no `failed to decrypt` in the logs over the following minutes.

**Rollback:** put `ENCRYPTION_KEY_OLD` back and deploy. Keep the old key value
until you have seen a clean read after this deploy — this is the one step
where the old key is genuinely needed if something was missed.

## Verification

The rotation verifies itself in two places, and neither is optional:

- **Step 4's second full scan** with both keys still configured. A
  `"rotated_total": 0` over a `scanned_total` matching the dry run's is the
  only evidence that no row was left behind — a run that reports success can
  still have skipped rows created while it was scanning.
- **After step 5**, the same read path as step 1 (a profile name, a
  2FA-enabled login) plus no `failed to decrypt` in the logs over the
  following minutes. This is the check that catches a missed row, because
  until the old key is gone a missed row reads fine.

The negative check worth naming: `rotated_total: 0` on the *first* run means
the job found nothing to do, not that it succeeded. Compare it against the dry
run's estimate before believing it.

## Rollback

Every step before step 5 is reversible, because both keys are configured
throughout: a mixed database — some rows on the new key, some on the old — is
a correct database, not a half-finished one. The per-step rollbacks are inline
above (step 1's swap, "Rollback during the run" under step 3, step 5's).

**The point of no return is step 5.** Once `ENCRYPTION_KEY_OLD` is removed, a
row still on the old key can no longer be decrypted — and worse, the next run
of the job treats its ciphertext as legacy plaintext and encrypts it again.
Do not cross step 5 without step 4's clean scan, and keep the old key value
somewhere retrievable until you have seen a clean read after that deploy.

## Adding a new encrypted column later

Nothing here needs updating: the job discovers columns from
`SQLModel.metadata` at run time. Declare the column `EncryptedString`, ship
the migration, then run the job once (`--only <table>.<column>`) to convert
any pre-existing plaintext rows. Two constraints the job enforces at
discovery time, both with an explicit error: the table needs a single-column
primary key (it pages on it), and that primary key can't itself be the
encrypted column.

## Escalation

- Read errors (`failed to decrypt`) *during* steps 1–4: the key material is
  wrong. Restore the previous key pair immediately (step 1 rollback) — do not
  attempt a data fix first.
- Read errors *after* step 5: a row was missed. Put `ENCRYPTION_KEY_OLD` back,
  then run the job again and re-verify with step 4 before removing it a second
  time.
- Rotation throughput far below expectations, or the pool saturating: raise
  `--throttle`, lower `--batch-size`, stop and resume later. The checkpoint
  makes stopping free.
