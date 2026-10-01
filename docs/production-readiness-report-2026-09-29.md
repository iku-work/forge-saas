# Production-readiness remediation — what was done and why

**Closes:** [`repository-audit-2026-09-24.md`](repository-audit-2026-09-24.md)
and [`production-cicd-audit-2026-09-24.md`](production-cicd-audit-2026-09-24.md),
worked through [`production-readiness-plan-2026-09-25.md`](production-readiness-plan-2026-09-25.md).

**Span:** `4602e7c` (the plan) … `b8ca822`. 21 commits.
**Deployed:** 2026-09-29, live single-box deployment, no migration.

This is the reasoning record. The plan says what was going to be done and the
commit messages say what each change did; this says **why each thing was
decided the way it was**, including the places where the plan was overruled
and the places where a fix turned out to be smaller or larger than it looked.

Read the plan's status table first if you only want state. Read this if you
are about to change one of these things and want to know what it cost to get
here.

> **Correction, 2026-09-29.** A follow-up review
> ([`production-readiness-review-2026-09-29.md`](production-readiness-review-2026-09-29.md))
> found that this report overstates three closures: file safety (§2),
> refresh-token theft containment (§4), and privileged-session MFA (§3). §5
> is only partially resolved. Each section now carries a note saying what is
> still open. The fixes are tracked in
> [`production-readiness-plan-2026-09-29.md`](production-readiness-plan-2026-09-29.md).

---

## Contents

