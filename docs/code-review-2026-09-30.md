# Repository review and remedy plan — 30 September 2026

Scope: the whole repository at `705f392` plus the uncommitted R1–R7 / C1–C5
work on `files-exclude-avatars-and-wordmark-link`. Three questions were
asked: is it bloated, is it secure, does it need meaningful changes — and
separately, are the tests correct and why do they take so long.

Every number below was measured on this machine on 2026-09-30 unless it
says otherwise.

## Status (updated 2026-09-30, same day)

Backend suite: **2,714 passed in 7 min 20 s**, down from 53 min 08 s, on the
same machine. `ruff`, `mypy --strict`, import-linter, the docs check,
`pip-audit` on the lock (no ignores), and the full JS lint, type-check and
test run are all clean.

| Step | Status |
| --- | --- |
| T1 One client | **Done, and deeper than planned.** The 49 committed-mode files moved to `tests/committed/`, whose `conftest.py` owns their fixtures once; both modes serve the app through `tests/_client.py::serve_app`. An autouse guard (`_no_dead_addresses`) fails any test that connects to `.env.test`'s addresses. It found four more places the planned fix would have missed: the slowapi limiter's sync Redis client, `app.core.metrics`' gauges, and a bare `TestClient` 404 test. |
| T2 One Redis container | **Done.** Only conftest starts one. `_redis_client`/`_rate_limit_storage` became `redis_client`/`rate_limit_storage`, which also deleted 26 one-line alias fixtures. |
| T3 Cheap argon2 in tests | **Done.** `ARGON2_*` settings; `.env.test` lowers them; `Settings` refuses anything below argon2-cffi's defaults outside dev (the non-dev invariants are now a rule table). Prod-shaped settings in tests come from one `tests/_prod_settings.py`. |
| T4 Migrated schema | **Done with T1.** Committed tests run on a per-test clone (~50 ms) of a template migrated through every branch. Of 910 tests, none failed on the real schema. `tests/_module_tables.py` is deleted. |
| S1 Rate limiting | **Done, differently from planned.** Moving slowapi's 129 decorator sites to `limits.aio` is its own project. The defect was the missing timeout and fail-open scattered across callers. Now one `FailOpenSlidingWindow` serves every limit: 0.25 s Redis timeout, then a 30 s stand-down so an outage costs one timeout per 30 s. Proven against a Redis that accepts and never answers. |
| S2 Backup codes | **Done.** `hmac-sha256:` digests keyed by `PASSWORD_PEPPER` (and `PASSWORD_PEPPER_OLD` during rotation); one hash per attempt. Existing Argon2 lists still verify. No migration was needed: the column holds either format. |
| S3 Retry-safe confirm | **Done.** `_promote` is idempotent. |
| S4 Infected verdict | **Done.** Verdict, audit event and notification commit together before the delete; a retry finishes an interrupted delete. |
| S5 python-jose | **Done.** joserfc for both JWT paths; python-jose, `ecdsa`, `rsa` and the CI `--ignore-vuln` are gone. joserfc does not check `exp` in `decode`, so claims go through `JWTClaimsRegistry` (mutation-checked). |
| B1 AGENTS.md | **Done.** 7,754 to 488 lines; phases in `docs/plan-archive/`, with a task index. |
| B2 Prose | **Started.** The wrong HMAC comments are fixed (in code and `.env.example`). |
| B3 Leftovers | **Done.** OpenTelemetry unpinned (>=0.64b0), `tracing.py`'s monkeypatch deleted; the 405 regression test stays. |

