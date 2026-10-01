# Follow-up remediation plan — 29 September 2026

Responds to `production-readiness-review-2026-09-29.md` (R1–R7 plus the CI/CD
section). Builds on `production-readiness-plan-2026-09-25.md` and corrects
claims in `production-readiness-report-2026-09-29.md`. Nothing here has been
implemented yet. Every design choice below comes from reading the code at
`705f392`. None of it has been reproduced against a database yet.

## Status

| Item | Severity | State |
| --- | --- | --- |
| 0 Correct the report | docs | **Done.** Correction notes in the report (§2, §3, §4, §5, §14) and the 09-25 status table |
| R2 Security writes rolled back on rejection (+ the harness that hid it) | P1 | **Fixed.** Reproduced first: after 10 wrong passwords the right one still logged in, and a replayed refresh token left the stolen session working. `persist_then_reject` (`app/core/db.py`) now keeps only the security consequence. Also covers WebAuthn clone detection, the SSO refusal audit, the permission-denied and impersonation audits, and a lapsed upload reservation. The test client rolls back like production (`tests/_sessions.py`). Found along the way and fixed: theft detection treated *every* revoked token as stolen, so a signed-out device's stale refresh signed the user out everywhere; `Session.revoked_reason` now separates rotation from signing out |
| R3 Admin gate trusts enrollment, not the session | P1 | **Fixed.** `Session.amr` records the factors each session passed and is copied by refresh. `require_superadmin` reads it from the session row. Enrolling a first second factor signs out every other session and marks the current one. One core migration (`9d4e1f7a2c63`, `amr` + `revoked_reason`). **Deploy note:** superadmins sign in once more to reach admin (D3) |
| R1 Scanned bytes are not the served bytes | P1 | **Fixed, reproduced on real MinIO first.** Uploads go to `incoming/<key>`, and confirm copies them to `<key>` (ETag-conditioned) before any check runs, so everything judged is what's served. Purges delete both copies. A MinIO lifecycle rule expires leftovers after a day (verified on a scratch MinIO for both compose files). **S3/R2 operators must add the same rule** (deploy runbook, step 5) |
| R4 Scan memory is unbounded by concurrency | P1 | **Fixed in code; memory limit still to measure.** Scans stream with per-chunk backpressure (32 MB streamed at < 8 MB peak; 38 MB without the drain), hold no DB connection, and run on their own queue in a new `scanner-worker` service (Hetzner, AWS and Render deploy paths updated). clamd's defaults checked in the image: `AlertExceedsMax` was off, so over-limit archives scanned clean. Now on, and limit hits map to "failed". **To do on the box:** the burst measurement in `docs/runbooks/malware-scanning.md` step 5, to set `scanner-worker`'s `mem_limit` |
| R5 Shared user/org rate budgets | P2 | **Fixed.** A shared per-user budget (100/min across all authenticated endpoints) and a per-org budget (5000/hour, D1), charged only after identity or membership is verified. The limiter's strategy is now a sliding window. API keys are excluded from the org budget. **Still to do:** a week of measurement to confirm 5000/hour |
| R6 Webhook response body and deadline | P2 | **Fixed.** The response is read raw up to a byte cap (an endless body costs one chunk). One `WEBHOOK_DEADLINE_SECONDS` covers DNS, connect, send and read. No DB transaction is held across the call |
| R7 "Scanning" never resolves (+ avatar race) | P2 | **Fixed.** The Files page polls while any file is scanning (2s, then 5s, then 15s) and stops when none are, or after 5 minutes with a "taking longer" note. An avatar can be chosen before its scan finishes, and `GET /auth/me/avatar` serves it only when clean; the shell retries a pending avatar |
| C CI/CD path to automatic deploy | gate | **C1–C5 done in code; C6 (the failure-injection drill) needs a throwaway target and a runner. `DEPLOY_TARGET` stays `none` until C6 has run.** C1: every CI install goes through the hash-pinned lock, and pip-audit reads the lock. Doing that found the shipped image carried `pyjwt 2.13.0` and `anyio 4.14.0` with open CVEs, invisible to the old audit because a fresh unpinned install got newer versions. pyjwt was held back by semgrep (a dev-only scanner) through the universal resolution, so semgrep now runs from its own pinned venv in CI and left the project's dependencies (17 packages gone). C2: deploys need `e2e-web` and `zap-baseline`. C3: each caller grants what its deploy job asks for (AWS's `id-token` had never taken effect). C4: no value is templated into a script's source (a pasted secret executing `touch` was demonstrated against the old form), and migrations run on the target: over SSH, as a Fargate task, as Render's now-required pre-deploy command. C5: Hetzner and AWS roll back after a half-finished switch; Hetzner's switch is idempotent and its rollback restores the env file and checkout. All asserted in `tests/test_ci_build_deploy.py`; none has run on a real runner |