| § | Stage | What it closed |
|---|---|---|
| [1](#1-secrets-out-of-the-edge) | 0.1 | PROD-02, PROD-03 |
| [2](#2-the-virus-scanner-and-the-download-gate) | 0.3, 1.1–1.4 | PROD-01, findings 1 + 14 |
| [3](#3-one-session-issuance-policy) | 2.1–2.3 | Finding 2 |
| [4](#4-single-use-that-means-single-use) | 3.1 | Finding 4 |
| [5](#5-rate-limiting-that-knows-who-you-are) | 3.2 | Finding 5 |
| [6](#6-ssrf-connect-to-the-address-you-checked) | 4.2 | Finding 3 |
| [7](#7-storage-quota-as-a-reservation) | 4.1 | Finding 8 |
| [8](#8-interfaces-that-tell-the-truth) | 5.1–5.3, 6 | Findings 11 + 12, UX-01, UX-02 |
| [9](#9-cursors-that-do-not-silently-restart) | 5.4 | Finding 13 |
| [10](#10-operational-assurance) | 7 | PROD-04, PROD-05, PROD-06 |
| [11](#11-reproducible-builds) | 8 | Finding 16 |
| [12](#12-test-infrastructure) | — | (not in the plan) |
| [13](#13-what-only-deploying-could-find) | — | Three defects |
| [14](#14-what-is-still-open) | — | Four items |

---

## 1. Secrets out of the edge

**The finding.** `docker-compose.prod.yml` gave Caddy `env_file: ./backend/.env`.
That file holds `JWT_PRIVATE_KEY`, `ENCRYPTION_KEY`, `DATABASE_URL`,
`STRIPE_SECRET_KEY` and the rest. The one container the internet can reach
could forge tokens, read the database and decrypt columns — none of which it
needs in order to terminate TLS and proxy.

**Decision: an explicit allowlist, not a filtered env file.** `env_file`
cannot select keys, so the allowlist has to be an `environment:` block —
`DOMAIN`, `MARKETING_DOMAIN`, `STORAGE_DOMAIN`, `CADDY_TRUSTED_PROXIES`, all
hostnames or CIDRs.

That forced a second decision. Compose interpolates `${...}` only from the
shell and from the compose project's own `.env`, never from `backend/.env`,
so those four have to exist in the root `.env` too. `DOMAIN` is
`${DOMAIN:?...}` with no default: **compose refuses to start rather than
booting an edge on a fallback hostname.** Empty is not the same as unset —
compose passes a set-but-empty variable through as `""`, and Caddy then uses
that instead of its `{$VAR:default}` fallback, producing a site block with no
address that stops the *whole* edge.

**Verification.** Two tests hold the allowlist in both directions: no
`env_file` on the edge, and no name in the Caddyfiles that is missing from
the list. Rendered with `docker compose config` both ways rather than
reasoned about.

---

## 2. The virus scanner and the download gate

**The finding, in three parts that are each inert alone.** The download gate
was vacuous while the scanner called everything clean; the scanner was
pointless while failed files stayed downloadable; and the scanner could not
look anything up while the digest column held an ETag. They had to land
together.

**Downloads were signed without reference to any scan.** `get_download_url`
checked tenancy and signed the object. `confirm_upload` sets `"ready"` and
*then* enqueues the scan, so every file was downloadable during the window
before a verdict existed, and stayed downloadable if the scan came back
failed.

**Decision: one gate module, two call sites.** Both signing surfaces go
through `app/modules/storage/scan_gate.py` so they cannot drift. The second
surface is the one the audit did not name and the more exposed of the two:
`resolve_own_avatar_key`. An avatar has no org, so it never passes through
`StorageService` and never met any of its checks — and an unscanned avatar is
still an attacker-supplied file served to a browser with an image content
type.

**Decision: refuse with 409 and four distinct message keys, not 404.** The
caller has already proved it may see the file. Hiding it would turn a
transient state ("still scanning") into an indistinguishable permanent one
("gone"), and the user cannot act on that.

**Decision: the scanner fails closed.** `VIRUS_SCANNER=virustotal` returned
*clean* for a hash VirusTotal had never seen — which is every file this
product has ever uploaded. Unknown now raises, and `virustotal` is refused
outside dev at startup. This makes the build a **breaking change** for any
deployment still on that setting, deliberately: the alternative is booting
something that certifies unknown files as safe.

**Decision: compute the digest from the bytes the scanner reads.** The column
was named `checksum_sha256` and held an ETag — 32 hex characters, an MD5, and
for a multipart upload not even that. Production held exactly such a value.
Computing it in the scan worker binds the digest to the object version the
verdict was reached on.

> **Correction (review R1).** The digest binds the *verdict* to the scanned
> bytes. It does not bind *downloads* to them. The upload form stays valid
> for 600 s and targets the same key that downloads read, so a clean file
> can be overwritten after its scan and the replacement is served. The fix
> is promotion to a server-only key at confirm time (plan R1).

**Sizing clamd was measured, not estimated.** The runbook's "~1.5 GB RSS, ~1 GB
disk" was wrong in both directions: real numbers are 955 MiB resident, 167 MB
of signatures, 149 MB image. But steady state is the wrong thing to size for
— `freshclam` runs unattended several times a day and clamd's default
`ConcurrentDatabaseReload` loads the new database before releasing the old,
roughly doubling usage. On a 3.8 GB box that peak picks a victim for the OOM
killer, and the victim is not necessarily clamd.
`CLAMD_CONF_ConcurrentDatabaseReload: "no"` swaps in place instead; scans
block for a few seconds and nobody notices.

**The physical deletion job did not exist.** `delete_file` had been enqueuing
`"hard_delete_stored_file"` by name since T-112 against a worker that had no
function by that name. ARQ resolves names at run time, so this was not an
import error — it logged and moved on, and every deletion since had left its
object in the bucket while the row's quota contribution disappeared. Written,
plus a reconciliation sweep (a deferred ARQ job is one Redis entry seven days
out; anything that loses it loses the deletion silently), plus
`tests/test_worker_registration.py`, an AST sweep asserting every string
passed to `enqueue_job` resolves against `WorkerSettings.functions`.

---

## 3. One session-issuance policy

**The finding.** Four ways into this product, four separate decisions about
whether an enrolled second factor gets checked, and three decided wrong.

- **Magic link** and **social OAuth** called `_create_session` directly. An
  account with TOTP or a security key enrolled could be signed into with
  neither — possession of an inbox, or of a Google session, was the whole
  requirement.
- **SSO** tested `user.totp_enabled` alone, so a user whose only second factor
  was a security key was never challenged.
- **Password** login was the one that got it right.

**Decision: hoist the correct one, do not patch the other three.** Password
login's logic moved into `_second_factor_challenge`, which the other three now
consult. Four independent decisions about the same question is the bug; three
fixes would leave four decisions.

**The social case had been argued for in a comment** — for Google or GitHub
"the account *is* the factor". That is a claim about the provider, and it is
not ours to make on the user's behalf: somebody who enrolled a second factor
*here* asked for it to be checked *here*. Accepting a provider's own MFA is a
legitimate design, but the way to do it is to read its `amr` claim and say so,
which is what the SSO path already does.

**Decision: `sid` on the access token.** Logout, logout-all, device
revocation, password reset and theft detection all revoked the *refresh* half
of a session. Access tokens already issued kept working until they expired, so
"sign out all devices" was a promise about the next fifteen minutes rather
than containment of a stolen bearer token. An access token now carries its
session id, and `resolve_user_from_token` refuses one whose session is no
longer usable. Tokens *without* `sid` are still accepted — the check applies
only when the claim is present — so deploying it does not sign anybody out.

**`amr` was deferred, and the reasoning is worth keeping.** Its purpose is
letting `require_superadmin` check the session *completed* a second factor
rather than that one is *enrolled*. After the unification, for an enrolled
superadmin "enrolled" already implies "was challenged", so `amr` turns an
inference into a proof — real defence in depth, not an open hole — and it
cannot be done without a migration, because it has to survive refresh
rotation. Pick it up with the next migration that ships for another reason.

> **Correction (review R3).** "Enrolled implies challenged" holds only for
> sessions created *after* enrollment. A password-only session opened before
> the user enrolls a second factor stays valid, and it passes
> `require_superadmin`, because that check reads the account's enrollment,
> not the session's factors. So `amr` is not optional defence in depth. It is
> the fix (plan R3).

---

## 4. Single-use that means single-use

**The finding.** Four kinds of single-use credential were spent with a
read-modify-write: `SELECT` the row, decide in Python that it was unused,
write a few statements later. Two requests arriving together both passed.

- **Single-use tokens** (email verification, password reset, magic link, email
  change). One magic link signed in twice; one reset link reset a password
  twice.
- **Refresh rotation.** Two successors from one token, with the theft detector
  silent throughout, because neither request ever saw a revoked row.
- **TOTP replay tracking.** Both requests read the same `last_consumed_step`
  and both concluded the code was fresh.
- **Backup codes**, the worst of the four. The list is one JSON column, so
  spending a code rewrites all of it — and last-writer-wins does not merely
  double-spend one code, it **puts back** a code the loser had removed.

**Decision: make the database arbitrate, per credential type.** Tokens get a
conditional `UPDATE` returning whether it won; the refresh rotation likewise;
the TOTP watermark advances by a strictly-monotonic `UPDATE`; backup codes use
a compare-and-swap against the exact list that was read.

**Decision: losing the refresh race is treated as theft.** It is either a
client racing itself or exactly the reuse the detector exists for, and there
is no way to tell from inside. Treating it as theft is the safe reading of an
ambiguous event.

Each of the four has a two-connection race test, because a single-connection
test cannot fail here.

> **Correction (review R2).** The atomic rotation prevents two successors
> from one token. It does not make the theft *response* stick. The
> revoke-all runs, then `AuthenticationError` is raised, and `get_session`
> rolls the revocation back. The HTTP test harness overrides `get_session`
> with a function that never rolls back, which is why no test caught this.
> The login lockout counter and WebAuthn clone detection fail the same way
> (plan R2).

---

## 5. Rate limiting that knows who you are

**The finding.** `_key_func` returned `user:{id}` when `request.state.user_id`
was set, and **nothing in the application ever set it**. The branch was dead,
every authenticated request fell through to the source address, and the
per-user limit §0.6 describes did not exist. Two people behind one office NAT
shared a budget; one person on a laptop and a phone got two.

**Why the obvious fix does not work**, which is probably why it stayed broken:
`SlowAPIMiddleware` runs during the middleware phase, before FastAPI has
solved a single dependency, so anything `get_current_user` stashes on
`request.state` arrives too late to key on.

**Decision: read the Authorization header directly, and treat it as identity
for throttling only.** It has no ordering relationship to anything. A token
that fails to verify returns `None` and falls back to the address, exactly as
an anonymous request does — authentication is still `get_current_user`'s job,
done again afterwards with the blocklist and session checks this has no
business repeating. An API key is not a JWT, so it lands on the fallback here
and is metered by its own per-key limiter, which knows which key it is.

> **Correction (review R5): partially resolved.** The identity is fixed, but
> `api_limit` is still one budget *per endpoint*. That means no shared
> per-user ceiling and no per-org ceiling, and the limiter still uses a
> fixed window. Plan R5 adds shared user and org budgets (org: 5000/hour,
> then measured).

---

## 6. SSRF: connect to the address you checked

Neither affected module is enabled in production, so this is template work
rather than an incident. Two halves of very different size.

**The token endpoint had no guard at all.** `discovery_url` was checked and so
was `jwks_uri`, but the `token_endpoint` *inside* the document those return
went straight to authlib. It is the worst of the three to miss: the document
comes from a URL an organisation admin supplies, and that request carries the
client secret in its body — so an admin pointing discovery at a document
naming an internal address had this server POST the secret there, before the
ID token it gets back is validated at all. Now guarded, with
`require_https=True`, because plaintext hands the same secret to anything on
the path.

**Decision: validating a URL and handing the string to httpx is not a guard.**
httpx resolves the hostname a second time, so an attacker controlling DNS
answers publicly on the first lookup and privately on the second. The fix is a
pinned transport: `validate_outbound_url` now returns a `SafeDestination`
carrying the resolved address, and `pinned_transport()` connects to *that*
address while preserving `Host` and TLS SNI.

**Decision: the pinning raises if it cannot attach.** It reaches into
`transport._pool._network_backend`, which is private API. If a future httpx
moves it, the guard must fail loudly rather than silently reverting to
unpinned connections — a security control that degrades quietly is worse than
one that is absent.

---

## 7. Storage quota as a reservation

**The finding, in three parts.** `total_bytes_for_org` counted only
`status == "ready"`, so N pending presigns each passed against the same free
space and then all confirmed. The 60-second Redis cache was invalidated on
confirm and delete but not on presign. And `size_bytes` had no ceiling, while
with billing disabled `_check_quota` was skipped entirely — so a non-billing
product had no limit at all.

**Decision: a pending upload is a reservation.** `app/modules/storage/quota.py`
counts `ready` rows plus `pending` rows inside a live window. This was not a
theoretical race: the web dropzone uploads three files at a time by default,
so it was hit on every multi-file upload.

**Decision: the window is an hour, not the ten-minute signing TTL.** A
presigned URL's expiry bounds when an upload may *start*, not when it must
finish — a PUT begun at 9:59 can still be streaming an hour later and S3 will
accept it. Expiring the reservation at ten minutes would uncount bytes that
are actively arriving.

**Decision: a per-org advisory transaction lock, not optimistic retry.**
Counting reservations fixes a presign that ignores the ones before it; it does
not fix two presigns running at the same instant, both reading the total
before either has written its row. `pg_advisory_xact_lock` is held until
commit or rollback — including the rollback `get_session` performs when a
request raises — so there is nothing to release and no table to contend on.

**Decision: delete the Redis cache rather than invalidate it on presign.** The
plan said to add the missing invalidation. But the quota gate was the cache's
*only reader*, so the cache bought nothing and cost a sixty-second window in
which every presign in the fleet could be authorised against already-spent
space. Caching a security gate is the hole, not the optimisation. A test that
asserted the stale-read behaviour went with it — it documented the bug.

**Decision: `MAX_UPLOAD_BYTES` is not negotiable by module config.** The
plan's `max_upload_mb` is a *product* limit and only exists where there is a
plan to read it from. A template that lets someone fill its disk the moment
billing is switched off is a template bug, not a product decision.

**Decision: expiry is a scheduled job, and confirm refuses a lapsed
reservation.** Reservations that never expire turn a correctness fix into a
denial of service — a client that closes the tab would hold its org's
allowance forever. The hourly job retires them; a confirm arriving after the
window but before the job refuses, because those bytes have already stopped
counting and letting them back in is the original finding by a slower route.

---

## 8. Interfaces that tell the truth

Four findings with one shape: the UI asserted something the backend would not
honour.

**Mobile MFA was a dead end** (finding 11). `apps/mobile/src/lib/auth.ts`
threw `TwoFactorRequiredError` and the login screen caught it as an ordinary
form error — so **enrolling a second factor locked you out of the mobile app
entirely**. Built the challenge screen; TOTP and backup codes share one field
because the backend tries both against it, and validating for six digits
client-side would reject the fallback from the person most likely to need it.

> A WebAuthn-only account still cannot finish on mobile. React Native has no
> equivalent to `navigator.credentials` without a native passkey module and a
> custom dev build. **Decision: say so on the screen** rather than offer a
> button that cannot work or leave someone typing codes they do not have.

**The file UI offered actions that 403 and 409** (finding 12). Upload and
delete are gated on `files:manage`; download is disabled with a stated reason
when the scan has not cleared; both failure paths are surfaced instead of
`void`-ed.

> **Decision: tighten `StoredFilePublic.status` from `str` to its `Literal`.**
> The generated client typed it `string`, so no frontend could handle it
> exhaustively and a page deciding what to render per status had no compiler
> help. Widening type information is not a breaking change — every member is
> still assignable to the `string` a consumer may have stored it in.
>
> **Decision: "ready" is not one state.** `status` goes to `ready` when the
> upload is confirmed and the scan runs *after*, so the UI shows a separate
> **Scanning** state. Otherwise a fresh upload reads "Ready" and cannot be
> downloaded — which is exactly the mismatch this work exists to stop showing.

**The login page offered providers that do not exist** (UX-01). Google and
GitHub were rendered from `modules.oauth` alone, and neither is configured on
a fresh deployment of this template — two buttons that could only ever 404.
`/config` now reports `oauth_providers`, and the page renders from that.

> **Decision: a list, not a flag.** The module being on says the *routes*
> exist, not that any provider will answer. Those are different questions
> whose answers coincide only by accident. Empty when the module is off, so
> clients render from the list alone.

**Registration asserted acceptance of terms it did not show** (UX-02). The
terms document says creating an account constitutes acceptance; the signup
screen linked to neither it nor the privacy policy. `/config` reports
`marketing_url` — those pages are served on a different host, so the app can
neither build a relative link nor guess the name.

> **Decision: `Trans`, not string concatenation.** The sentence has two inline
> links, and splitting it into fragments fixes English word order for every
> language. This is the only use of `Trans` in the app, for that reason.
>
> **Decision: no link at all when the marketing module is off.** Those pages
> do not exist anywhere in that product, and a dead link to a policy is worse
> than none — it looks like the document exists. That leaves such a product
> asserting acceptance of terms it does not publish, which is a content
> decision and is written down as one.

---

## 9. Cursors that do not silently restart

**The finding.** Every cursor implementation resolved its anchor and, if it
could not, **dropped the `WHERE` clause entirely** — which does not mean "no
more pages", it means *page one*. An infinite list appends that, so the user
scrolls and sees the first twenty rows again and reads it as the product
losing their data. Eleven sites across eight files, all the same shape.

**The plan offered two routes: a signed opaque cursor, or an explicit
invalid-cursor error. Decision: neither, quite — split the case in two.**

The common case is not an invalid cursor at all. It is a perfectly good
anchor that happens to be soft-deleted: `BaseRepository.get()` filters
`deleted_at IS NULL`, which is right for a lookup and wrong for a cursor,
because the anchor is read only for its `(created_at, id)` and that position
is just as valid after the row is deleted. Someone deleting a row you are
paging past is an ordinary Tuesday.

So anchors now resolve *ignoring* `deleted_at`, and only a genuinely
unplaceable cursor — hard-deleted, another tenant's, forged — raises
`InvalidCursorError` (422).

**Why this beat the opaque cursor.** The wire format does not change, so no
client regeneration, no published-contract change (T-314), and no blast radius
across every paginated surface. The signed opaque cursor remains strictly
better — it would also survive a *hard*-deleted anchor — but it is now a
small isolated follow-up rather than a prerequisite for closing the finding.

**The cross-tenant comments were right and their conclusion was wrong.**
Several sites carried comments explaining that another org's anchor is
"treated as no cursor" rather than leaking its position. The non-leak is
correct and is preserved. Answering with page one and a 200 is not: it is
indistinguishable from a successful continuation.

---

## 10. Operational assurance

### Worker liveness (PROD-06)

The worker had **no healthcheck at all**, so a wedged one looked exactly like
a working one — and everything slow in this product runs there: email, virus
scanning, GDPR deletion, webhook delivery.

**Decision: `arq --check`, not an HTTP probe.** It reads the heartbeat the
worker writes from its main loop, which is a stronger claim than "the process
is up" — a job that blocks that loop stops the heartbeat, which is precisely
the failure a liveness probe is for.

**Decision: cut `health_check_interval` from arq's default.** That default is
3600 seconds, and the key's TTL is the interval plus one — so with it, a
wedged worker would have looked healthy for the better part of an hour and the
probe would have been decorative. Set to 30.

Two alerts, because they catch different failures: `JobQueueNotDraining`
(work arriving, nothing consuming) and `WorkerDown` (the worker died with an
empty queue — depth stays at zero, every dashboard looks calm, and the first
symptom is a customer asking where their email went).

### Disk and logs (PROD-04)

Docker's `json-file` driver keeps every byte a container has ever written,
forever. On a single-box deployment that is a disk-full incident with a date
on it, and the first service to notice is Postgres, which cannot write a WAL
segment. Every service now anchors a shared `x-logging` block.

**Decision: memory caps are runaway guards, not reservations,** and the sum is
allowed to exceed physical RAM — it does, at about 5 GB of caps on a 3.8 GB
box with clamav's profile enabled. Docker does not reserve; it refuses to let
a service exceed. The caps are set well above every service's measured steady
state, so a cap firing is a bug report rather than a capacity signal. Capacity
is the disk/memory alert's job. (Measured on the live box: backend 121 MB
against 768, worker 46 against 768, pgbouncer 4.6 against 64.)

**Decision: add `node-exporter` rather than cron a `df`.** `DiskSpaceLow`
needs a metric, and nothing else in the stack can see the host's filesystems —
a container's view stops at its own layer.

### Backups (PROD-05)

The audit found one dump, four days old, mode 0664, on the same disk as the
database, and **no restore ever executed**. The runbook said an operator "is
expected to wire up their own backup schedule" — a fair thing to say about
retention policy and a bad thing to say about whether backups happen at all.

**Decision: `pg_dump -Fc`, not a volume tarball.** Custom format is consistent
against a running database, compresses, and restores selectively. Tarring a
live volume produces a file that restores into a corrupt cluster often enough
to be worthless and rarely enough that nobody notices until they need it.

**Decision: age with a *public* recipient, not a passphrase.** The box
encrypts and cannot decrypt, so a compromised host cannot read its own backup
history. The private key lives wherever the operator keeps `ENCRYPTION_KEY`'s
recovery copy — the two are needed together, because a restored database is
ciphertext without it.

**Decision: fail loudly rather than write plaintext.** A backup script whose
encryption silently degrades is worse than no script: it produces a file that
looks like a backup and an unencrypted copy of every row in the product,
off-host, forever.

**Decision: the restore script defaults to a scratch database.** The drill is
the thing nobody does, so it should be the easy thing to run; restoring over
the live database takes an explicit `--into` and warns for ten seconds.

**The drill has now been run** (see §13).

---

## 11. Reproducible builds

**The finding.** `backend/pyproject.toml` uses open-ended minimums and the
Dockerfile ran `pip install .`, so the same commit resolved differently on
different days. The repo's own comments already record two resolver-driven
incompatibilities found that way.

**Decision: `uv`, not `pip-compile`.** pip-compile resolves for the platform
it runs on. This repo is developed on Windows and deployed on Linux, so a
platform-specific lock would be wrong for the image it is meant to pin. uv
resolves universally and emits environment markers.

**Decision: two locks.** `requirements.lock` is what ships (runtime only);
`requirements-dev.lock` adds the toolchain, which matters because ruff, mypy
and pytest are all pinned exactly in `pyproject.toml` after being bitten twice
— a floating linter turns a green tree red with no code change behind it.

**Decision: `--require-hashes`.** Digests are the half that makes this a
supply-chain control rather than a version list; without them, a re-uploaded
artifact at the same version installs silently.

**Verification, and a lesson about mutation testing.** The first attempt
corrupted *one* of alembic's two hashes and the build succeeded — pip accepts
a match against any listed digest. Corrupting all of them produced
`THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE` and a
failed build. **An inadequate mutation is indistinguishable from an absent
gate.**

`tests/test_requirements_lock.py` keeps both locks in step with
`pyproject.toml` — the way a lock rots is mundane: someone adds a dependency
and does not run `make lock`.

---

## 12. Test infrastructure

Not in the plan; done because it was in the way.

Sixty-four test files each started their own `PostgresContainer`. They now
share one server and take a database each (`backend/tests/_database.py`,
`postgres_container` module-scoped, `fresh_database` per-test). The isolation
they wanted was right — each creates and drops its own subset of tables — but
a *database* provides it for about a tenth of a second.

**This saved almost nothing, and that is the useful finding.** The suite went
from 52 minutes to 50. A cProfile of one 24-second storage-router test showed
**24.6 of 28 seconds in `GetQueuedCompletionStatus`** — Docker-on-Windows
socket round-trip latency, roughly 18 ms a hop. The runtime floor on this
machine is the environment, not the test design. Do not chase suite runtime
with test-design changes here.

The refactor is still worth keeping: the container count went from 64+ to 1,
and a targeted run of six files now takes 13 seconds rather than two minutes.

---

## 13. What only deploying could find

Three defects that every local gate passed.

**1. Every script under `scripts/` was committed `100644`.** `chmod +x` on
Windows is invisible to git, so the mode never reached the repo and
`backup-postgres.sh` died with "Permission denied" on first use. The three
older scripts had the same defect and got away with it because the Makefile
invokes them as `bash scripts/x.sh`; a systemd unit calling the path directly
does not. Fixed with `git update-index --chmod=+x`.

**2. The backup script passed a single `-f "$COMPOSE_FILE"`** and the box
assembles its stack from three overlays. The quoting error is not the
interesting part — **the base file alone is a valid but different stack**
(since PR #28 an edge with no site in it), so without the error this would
have silently backed up something that is not the deployment serving traffic.
Fixed by deleting the flag: `COMPOSE_FILE` is docker compose's own
colon-separated variable, so it works for one file or five and the script
stops needing to know.

**3. Hand-made dumps were `0664`** — world-readable copies of every row.
The script uses `umask 077`; the existing ones were tightened.

### The restore drill, run for the first time

| | |
|---|---|
| Archive | 102,458 bytes, mode 0600 |
| Restore | 2 seconds into a scratch database |
| Contents | 8 users, all five migration heads (one per enabled module branch) |
| **Decryption** | an `EncryptedString` column decrypted with the live `ENCRYPTION_KEY` |

That last row is the one that matters. **A restore can report every row
present and still be unreadable ciphertext, and no row count distinguishes
the two.** The drill now checks it.

`forge-backup.timer` is installed (daily 03:30 UTC, `Persistent=true` so a
missed day runs after a reboot rather than being skipped) and was fired once
by hand.

### The deployment itself

Box went `287d65b` → `b8ca822`. No migration. Backend image built **on the
box, from the lock, with `--require-hashes`** — so §11 is verified in the real
deployment path rather than only locally.

Verified after: app and marketing 200, storage 403 (MinIO's `AccessDenied`
proves reachability), `/metrics` 404 at the edge, HTTP→HTTPS 301, worker
`(healthy)` for the first time, zero application errors, and the served bundle
hash matching the one the build produced.

`docker builder prune -f` reclaimed 21 GB — disk went from **81% to 27%**.
It was back to 41% after a single build, so that recurs; the new alert is what
will say so next time.

---

## 14. What is still open

**1. Backups are not off-host.** `BACKUP_REMOTE` is unset, so archives sit on
the same disk as the database they came from — they survive a bad migration,
not a dead disk. Needs an rclone remote and credentials the operator holds.
The script says so on every run.

**2. The backup private key exists in exactly one place.** Deliberately not on
the box. Lose it and every archive is unreadable. It belongs wherever
`ENCRYPTION_KEY`'s recovery copy lives, because a restore needs both.

**3. CI still floats.** Nine `pip install -e ".[dev]"` steps install
unlocked, and `pip-audit` runs against the environment rather than the lock.
Both are one-line changes; both are CI work, deferred with the rest of the
plan's §9 while Actions is unavailable. Until then the image is reproducible
and the pipeline that tests it is not.

**4. Stage 6 needs a decision, not code.** Real identity, a working support
route, and a privacy page naming this deployment's actual processors — or
label the deployment a public demo and stop accepting real signups. Both are
defensible; the current state is neither. Note that the registration screen
now links to whatever those pages say, which makes the copy load-bearing in a
way it was not before.

Two smaller ones, written down so they are not rediscovered: the signed opaque
cursor (§9) would additionally survive a hard-deleted anchor; and `amr` (§3)
turns an inference into a proof and should ride along with the next migration.

**5. Found by the follow-up review** (see the correction note at the top):
downloads not bound to scanned bytes (R1), theft-response revocations rolled
back (R2), admin gate trusting enrollment (R3, which makes the `amr` item
above a P1 rather than an optional extra), scan memory unbounded by
concurrency (R4), no shared rate budgets (R5), unbounded webhook responses
(R6), and the file page stuck on "Scanning" (R7). All are tracked in
[`production-readiness-plan-2026-09-29.md`](production-readiness-plan-2026-09-29.md).

---

## Verification summary

| Gate | Result |
|---|---|
| Backend suite | 2636 passed, 6 skipped |
| Web | 363 passed |
| Mobile | 113 passed |
| `ruff check` / `ruff format --check` | clean |
| `mypy --strict app/ scripts/` | clean |
| `pnpm lint` / `pnpm type-check` | clean |
| `promtool check rules` | 6 rules valid |
| `check_docs.py` | passed |
| Live deployment | all hosts verified, zero application errors |

Every security fix in §§1–6 was mutation-checked: the code was deliberately
broken and the test watched to fail. Where a mutation turned out to be
inadequate (§11), that is recorded rather than quietly re-done.