**Found while doing it:** syncing the venv to the lock (review C1) broke
`mypy --strict`: 58 errors. The dev venv had an older sqlmodel than the lock
(and than CI's former unpinned install). Under sqlmodel 0.0.47, fields on the
plain-class mixins were invisible to model constructors; the mixins now
subclass `SQLModel`, two JSONB fields got `default_factory`, and 52 stale
`type: ignore`s went.

**GDPR purge was failing (found 2026-10-01).** Purge deleted from a
hand-written list of tables that had fallen behind the schema: `Notification`,
`PushToken`, `StoredFile`, `WebAuthnCredential`, `EmailChangeToken`,
`Invitation` and `WebhookEndpoint` all reference `user.id` and none was
handled, so the final `DELETE FROM "user"` hit a foreign key (reproduced:
`invitation_invited_by_fkey`) and the erasure never completed. Purge now
works from one `USER_REFERENCES` table (delete or null, per module), deletes
the user's stored objects before their rows, and a test sweeps
`SQLModel.metadata` so a new foreign key to `user.id` fails the build until it
is listed. Export gained the notifications and stored-file metadata T-037
asked for, and no longer includes refresh-token hashes.

**Deliberately not done:** splitting `app/modules/auth/service.py` (1,627
lines). It would move code, not remove any; worth it the day a change there
is hard to make, not before.

---

## 1. Verdicts

| Question | Verdict |
| --- | --- |
| **Secure?** | **Broadly yes.** No raw SQL built from strings, no `verify=False`, no `shell=True`/pickle/unsafe YAML, and the review fixes (R1–R7, C1–C5) close the known holes. Three issues remain worth fixing: a synchronous, timeout-less Redis call on every authenticated request (§3.2), backup codes that make a wrong 2FA code cost ~0.5 s of argon2 (§3.3), and session JWTs on the unmaintained python-jose (§3.6). |
| **Bloated?** | **Yes, in prose and in tests — not in features.** AGENTS.md is 7,754 lines loaded into every agent session; backend code carries 0.49 lines of comment/docstring per line of code; tests are 51.6k lines against 31.1k of app code, with the same fixtures copied into dozens of files. The feature set itself is coherent: every module is toggleable and used. |
| **Meaningful modifications needed?** | **Yes, but none are rewrites.** Two correctness defects (§3.4, §3.5), the three security items above, and — by far the largest single win — a test-infrastructure fix that makes the suite roughly an order of magnitude faster on Windows (§2). |

Size, for reference:

| Area | Lines |
| --- | --- |
| backend/app (Python) | 31,079 (18,305 code · 5,488 docstring · 3,516 comment) |
| backend/tests (Python, 161 test files) | 51,586 |
| apps/web/src | 26,181 |
| apps/mobile | 8,257 |
| packages (excluding generated/dist) | 11,811 |
| docs (Markdown) | 11,961 |
| AGENTS.md | 7,754 |

---

## 2. The tests

### 2.1 Why the backend suite takes 53 minutes

The last full run: **2,682 passed, 53 min 08 s**. Four causes, in order of
cost.

**(a) Twelve test files wait on ports nothing listens on — the dominant
cause.** These files each define their own `client` fixture that enters the
real app lifespan:

`test_auth_router`, `test_auth_oauth`, `test_auth_totp`,
`test_auth_webauthn`, `test_billing_router`, `test_billing_invoices_router`,
`test_billing_webhooks`, `test_gdpr_router`, `test_notifications_router`,
`test_notifications_sse`, `test_storage_router`, `test_tenancy_router`.

The lifespan sets `app.state.redis` from `REDIS_URL`, which `.env.test` sets
to `localhost:6379` — not the test container. The maintenance middleware
reads product settings through that client on every request; the read fails,
the cache falls back to Postgres through `settings_session_scope`, which in
these files points at `localhost:5432` — also dead. The middleware then fails
open (correctly) and the request proceeds. On Linux a refused connection is
instant, which is why CI never noticed; **on Windows each refused connect
retries for seconds**, and a request pays two of them.

Measured on `test_tenancy_router.py` (34 tests):

| Change | Wall time |
| --- | --- |
| As committed | **343.9 s** (tests take 8.3 s, 16.6 s, 41.4 s — multiples of the two waits) |
| + `app.state.redis = redis_client` | 181.1 s |
| + the conftest `product_settings_session` fixture | **16.2 s** |

The shared `client` in `tests/conftest.py` already does both; the twelve
private copies predate it or were written beside it.

**(b) Twenty extra Redis containers.** Twenty files override the
session-scoped `redis_container` with a module-scoped one of their own, so
twenty containers start and stop per run (a few seconds each on Docker
Desktop). Postgres was consolidated onto one shared server earlier; Redis was
not.

**(c) Production argon2 cost in tests.** `PasswordHasher()` defaults to
64 MiB / time_cost 3, **47 ms per hash** here. 79 direct `hash_password`
calls plus every register/login/reset/2FA flow pay it; enabling 2FA hashes
ten backup codes (≈0.5 s per enrollment).

**(d) 52 private `test_engine` fixtures** run `create_all`/`drop_all` per
module against hand-picked table lists. Cheap individually, but it adds up,
and it is also a correctness problem (§2.2).

Expected result after §4 step T1–T3: the twelve files drop from minutes to
seconds each. A realistic target for the full suite on this machine is
**10–15 minutes**, dominated by genuinely container-bound tests (pgbouncer,
read-replica, MinIO, SSE).

### 2.2 Are the tests correct?

Mostly yes — the suite is thorough and many tests are mutation-checked. Four
weaknesses:

1. **61 files test against a schema that is not the real one.** They build a
   subset with `SQLModel.metadata.create_all(tables=[...])` instead of
   running migrations. Anything that lives only in a migration — server
   defaults (`session.amr DEFAULT '[]'`), CHECK and unique constraints,
   `ON DELETE CASCADE`, partial indexes — is invisible to them.
2. **32 of those omit `audit_event`,** so every audit write in those flows
   fails and is swallowed by the never-raise audit contract. The log shows it
   (`audit.write_failed: action=user.login_success` in `test_auth_totp`); no
   test does. An audit regression on login or 2FA would pass green.
3. **The twelve files in §2.1(a) never exercise the maintenance middleware's
   real path** — every request takes the fail-open branch. The maintenance
   tests themselves (`test_maintenance_mode.py`) are correct; the point is
   that the other files are not testing the stack they appear to.
4. **Harness gap closed, but only just.** Until 2026-09-29 the HTTP client
   never rolled back on errors, which hid four security bugs (review R2).
   Fixed in `tests/_sessions.py`; the twelve private clients now use it, but
   it is still twelve copies to keep in step.

The frontend suites (web 82 files, mobile 25, marketing 13, packages) are
healthy and fast; nothing to remedy there.

---

## 3. Findings

### 3.1 Test infrastructure — see §2 (highest leverage)

### 3.2 Synchronous, timeout-less Redis on the request path — `app/middleware/budgets.py:70`

The shared user/org budgets (R5) call `limits`' **synchronous** strategy on
the event loop, twice per authenticated request, on top of slowapi's own
synchronous hits. The storage URI carries no socket timeout.

*Failure:* a Redis that stops answering without refusing (partition,
failover, overload) blocks `strategy.hit` indefinitely, and with it every
request on that worker. The `except Exception` fail-open never runs, because
nothing raises. The design intent — "a Redis outage must not become a total
outage" — holds for refusals and not for hangs, which are the more common
production failure.

### 3.3 Backup codes hashed with argon2 — `app/modules/auth/service.py:1316`

Backup codes are 40-bit random values; each is stored as a 64 MiB argon2
hash and `_consume_backup_code` checks a submitted code against up to ten of
them. A wrong code costs ~0.5 s of CPU and repeated 64 MiB allocations in the
thread pool, per request — a cheap amplifier against a backend whose
`mem_limit` is 768m. A high-entropy secret needs no KDF (the argument T-310
already made for API keys).

### 3.4 `confirm_upload` is not retry-safe — `app/modules/storage/service.py:311`

`_promote` copies `incoming/<key>` to `<key>` and deletes the incoming copy
*before* the sniff, the scan enqueue and the commit. Any transient failure
after that point leaves the row `pending`; the client's retry finds no
incoming object and gets 422 `file_not_uploaded`, although the bytes sit at
the served key. The user must re-upload, and the orphan holds quota until the
reservation expires.

### 3.5 Infected verdict can be lost — `app/workers/jobs/virus_scan.py:292`

`_record_infected` deletes the object, then writes the verdict. If that
write fails, ARQ retries; the retry cannot read the deleted object and
records `failed` / `file.scan_failed`. The malware verdict, its audit event
and the uploader's notification are lost. Separately, `_record_failure` lacks
the `deleted_at IS NULL` guard `_record_verdict` has.

### 3.6 Session JWTs on python-jose — `app/modules/auth/utils.py:19`

python-jose is unmaintained, and its `ecdsa` dependency has an advisory with
no fixed version (PYSEC-2026-1325) that CI ignores permanently. joserfc is
already a dependency (OIDC, T-331). Moving the two JWT functions removes both
packages and the ignore.

### 3.7 Bloat — AGENTS.md and inline prose

- **AGENTS.md (7,754 lines)** is read into every agent session. Its binding
  content — §0 laws, §1 stack, §2 layout, §7 checklist — is a few hundred
  lines; the rest is finished task specs and long completion notes.
- **Prose in code.** 0.49 prose lines per code line across `backend/app`;
  `core/config.py` has 341 prose lines to 176 of code, `core/db.py` 150 to
  132. Much of it is history — ticket numbers, dates, past incidents — that
  belongs in ADRs, runbooks or commit messages. History in comments goes
  stale: `auth/utils.py` says the pepper is applied as an HMAC-SHA256; the
  code is a plain `sha256(f"{pepper}:{plain}")` (harmless as a pre-hash, but
  the comment is wrong).
- **Tests duplicate fixtures:** `test_engine` ×52, `session_factory` ×23,
  `redis_container` ×20, `redis_client` ×18, `client` / `_redis_backed_storage`
  / `get_session_override` ×12 each.

### 3.8 Smaller items noted

- `app/middleware/rate_limit.py`: a docstring still says "fixed-window
  strategy" after the switch to sliding-window-counter.
- The OpenTelemetry exact pins and `tracing.py`'s
  `_patch_fastapi_partial_match_bug` existed only because semgrep sat in the
  project environment; since C1 they can be relaxed.

---

## 4. Remedy plan

Ordered by value per hour. Each step is independently shippable; none needs
a migration except where stated.

### Tests first (T) — makes every later step cheaper to verify

**T1 · One `client` fixture (fixes §2.1a). ~½ day.**
1. Make the conftest `client` a factory-capable fixture: parameters for the
   session override (committed engine vs savepoint) and for Redis, so every
   file's needs are expressible. Keep `rolling_back()` and the
   `app.state.redis` + `product_settings_session` wiring inside it.
2. Delete the twelve private `client` fixtures; each file keeps only the
   fixtures genuinely specific to it.
3. Belt and braces: add an autouse fixture that fails the test if anything
   connects to the `.env.test` addresses (`localhost:6379`, `:5432`) — e.g.
   by wrapping `redis.asyncio.Redis.from_url` and asserting on the URL.
   Changing those addresses would only move the problem; the silent
   fallback to them is what hid this, so make it loud.
4. *Verify:* `test_tenancy_router.py` < 20 s; full suite time recorded
   before and after in the PR.

**T2 · One Redis container. ~2 hours.** Delete the twenty module-scoped
`redis_container` overrides; the session-scoped one plus `FLUSHDB` in a
per-test (or per-module) fixture gives the same isolation. Keep private
containers only where a test needs its own server (pgbouncer, read-replica,
MinIO).

**T3 · Cheap argon2 in tests. ~2 hours.** Add
`ARGON2_TIME_COST` / `ARGON2_MEMORY_KIB` / `ARGON2_PARALLELISM` settings,
defaulting to today's values; build the hasher from settings; set
`1 / 8 / 1` in `.env.test`; refuse anything below the defaults when
`ENVIRONMENT` is not `dev`/`test` (same pattern as the other startup
invariants). Existing hashes keep verifying — argon2 encodes its parameters
in the hash.

**T4 · Test against the migrated schema. ~1–2 days, incremental.**
1. Replace the 52 private `test_engine` fixtures with the conftest `engine`
   (migrated once per session) plus the savepoint/`rolling_back` session.
   Files that genuinely need an empty schema keep `fresh_database`.
2. Do it file by file; each conversion must stay green. Expect a few tests
   to start failing on real constraints — those are findings, not
   regressions.
3. Add a guard: an autouse fixture that turns `audit.write_failed` into a
   test failure unless the test opts out explicitly. That makes §2.2 item 2
   impossible to reintroduce.

### Correctness and security (S)

**S1 · Async rate limiting with a timeout (§3.2). ~1 day.** Move both
slowapi and the budgets onto `limits.aio` (`MovingWindow`/`SlidingWindowCounter`
async strategies over an async Redis storage) with explicit
`socket_timeout` / `socket_connect_timeout` (≈200 ms). Keep fail-open, now
covering timeouts too. *Verify:* a test with a Redis that accepts and never
answers (a bare TCP listener) — requests must complete within the timeout,
not hang.

**S2 · Backup codes without a KDF (§3.3). ~½ day + migration.** Store
`HMAC-SHA256(pepper, code)` per code (a JSON list, or better a small
`backup_code` table with `(user_id, digest)` unique). Consume with a single
conditional `UPDATE … WHERE digest = ? AND used_at IS NULL` — one lookup,
atomic, no loop. Migration: existing argon2 lists keep working through a
fallback path until each user regenerates codes or a background job
re-issues them; do not rehash (the plaintext is not available).

**S3 · Retry-safe confirm (§3.4). ~½ day.** In `_promote`: if the incoming
copy is absent **and** the served key exists with a size within tolerance,
treat the file as already promoted and continue. Delete the incoming copy
only after the commit (or leave it to the `incoming/` lifecycle rule, which
already exists for this). *Verify:* MinIO test that fails the sniff once
with a transient error and asserts the retry succeeds.

**S4 · Keep the infected verdict (§3.5). ~2 hours.** Write the verdict (and
the audit event) first, commit, then delete every copy; if deletion fails,
the row already says `infected` and the download gate refuses it. Add the
`deleted_at IS NULL` guard to `_record_failure`.

**S5 · Retire python-jose (§3.6). ~1 day.** Port `create_access_token` /
`verify_access_token` to joserfc with RS256 pinned; keep claim names so
existing tokens verify across the deploy. Remove python-jose, `ecdsa`, the
`--ignore-vuln PYSEC-2026-1325` flag and `types-python-jose`. *Verify:* the
existing JWT tests (wrong algorithm, expiry, `none` alg) pass unchanged.

### Bloat (B)

**B1 · Split AGENTS.md. ~½ day.** Keep §0, §1, §2, §3 and §7 in AGENTS.md
(target < 600 lines). Move every completed task spec, with its notes, to
`docs/plan-archive/phase-N.md`; leave a one-line index per task. Open tasks
stay in AGENTS.md.

**B2 · Prose diet in code, opportunistically. Ongoing.** Rule for review:
a comment says *why the code is this way now*; history (tickets, dates,
incidents) goes to the ADR, runbook or commit. Start with the densest
modules — `core/config.py`, `core/db.py`, `auth/utils.py` — and fix the
wrong pepper comment while there. No big-bang rewrite: it would be a large
diff with no behaviour change and real merge cost.

**B3 · Tidy leftovers. ~1 hour.** The stale "fixed-window" docstring; relax
the OpenTelemetry pins and remove `_patch_fastapi_partial_match_bug` now
that semgrep is out of the environment (separate PR — it changes runtime
instrumentation).

### Suggested order and grouping

| PR | Steps | Why this order |
| --- | --- | --- |
| 1 | T1, T2, T3 | Makes every following PR ~5× cheaper to verify locally |
| 2 | S3, S4 | Small, contained correctness fixes in storage |
| 3 | S1 | Touches every request; wants the faster suite behind it |
| 4 | S2 | Needs a migration and a compatibility path |
| 5 | S5 | Auth-critical; its own review |
| 6 | T4 | Incremental; can run in parallel with 2–5 |
| 7 | B1, B3 | Docs and hygiene |
| — | B2 | Ongoing, as modules are touched |