The order is deliberate: **R2, R3, R1, R4, then R5–R7.** R2 comes first
because it is the smallest diff with the widest reach. Its harness fix
(R2.1) also changes what every HTTP test in the suite is able to observe, so
later work should be tested against the corrected harness, not the current
one.

---

## What planning found beyond the review

These came from tracing the review's findings into neighbouring code. Each is
labelled with how it was established. None of them has run against a
database.

1. **R2 is a class of bug, and the test harness is why nobody saw it.**
   `tests/conftest.py`'s `client` fixture sets
   `app.dependency_overrides[get_session] = lambda: session`. That is a plain
   function, not a `yield` dependency, so `get_session`'s
   `except Exception: rollback()` **never runs under test**. Every HTTP test
   therefore sees writes that production rolls back. The review's diagnosis
   is right, and it describes a larger class than refresh:
   - **The login lockout (T-018) never engages over HTTP.** `login()` calls
     `increment_failed_login` / `set_lockout` and writes `user.login_failed` /
     `user.account_locked` audit events. Then it raises. The router's commit
     is skipped, and `get_session` rolls all of it back. The counter never
     rises, so per-account lockout does not exist in production. Only the
     5/min per-IP `auth_limit` bounds guessing. (Source tracing. Every
     existing lockout test is service-level and runs in one session.)
   - **WebAuthn clone detection** (`service.py`, around line 1362):
     `revoke_all_for_user` runs, then `AuthenticationError`. Same outcome.
   - **SSO-enforced password refusal**: its audit event is lost the same
     way.
   - The two dependencies that *do* commit before raising
     (`require_permission`'s `permission.denied`, `reject_during_impersonation`)
     got there by someone noticing this bug one site at a time. The comments
     at `dependencies.py:291` and `:407` describe exactly this failure mode.
2. **Avatar upload races the scan.** `ProfileSettingsPage` PATCHes
   `avatar_file_id` inside `FileUploader`'s `onUploaded`, which fires on
   *confirm*. `resolve_own_avatar_key` requires a clean verdict, and the scan
   has only just been enqueued. With a real scanner, a first avatar upload
   should almost always end in the `auth.profile.error_avatar` toast. (Source
   tracing. Reproduce before fixing.)
3. **clamd's size limits may fail open, and may reject a maximum-size
   upload.** The compose service sets only `ConcurrentDatabaseReload`.
   clamd's documented defaults report files over `MaxFileSize` /
   `MaxScanSize` / recursion limits as `OK` unless `AlertExceedsMax` is on,
   and `StreamMaxLength` (documented default 100M) sits below
   `MAX_UPLOAD_BYTES` plus the 1 KiB confirm tolerance. **Verify against the
   running image** (`docker compose exec clamav clamconf`) before changing
   anything. This belongs with R4.
4. **The scan job and the webhook job hold a DB transaction across network
   I/O.** `virus_scan` autobegins on `session.get(...)` and keeps that
   transaction open through a download of up to 60 s and a scan of up to
   30 s. `deliver_webhook` does the same around the outbound POST. Under
   pgbouncer transaction pooling (T-244), each one pins a server connection
   for the whole call, so 20 concurrent jobs can take the entire pool. This
   is fixed as part of R4 and R6.

5. **Fixing R2 exposed a second bug in theft detection** (found while testing
   R3, then confirmed with a test that fails without the fix). `refresh()`
   treated *any* revoked refresh token coming back as a stolen one being
   replayed. Tokens revoked on purpose come back routinely: after a logout,
   a password change, "sign out other devices", or a second-factor
   enrollment elsewhere, the stale device tries its token once more. That
   stale refresh then revoked every session the user had, including the one
   that had just done the revoking. It never showed while R2 rolled the theft
   response back. Fix: `Session.revoked_reason`. Only a token revoked by
   *rotation* is a theft signal. `NULL` (revoked before the column existed)
   keeps the old, conservative reading.

---

## 0 · Stop overstating (docs only, first commit)

- `production-readiness-report-2026-09-29.md`:
  - §2: remove the claim that the stored digest binds downloads to the
    scanned bytes. It binds the *verdict* to those bytes, and downloads to
    nothing. Point at R1.
  - §3: remove "enrollment implies a challenge was completed". Point at R3.
  - §5: mark it partially resolved, since endpoint-scoped only. Point at R5.
  - §14 ("still open"): add R1–R7 and a link to this plan.
- `production-readiness-plan-2026-09-25.md` status table: 1.1/1.2 → "Done;
  bytes not yet bound (R1)"; 2.1 → "Done; admin gate still enrollment-based
  (R3)"; 3.2 → unchanged wording, but link R5.

Correct the record before the fixes land. Otherwise the docs keep claiming
closure while the work is in progress.

---

## R2 · Security writes must survive the rejection they cause

### R2.1 Make the test harness roll back the way production does

Replace the `client` fixture's override with a generator that mirrors
`get_session`:

```python
async def _session_override() -> AsyncGenerator[AsyncSession, None]:
    try:
        yield session
    except Exception:
        await session.rollback()   # ends the savepoint; the listener reopens one
        raise
```

Seeded data survives, because the rollback ends only the savepoint and the
outer transaction stays open.

**Expect tests to go red.** Each failure is either a test that asserted a
write the product never persists, which is a real bug to add to R2.3's list,
or a test that relied on the old semantics by accident, which needs a
rewrite. Triage every one of them. Do not flip them to green with
assertions. Land this change in the same PR as R2.2, because on its own it
turns CI red for reasons that are already understood.

### R2.2 Design: roll back, write the consequence, commit, then raise

Add one helper, used at every site where the rejection *is* the security
response:

```python
async def persist_then_reject(
    session: AsyncSession,
    consequence: Callable[[], Awaitable[None]],
    error: DomainError,
) -> NoReturn:
    await session.rollback()   # nothing else this request did survives
    await consequence()        # only the security state change + its audit row
    await session.commit()
    raise error
```

Why this shape and not the alternatives:

- **Plain commit-before-raise** (what the two dependencies do) commits
  whatever the request has pending. That is safe today on the refresh path
  and **already unsafe on the SSO-refusal path**: `reset_failed_login` and a
  possible password rehash are pending there. The review asks for exactly
  what this helper guarantees by construction: no unrelated writes committed.
- **An independent session/connection** ("autonomous transaction") is
  correct in production but untestable here. The test session's seed data
  lives in an uncommitted outer transaction that a second connection cannot
  see. It also adds a self-wait hazard whenever the request transaction
  already holds a lock on the row.
- **A marked exception that `get_session` commits on** is the same problem
  as plain commit-before-raise, applied globally.

Consequences must capture plain ids before the rollback, because ORM
instances are expired afterwards. Put the helper in `app/core/db.py` next to
`get_session`, since it is a unit-of-work rule rather than an auth rule, and
document it there as the only sanctioned way for a service to commit.

### R2.3 Sites to convert

| Site | Consequence that must persist |
| --- | --- |
| `refresh`: revoked token replayed | `revoke_all_for_user` |
| `refresh`: lost the rotation race | `revoke_all_for_user` |
| `refresh`: `is_session_usable` false | `revoke(session.id)` (hygiene, not theft) |
| `login`: wrong password | `increment_failed_login`, maybe `set_lockout` + `reset`, and the matching audit event |
| `login`: SSO-enforced refusal | `user.password_login_refused_sso` audit event |
| WebAuthn sign-count regression | `revoke_all_for_user` (+ an audit event, currently only a log line) |
| anything R2.1 turns red | as found |

Then convert `require_permission` and `reject_during_impersonation` to the
helper as well. Their current bare commit is safe only because "no endpoint
body has run yet", and that is an argument about call order. The helper
makes it structural.

### R2.4 Tests (committed per-test database, not the savepoint fixture)

The review asks for verification "from a fresh database session after the
response". That needs committed data, so build an HTTP fixture on a per-test
database with the real `get_session`, the way `test_auth_repository.py`'s
`test_engine` works, and read back through a separate engine.

- Log in on device A and device B. Rotate A. Replay A's *old* refresh token
  → 401. From a fresh session: A's successor and B are revoked. A's
  successor refresh token → 401. A's successor access token → 401 on
  `/auth/me`. That last step exercises the `sid` revocation check.
- Two concurrent `/auth/refresh` calls with the same token on two
  connections → exactly one 200, and afterwards every session is revoked.
- N wrong passwords over HTTP (lift `AUTH_RATE_LIMIT` for the test) → the
  next attempt with the correct password → `errors.account_locked`.
  `locked_until` and both audit actions are visible from a fresh session.
- The SSO-refusal path persists its audit row and **does not** persist the
  rehash. This is the "no unrelated writes" property.
- Keep `test_refresh_reuse_of_revoked_token_revokes_all_user_sessions`, but
  rename or annotate it as a service-level test. It cannot prove HTTP
  containment, and that should be obvious from its name.

**Done when:** every row in R2.3 has an HTTP test that reads from a fresh
session, and the harness change is merged with every resulting failure
triaged.

---

## R3 · The admin gate checks the session's factors, not the account's

### Design

- **`Session.amr`**: JSONB, `NOT NULL DEFAULT '[]'`. Core-branch migration
  with a working `downgrade()`. Set it when the session is created, from the
  path that created it:

  | Path | `amr` |
  | --- | --- |
  | password, no second factor enrolled | `["pwd"]` |
  | password + TOTP challenge | `["pwd", "otp"]` |
  | password + backup code | `["pwd", "otp"]`. A backup code is the TOTP factor's recovery; tag `"backup"` too if audit wants the distinction |
  | WebAuthn login | `["hwk"]` (+ `"pwd"` if the flow took a password) |
  | magic link (+ unified challenge) | `["email"]` plus the factor the challenge used |
  | social OAuth | `["oauth"]` plus the challenge factor if enrolled |
  | SSO | `["sso"]`, plus `"mfa"` only when T-331's `amr`/`acr` interpretation says the IdP performed MFA |

  `refresh()` copies `amr` unchanged, exactly as it copies `auth_time`. A
  rotation proves nothing new.
- The access token carries an `amr` claim. This closes the 09-25 plan's
  "2.2 `amr` claim — deferred deliberately". The admin gate still reads the
  **Session row**: `get_current_user` already consults it for revocation, so
  reuse that load (or its cache) rather than adding a query.
- `require_superadmin` changes from "enrolled" to **"enrolled *and* this
  session's `amr` contains a second factor"** (`otp`, `hwk`, or SSO `mfa`).
  Add a new error key, `errors.superadmin_mfa_session_required`, in every
  enabled locale (§0.11). The admin UI maps it to "sign in again to use
  admin".
- **Enrolling raises the bar for every existing session.** When TOTP
  verification succeeds (not when enrollment starts), or when a first
  WebAuthn credential is registered: revoke all *other* sessions, and add
  the factor to the current session's `amr`, since the verify code or the
  registration ceremony just proved possession. This is the review's
  interim control, kept permanently. Someone who enables 2FA because they
  suspect a compromise expects the other devices to be signed out.

### Rollout

Existing sessions get `amr = []`. Superadmins therefore have to sign in
again once to reach admin. Nothing else reads `amr` yet, so ordinary users
are unaffected. Say this in the deploy note. Check whether the branch has a
`CORE_HEAD_REVISION` constant before adding the migration (see the
frontend-refinement memory note).

### Tests

- The two-session test from the review: device A signs in with a password
  only. On device B the same superadmin enables TOTP. Then A's access token
  → 401 (session revoked), A's refresh → 401, and B passes the admin gate.
- A superadmin session with `amr=["pwd"]` whose account *is* enrolled →
  403 `superadmin_mfa_session_required`. This is the review's probe, now
  failing the right way.
- `amr` survives refresh. SSO with an MFA claim passes the gate, and
  without one it does not. First WebAuthn registration revokes the other
  sessions.
- Migration round-trip. `mypy --strict`.

---

## R1 · Serve exactly the bytes that were scanned

### Design: promote on confirm to a key the client can never write

- The presigned POST targets `incoming/{key}`. Derive that from `key` with
  a pure function, which needs no new column. `StoredFile.key` stays the
  canonical, server-only object, so every existing reader keeps working
  unchanged: downloads, avatars, hard delete, GDPR purge, the orphan sweep.
- `confirm_upload` becomes:
  1. `HEAD incoming/{key}` → size check (as now) → ETag.
  2. **`CopyObject incoming/{key} → {key}` with `CopySourceIfMatch=<ETag>`**,
     keeping SSE-AES256. The copy is atomic, so it copies one version of the
     source.
  3. Delete `incoming/{key}`.
  4. Everything after this point reads `{key}`: the content sniff, a final
     `HEAD`, the status transition, and the scan the job performs.
- The ordering is what closes the race. **All verification happens after
  promotion, on bytes only the server can change.** An overwrite of
  `incoming/` after step 2 is ignored. An overwrite between steps 1 and 2
  fails the precondition, which returns 409 "the upload changed, confirm
  again" and leaves the row `pending`. The review's warning about an
  unconditioned copy applies to copying *after* scanning. Here nothing is
  judged before the copy. The `IfMatch` is belt-and-braces, and it keeps the
  size check honest.
- A re-POST to `incoming/` within the form's remaining 600 s leaves an
  orphan. Extend `sweep_orphaned_objects` to delete `incoming/` objects
  older than the presign expiry plus a margin. Document an optional bucket
  lifecycle rule (`incoming/`, expire after 1 day) as the cheap backstop.
  Each presign bounds this to one object, because every re-POST overwrites
  the same key, so the orphans cannot grow into an unbounded storage leak.
- New `StorageBackend.copy_if_match(src, dst, etag)`. `R2Backend` and
  `MinioBackend` inherit the S3 implementation. Confirm both honour
  `x-amz-copy-source-if-match` in the backend test suite, which already runs
  against MinIO.

**Rejected: bucket versioning** (store the scanned `VersionId`, sign
downloads with it). Cloudflare R2 did not offer S3 object versioning at the
time of writing, so this fails one of the template's three backends. Every
overwrite inside the form's lifetime would also become retained bytes that
the quota cannot see, and every delete path would have to learn to delete
versions. **Rejected: signing `If-Match` into the download URL.** The
browser does not send the header for `window.open` / `<img src>`.

### Rollout

Rows already `ready` are unaffected. Their forms expired long ago, and
their bytes live at `key` as before. A row that is `pending` at deploy time
has a form pointing at `key`, so its confirm fails with `file_not_uploaded`
and the user uploads again. That window is at most one reservation (1 h).
Accept it and note it in the deploy note. Update T-111's "keys are
`{org_id}/…`" comment, since `incoming/` is now a second top-level prefix.

### Tests (MinIO testcontainer, real POST policy uploads)

The memory note on S3 POST-policy uploads covers the bucket-root URL and the
field-ordering traps. Read it before writing the fixture.

- **The review's scenario, end to end:** upload A → confirm → run the scan
  job (clean) → re-POST the *same* form with bytes B (same size and type) →
  the download URL serves A, byte for byte (compare with the stored
  `checksum_sha256`).
- The same scenario for avatars, through `GET /auth/me/avatar`.
- Overwrite between HEAD and copy (inject via the backend seam) → 409, the
  row stays `pending`, and `{key}` does not exist.
- The form cannot write `{key}`: a POST using the returned fields with the
  key changed → 403 from MinIO.
- The sweep deletes stale `incoming/` objects and leaves fresh ones.

---

## R4 · Bound scan memory and isolate scans from the other job types

### R4.1 Stream, hash incrementally, apply backpressure

- Replace `_fetch` → `bytes` with a **re-openable streaming source**. Each
  iteration re-issues the ranged GET, so the `@resilient("virus_scanner")`
  retry re-streams the object instead of replaying a buffer. Hash as the
  bytes pass.
- `VirusScanner.scan(source)`: `ClamAVScanner` writes each chunk and
  `await writer.drain()`s before reading the next one, so at most a chunk or
  two are in flight. `VirusTotalScanner` (dev only) drains the source for the
  digest, then does its lookup.
- Treat clamd's `INSTREAM size limit exceeded` as a permanent failure, not
  a transient one. It will be just as true on the third try.
- **Rejected: spooling to a temp file.** It is simpler for retries, but it
  moves 100 MiB per concurrent scan onto the one disk that keeps filling up
  (81% → 27% → 41% across one deploy).

### R4.2 Release the database before the I/O

Load the row and capture `key`/`size_bytes`/`org_id`. Close the session.
Stream and scan. Then open a new session and write the verdict with a
conditional update: `WHERE id = :id AND status = 'ready' AND
virus_scan_result IS NULL`. A file deleted or already resolved during the
scan is left alone. Log that case. It is not an error.

### R4.3 A scan queue with its own worker

- `virus_scan` is enqueued with `_queue_name = SCAN_QUEUE`. `ScanWorkerSettings`
  (`functions=[virus_scan]`, `max_jobs=settings.SCAN_MAX_CONCURRENCY`,
  default 4, and its own `arq --check` healthcheck) runs as a
  `scanner-worker` service (D2, approved) in both compose files with its own
  `mem_limit`.
  Size that limit from the R4.5 measurement, not now. The main worker stops
  registering `virus_scan`.
- **Rejected: an in-process semaphore.** A job waiting on the semaphore
  still holds one of the 20 `max_jobs` slots, so a burst of scans still
  starves email. This fails the review's "other job types still progress"
  requirement.
- **Trap:** a job enqueued to a queue whose worker does not register it
  never runs, and nothing reports it. Extend
  `tests/test_worker_registration.py` to check the *(queue, name)* pair,
  not just the name. The registry-pinning memory note applies: also update
  `test_worker_main.py`.

### R4.4 clamd configuration

After verifying the defaults (finding 3), set these through the image's own
`CLAMD_CONF_*` mechanism:
`StreamMaxLength` ≥ `MAX_UPLOAD_BYTES` + tolerance (derive it, say
`110M`), and `AlertExceedsMax yes`, so a file that exceeds a limit comes back
as `Heuristics.Limits.Exceeded` (`infected`, fails closed) rather than `OK`.
Add a config test tying the compose value to `MAX_UPLOAD_BYTES`, so raising
one without the other fails CI. Update `docs/runbooks/malware-scanning.md`.

### R4.5 Verification

- Unit: a 100 MiB synthetic object through the whole pipeline (fake
  transport, fake clamd that reads slowly) with `tracemalloc` peak under a
  small bound (~8 MiB). The fake clamd confirms the client never has more
  than two chunks written ahead of its reads.
- Isolation: with the scan worker saturated (a scanner that blocks), a
  `send_email` job on the default queue completes.
- **On the box, documented in the runbook:** 20 concurrent maximum-size
  uploads. Record peak RSS for `scanner-worker` and `worker` (`docker stats`)
  and confirm an email sends during the burst. This is the review's "verify
  peak RSS" requirement, and only the real stack can answer it.

---

## R5 · Shared user and org budgets (endpoint limits stay)

### Design

- **Keep** `auth_limit` / `sensitive_limit` / `api_limit` as they are.
  They defend specific endpoints.
- **User budget**: `user:{id}`, `API_RATE_LIMIT` (100/min), charged inside
  `get_current_user` *after* the JWT verifies. It is one bucket across every
  authenticated route, which is what makes it shared.
- **Org budget**: `org:{id}`, `ORG_RATE_LIMIT` (5000/hour, see D1), charged inside
  `get_current_org` **after membership is verified**. It must never be keyed
  on the raw `X-Org-ID` header, or any user could exhaust another org's
  budget by sending its id.
- Both use `current_strategy()`'s `hit()`, the pattern T-312 established,
  and T-312's 429 envelope and `X-RateLimit-*` headers.
- **Window:** move the limiter to limits' `sliding-window-counter` strategy
  (limits 5.8.0 is locked). That matches T-008's "sliding window" and it is
  cheap: two counters, not a sorted set. The change is global, so T-312's
  per-key and the endpoint limits change with it. Update their header tests
  in the same PR.
- Unauthenticated routes (health, `/config`, the Stripe webhook) never reach
  `get_current_user`, so they are exempt by construction.

### D1 · Org budget: 5000/hour, then measure (decided 2026-09-29)

§0.6's 1000/hour works out to about 16 requests a minute *for the whole
org*, which a few active users would exhaust. That figure was never
measured. **Decision:** ship `ORG_RATE_LIMIT` as a setting defaulting to
**5000/hour**, and change the §0.6 line in `AGENTS.md` to match, since the
law and the default must agree.

**Measure afterwards.** Record requests per active user per hour on the box
for a week, using the route-labelled Prometheus request counter plus the
org budget's own 429 count. Then confirm the default or change it. Add a
`forge_rate_limited_total{budget="org"}` counter so that week's data shows
how close real orgs come to the limit, and not only whether they crossed
it. Record the result in this plan's status table.

API-key traffic does **not** count toward the org budget. Keys have their
own per-key budgets (T-312), and an integration running at its budget
should not lock the org's people out. This was the plan's default, and the
decision did not override it.

### Tests

- One user alternates two routes, each under its own endpoint limit →
  request 101 in the minute gets a 429 from the *shared* budget.
- Two users in one org together cross the org budget → both get 429. A user
  in another org is unaffected.
- A request carrying another org's `X-Org-ID` → 403. That org's counter is
  unchanged.
- Window boundary: 100 requests at the end of one window and 100 at the
  start of the next → the sliding counter refuses part of the second batch.
  A fixed window would allow all 200.

Update report §5 when this lands.

---

## R6 · Webhook responses: cap the bytes, bound the time

- `client.stream("POST", …)`. Read `aiter_bytes()` until
  `MAX_RESPONSE_BODY_BYTES` (a byte cap, replacing today's char cap on
  already-buffered text), then stop and close. Decode with
  `errors="replace"`.
- `async with asyncio.timeout(WEBHOOK_TOTAL_DEADLINE_SECONDS)` around
  **everything outbound**: `validate_outbound_url` (DNS), connect, send, and
  read. A `TimeoutError` becomes `record_failure(error="deadline exceeded")`
  and follows the normal retry ladder.
- Release the DB transaction before the call, as in R4.2 (finding 4).
- Tests: a 50 MiB response → the stored body is at most the cap, the job
  finishes promptly, and `tracemalloc` shows a bounded peak. A slow-trickle
  body (a stream that sleeps between bytes) → ends at the deadline, recorded
  as a failure, retry scheduled. Slow DNS counts against the same deadline.

The module is off on the live box, so this is template risk rather than live
exposure. It is still P2, because any product that enables the module
inherits it.

---

## R7 · Tell the user when the scan finishes

### Files page

- `refetchInterval` as a function of the loaded data: while any file is
  `ready` with `virus_scan_result === null`, poll with backoff (2 s, 4 s,
  8 s, capped at 15 s). Stop as soon as none are pending. After about
  5 minutes, stop polling and show "still scanning — this is taking longer
  than usual". That string needs a new key in every enabled locale. TanStack
  already pauses polling in a hidden tab.
- Polling rather than an SSE event: the viewer is often not the uploader
  (other members see the list too), and polling needs no new fan-out. If
  scan latency turns out to matter, a `file.scan_completed` event on the
  existing notification stream is the upgrade.
- Test: mounted and focused, first response "scanning", advance fake timers,
  next response "clean" → Download enables *and* the request count stops
  rising. The review is right that the existing state-rendering tests do not
  cover this transition.

### Avatar (finding 2)

Reproduce it first, with an HTTP test: confirm, then immediately PATCH
`avatar_file_id`, and expect a 422 today. Then fix it on the server (D4:
show the avatar only once the scan is clean).
Accepting "this file will be my avatar" is valid before the scan. Serving it
is not.

- `resolve_own_avatar_key` accepts `ready` files with a pending verdict, and
  still refuses `infected`, `failed`, and wrong-owner or wrong-bucket files.
- `GET /auth/me/avatar` applies `require_clean_scan` at read time (looked up
  by key). Today it presigns the stored key without checking anything. It
  returns 409 `file_scan_pending` until the scan is clean, and the shell
  shows the initials fallback until then.
- R1 makes the served bytes immutable, so the read-time gate is the only
  remaining check, and it is sufficient.

---

## C · CI/CD: prerequisites for automatic deploy

This is a separate track from R1–R7. None of it blocks the fixes above. All
of it blocks setting `DEPLOY_TARGET` to anything other than `none`.

| # | Change | Proof |
| --- | --- | --- |
| C1 | The nine `pip install -e ".[dev]"` steps install from `requirements-dev.lock` with `--require-hashes`, then `pip install --no-deps -e .`. `pip-audit` reads the lock. | A lock/pyproject mismatch fails CI (the existing lock test already covers drift) |
| C2 | Deploy jobs `needs:` the E2E and ZAP jobs as well as `build` (and `test`/`security`, if they are not transitively required) | Workflow graph. A red `e2e-web` blocks deploy |
| C3 | The `deploy-aws` *caller* job grants `permissions: { id-token: write, contents: read }`. A reusable workflow cannot exceed its caller's grant | OIDC role assumption succeeds on a dry run |
| C4 | `deploy-preflight`: pass the env file through `env:` and write it with `printf '%s' "$PROD_ENV_FILE"`. Never interpolate `${{ }}` into script source, where a `'` or `$(` in a secret value becomes shell. Run migrations **on the target** (over SSH on Hetzner, as a one-off task on AWS/Render) rather than on the runner, so the production DB is never reachable from GitHub's network | A secret containing `'$(id)` round-trips verbatim. The migration runs on the target |
| C5 | Hetzner rollback: run when `steps.switch.outcome != 'skipped'`, which includes a failed or partial switch, and make the switch step idempotent | Failure injection below |
| C6 | Failure-injection drill on a throwaway target: a failing migration aborts before the switch; a failing smoke test rolls back; a switch that fails halfway (kill `worker` mid-`up`) rolls back | Recorded in `docs/runbooks/deploy-*.md` |

Minutes budget: add no new push-triggered workflows (see the Actions-minutes
memory note). C2 only reorders `needs:`.

The operator items still open from the 09-25 plan are unchanged: off-host
backups (`BACKUP_REMOTE`), a second copy of the recovery keys, legal and
product content, and mobile WebAuthn.

---

## Decisions (made 2026-09-29)

- **D1 Org rate budget: 5000/hour, measured afterwards.** API keys are not
  counted. See R5.
- **D2 A separate `scanner-worker` container on the box: yes.** Its memory
  limit is set from R4.5's measurement.
- **D3 Superadmins sign in again once after the R3 migration: accepted.**
  The deploy note says so.
- **D4 The avatar is shown only once its scan is clean.** The server accepts
  a pending-scan file as the avatar. `GET /auth/me/avatar` serves it only
  after a clean verdict, and the initials show until then. See R7.

## Definition of done for this plan

- Every item has behavior-level tests. R1, R2 and R3 use real
  Postgres/MinIO. R2 reads its results from a fresh session.
- A full suite run passes with coverage ≥ 85% overall and 100% on `auth/`
  and `storage/`'s touched files. Run the module-matrix legs locally for R5,
  because a route under a module toggle changes the budget surface.
- Deploy to the box with the R3 migration, the `scanner-worker` service and
  the clamd config, then run R4.5's burst measurement there.
- Update the report and the 09-25 plan status table to state what was
  verified and where, and to name anything that remains open.
- Docker must be running locally before any of this starts. Every P1 test
  needs testcontainers, and the review's own probe failed for lack of it.
