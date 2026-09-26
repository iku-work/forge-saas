# Production-readiness remediation plan — 25 September 2026

Derived from the [repository audit](repository-audit-2026-09-24.md) and the
[live production and CI/CD audit](production-cicd-audit-2026-09-24.md).

CI/CD repair (CICD-01 … CICD-07) is **deferred by decision** — GitHub Actions is
unavailable on this account. Everything below is therefore written to be
verifiable locally, against the Docker compose stack, without a green pipeline.
§9 records what that costs us and what to pick up when Actions returns.

## Status — 25 September 2026

| Stage | State |
|---|---|
| 0.1 Secrets out of the edge | **Done.** Caddy takes a four-name allowlist; `env_file` removed. Two tests keep it honest in both directions. Rotation still outstanding — an operator action on the box. |
| 0.2 App document headers | **Done and verified on the wire.** All five headers plus HSTS observed on a served document from a real Caddy container; `/metrics` still 404s. Enforced, not report-only, in both the dev and prod edges. |
| 0.3 Scanner on the box | **Not done — operator action.** The template now refuses the stub at startup (1.3), so the box must move to `VIRUS_SCANNER=clamav` before it will boot on this build. See the warning below. |
| 1.1 Download gate | **Done.** Both signing surfaces, including the avatar path the audit did not name. |
| 1.2 Real SHA-256 | **Done.** Computed in the scan worker from the bytes it scans. |
| 1.3 Scanner fails closed | **Done.** Unknown hash raises; `virustotal` refused outside dev. |
| 1.4 Deletion job | **Done.** Implemented, registered, plus a reconciliation sweep and a test that every enqueued job name resolves. |
| 2.1 One session-issuance policy | **Done.** All four sign-in paths consult one helper; magic link, social OAuth and SSO each had a different hole. Web magic-link page finishes the challenge; api-client regenerated. |
| 2.2 `sid` claim | **Done.** Access tokens name their session. |
| 2.2 `amr` claim | **Deferred deliberately — see below.** |
| 2.3 Immediate revocation | **Done.** Every revocation path kills issued access tokens now, not in 15 minutes. |
| 3 onwards | Not started. |

**Why `amr` is deferred rather than done.** Its purpose is letting
`require_superadmin` check that the current session *completed* a second
factor, instead of checking that one is *enrolled*. Two things changed the
calculus after 2.1 landed:

- 2.1 already makes every sign-in path challenge an enrolled user, so for an
  enrolled superadmin "enrolled" now implies "was challenged". `amr` turns an
  inference into a proof, which is real defence-in-depth but not an open hole.
- It cannot be done without a migration. `amr` has to survive refresh
  rotation, and the only reason `auth_time` does is that it is a column on
  `Session`. So this is a schema change plus a deploy step, for a marginal
  gain over what 2.1 delivers.

Meanwhile Stage 3 has two P1s that *are* open holes: single-use tokens and
refresh rotation are not atomic, and general rate limiting keys on an
attribute nothing ever sets, so every authenticated user is limited by IP.
Those come first. Pick `amr` up with the next migration that ships for
another reason.

**Deploying this build to the box is a breaking change for it.** The box runs
`VIRUS_SCANNER=virustotal`, and this build refuses to start on that outside
dev — deliberately, because that scanner certified unknown files clean. Set
`VIRUS_SCANNER=clamav`, bring up the existing profile
(`docker compose --profile clamav up -d`), and set `CLAMAV_HOST=clamav`
*before* deploying. The same deploy needs `DOMAIN`, `MARKETING_DOMAIN`,
`STORAGE_DOMAIN` and `CADDY_TRUSTED_PROXIES` in the compose project's own
`.env` (not just `backend/.env`); compose refuses to start without `DOMAIN`.

**Existing files will be undownloadable until they are scanned.** The gate
requires `virus_scan_result == "clean"`, and every file uploaded before this
build has that column NULL — they were never scanned, which is the finding.
Re-enqueue `virus_scan` for them after clamd is up, or accept that they stay
unavailable. This is the "controlled remediation plan" PROD-01 asks for and
it has not been written yet.

## What was re-verified before writing this

Every P1 in both audits was checked against the working tree rather than taken
on trust. All of them reproduce:

| Claim | Verified at | Status |
|---|---|---|
| Download signing ignores scan state | `storage/service.py:260` `get_download_url` | confirmed — no `status`/`virus_scan_result` predicate |
| VirusTotal 404 → clean | `storage/scanner.py:174` | confirmed — `return ScanResult(clean=True)` |
| `checksum_sha256` is really an ETag | `storage/service.py:198` | confirmed |
| Magic link / social login skip MFA | `auth/service.py:523`, `:575` | confirmed — both call `_create_session` directly |
| SSO checks TOTP only, not WebAuthn | `auth/service.py:632` | confirmed — `if user.totp_enabled` |
| Superadmin gate checks enrolment, not proof | `core/dependencies.py:191` | confirmed |
| Rate limiter keys on an unset attribute | `middleware/rate_limit.py:16` | confirmed — `state.user_id` assigned nowhere in `app/` |
| Single-use tokens consumed non-atomically | `auth/repository.py:489` | confirmed — SELECT-by-id then assign |
| Refresh rotation non-atomic | `auth/repository.py:173` | confirmed — no lock, no conditional update |
| `token_endpoint` bypasses the URL guard | `sso/oidc.py:152` | confirmed |
| URL guard is TOCTOU by construction | `core/url_guard.py:71` | confirmed — resolves, then returns the original URL for httpx to re-resolve |
| Quota counts only `ready` bytes | `storage/repository.py:50` | confirmed — `m.status == "ready"` |
| `size_bytes` has no upper bound | `storage/schemas.py:14` | confirmed — `Field(gt=0)` |
| `hard_delete_stored_file` does not exist | `workers/main.py:57` | confirmed — absent from `WorkerSettings.functions`, absent from the tree |
| Caddy receives the whole backend env | `docker-compose.prod.yml:249` | confirmed — `env_file: ./backend/.env` |
| App HTML has no document headers | `Caddyfile.prod:112` | confirmed — HSTS and `-Server` only |

Two things the audits understate, found while checking:

- **ClamAV is already wired.** `docker-compose.prod.yml` ships a `clamav`
  service behind a profile, and `ClamAVScanner` is implemented. Switching off
  the VirusTotal stub is mostly an ops action, not a build.
- **`config.py:424` already refuses `noop` outside dev** but not `virustotal`.
  The guard exists; it is one branch short of covering the case that shipped.

Two places the audits are imprecise, worth knowing before acting on them:

- **Finding 5's "1000/hr org budget"** is this repo's own §0.6 law, not an
  external requirement. Still worth having — but it is a target we set
  ourselves, not a compliance item.
- **Findings 3 and 9 are latent in production.** `enterprise_sso` and
  `outbound_webhooks` are both disabled on this deployment, so no enabled code
  path reaches either. They are template defects, fixed on the template's
  schedule, not the box's.

## Two targets, deliberately separated

The audits interleave "this template is not production-ready" with "this
deployment is not production-ready". Those need different work at different
speeds:

- **The box** (`mycalendar.party`) — config changes, deployable today, no code.
- **The template** — code changes every future product inherits.

Stage 0 is the box. Stages 1–8 are the template, and reach the box on the next
deploy.

---

## Stage 0 — the live box, today (config only)

No code, no release. Three changes, each independently revertible.

### 0.1 Take application secrets out of the edge container — PROD-02

`docker-compose.prod.yml:249` hands Caddy the entire backend env file. Caddy
needs `DOMAIN`, `APP_DOMAIN`, `MARKETING_DOMAIN` and the ACME contact — nothing
else. Replace `env_file` with an explicit `environment:` allowlist of exactly
those keys.

**Care needed:** `MARKETING_DOMAIN` must not end up set-but-empty. A blank
`{$VAR}` makes Caddy build a site block with no address, and the *whole edge*
fails to start — app host included (measured during T-307, recorded in
`docs/runbooks/deploy-render.md`).

**Verify:** `docker compose exec caddy env` shows none of `JWT_PRIVATE_KEY`,
`ENCRYPTION_KEY`, `DATABASE_URL`, `STRIPE_SECRET_KEY`, `VIRUSTOTAL_API_KEY`;
both hosts still serve over TLS.

**Then rotate.** These values sat in an internet-facing container. There is no
evidence of compromise, but the cheap move is to rotate anyway:
`docs/runbooks/credential-rotation.md` and
`docs/runbooks/encryption-key-rotation.md` already cover the procedures, and the
encryption key has zero-downtime rotation built in (`ENCRYPTION_KEY_OLD`).

Size: S.

### 0.2 Give the app's HTML the headers it claims — PROD-03 / finding 7

`Caddyfile.prod`'s app-site `header` block sets HSTS and strips `Server`, and
nothing else. The marketing block next door already does this properly; copy its
shape. The app needs CSP, `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`,
`Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`.

**The CSP is the work.** The SPA is Vite-built with hashed assets, so
`script-src 'self'` should hold — but Stripe Checkout redirects, Sentry (once
configured) and any analytics need explicit entries. Ship it report-only first,
watch for a day, then enforce.

**Verify:** `curl -sI https://app.mycalendar.party/en/login` shows all five;
login, checkout redirect and file upload still work in a real browser with the
console open.

Size: S–M — the report-only soak is the calendar time, not the work.

### 0.3 Stop the scanner certifying unknown files clean — PROD-01, partial

Until Stage 1 lands, the fastest honest position is to **turn the stub off**.
Set `VIRUS_SCANNER=clamav`, bring up the existing profile
(`docker compose --profile clamav up -d`), set `CLAMAV_HOST=clamav`.

**Check the resource budget first.** clamd holds its signature database in
memory — budget ~1.5 GB RSS and ~1 GB disk. The box is at 68% of 38 GB with
13 GB of reclaimable build cache; prune that first (Stage 7) rather than
discovering the shortfall at 2am.

If clamd will not fit, the interim alternative is `VIRUS_SCANNER=noop` plus
blocking downloads outright at the edge. That is worse product, but it is
honest, where "VirusTotal says clean" is neither.

**Worth saying plainly:** the VirusTotal path can only become a real scanner by
uploading customer files to a third party. That is a processor disclosure and a
GDPR question, not merely an engineering choice — a second, independent reason
to standardise on self-hosted clamd.

**Verify:** upload the EICAR test string through the app; the file reaches
`status="infected"` and the object is gone from MinIO.

Size: S, contingent on RAM.

---

## Stage 1 — file safety (repo findings 1, 14; PROD-01)

These are one change, not three. The download gate alone is vacuous while the
scanner calls everything clean; the scanner alone leaves failed files
downloadable; and the scanner cannot look anything up while `checksum_sha256`
holds an ETag.

### 1.1 Gate every signing surface on a clean verdict

`get_download_url` signs immediately after the ownership check. Require
`status == "ready"` **and** `virus_scan_result == "clean"`, raising a typed
`DomainError` — not a 404, since the caller needs to distinguish "not yours"
from "not scanned yet".

`grep -rn "presign_download" backend/app` — gate *every* call site, not only the
one the audit names. Avatars go through `bucket="avatars"` and need the same
treatment, or an explicit commented exemption.

Expose the state on `StoredFilePublic` so the frontend can tell the difference
(feeds 5.2).

**Tests:** parametrise `pending` / `failed` / `infected` / `ready+clean` through
the HTTP endpoint, not the service — the router is where the gate has to hold.

Size: S.

### 1.2 Compute a real digest, bound to the bytes that were scanned

`confirm_upload` copies the ETag into `checksum_sha256`. For a multipart upload
an ETag is not even an MD5 of the content, so the column is wrong in a way that
silently varies with upload size.

**Recommended shape:** compute SHA-256 in the scan worker, in the same pass that
streams bytes to clamd. `ClamAVScanner` already streams the object; hash it
while it goes past. That yields a digest provably of the scanned bytes rather
than of whatever is at the key now — the "bind the verdict to the exact object
version" the audit asks for, obtained for free.

Leave `checksum_sha256` NULL until the scan sets it. Rename nothing: the column
name was always right, only the value was wrong.

Size: M.

### 1.3 Make the scanner fail closed, and refuse the stub in production

- `VirusTotalScanner`: a 404 raises `ScannerError` instead of returning clean.
  Keep the class, documented as reputation *enrichment* — it is not a scanner.
- `config.py:424`: widen the existing non-dev guard from `{"noop"}` to
  `{"noop", "virustotal"}`, with the reason in the message.
- `virus_scan` job: on `ScannerError` after retries, `status="failed"` — which
  1.1 now makes undownloadable, closing the loop.

Size: S.

### 1.4 Implement the deletion job that is already being enqueued

`delete_file` enqueues `hard_delete_stored_file`; nothing implements or
registers it. Every delete since the feature shipped has left its object in
storage.

- Write it in `workers/jobs/storage.py`: idempotent, re-checks the row is still
  soft-deleted (so the grace window can still be cancelled), deletes the object,
  then the row.
- Register it in `WorkerSettings.functions`.
- Add a reconciliation sweep on the existing APScheduler for rows whose job was
  lost. There is a backlog to clear, and after that it is cheap insurance.
- **Add a test asserting every string passed to `enqueue_job` anywhere in `app/`
  resolves against `WorkerSettings.functions`.** That is the test that would
  have caught this, and it is about fifteen lines.

Size: M.

**Stage 1 gate:** upload → confirm → download refused → scan completes →
download succeeds; EICAR quarantined and its object removed; a deleted file's
object gone from MinIO after the grace period.

---

## Stage 2 — authentication policy (repo findings 2, 6)

Four sign-in paths, four different second-factor policies, and an access token
carrying no record of which factors were actually used. Both findings are the
same missing thing: **the token does not say how it was obtained.**

### 2.1 One session-issuance policy for every entry point

`login`, `consume_magic_link`, `oauth_login` and `sso_login` each decide for
themselves today. Replace with one path:

```python
async def _issue_session(user, ip, ua, *, methods: frozenset[AuthMethod])
```

and one policy function every caller consults. A user with TOTP *or* WebAuthn
enrolled gets challenged unless `methods` already contains a second factor — the
IdP's asserted `amr`, or a completed local challenge.

Magic link is the interesting case: possession of a mailbox is not a second
factor for an account whose owner asked for one, so it must challenge exactly as
password login does. Social is the same argument — "the provider probably did
MFA" is not proof. If we want to accept it, accept it from the provider's `amr`
claim explicitly, not by assumption.

### 2.2 Put `amr` and `sid` in the access token

`create_access_token` already carries `auth_time` (T-218). Add:

- `amr: list[str]` — the methods actually used. `require_superadmin` then checks
  *proof* rather than enrolment, and T-218's step-up gate gets sharper for free.
- `sid: str` — the refresh session's id.

`sid` is what makes finding 6 fixable at all: `get_current_user` can reject a
token whose session is revoked.

### 2.3 Make revocation immediate

Two mechanisms, both cheap:

- **Bulk** (logout-all, password reset, theft response): a `session_epoch`
  timestamp on `User`, bumped on revoke-all. `get_current_user` rejects tokens
  with `iat < session_epoch`. **Zero extra round trips** — the user row is
  already loaded and already cached (T-240), and that cache is already
  invalidated on these paths.
- **Single session** (revoke one device): add its `sid` to the existing
  `core/token_blocklist.py` with a TTL of the access-token lifetime. Bounded,
  self-expiring, one Redis GET.

**Tests:** hold a bearer token across each revocation path and assert it stops
working *on the next request*, not after fifteen minutes.

Size: L. This changes the token contract, so mobile and web both need a pass and
`@forge/api-client` regenerates.

---

## Stage 3 — concurrency and identity (repo findings 4, 5)

### 3.1 Atomic consumption (finding 4)

Both are read-then-write with no lock:

- **Single-use tokens** (`auth/repository.py:489`): replace `mark_used`'s
  SELECT+assign with `UPDATE … SET used_at = now() WHERE id = :id AND used_at IS
  NULL RETURNING id`, treating an empty result as "already consumed".
  Conditional UPDATE rather than `SELECT … FOR UPDATE`, because it composes
  cleanly with pgbouncer's transaction pooling (ADR-0002).
- **Refresh rotation** (`service.py:384`): same shape on `revoked_at`. The
  transaction that wins the UPDATE rotates; the one that loses triggers the
  existing theft response — which turns a race into exactly the signal the theft
  detector was built for.
- TOTP and backup-code consumption share the read/modify/write shape and want
  the same treatment.

**Tests:** real Postgres, two independent connections, a barrier, assert exactly
one success. The existing sequential-replay tests prove nothing about this. Note
these tests must connect direct, not through pgbouncer.

Size: M.

### 3.2 Rate-limit identity (finding 5)

`_key_func` reads `request.state.user_id`, which nothing sets — so every
authenticated request is limited by IP. The structural cause is ordering:
`SlowAPIMiddleware` runs before dependencies resolve, so no dependency can
supply it.

**Fix:** a small middleware registered *before* the limiter that parses and
verifies the bearer token for **keying only**, stashing `user_id`/`org_id` on
`request.state`. Dependencies keep doing their own authoritative verification —
this one is advisory, and a malformed token simply falls back to IP.

Then add the org budget the laws call for (§0.6: 1000/hr) as a second limiter
keyed on `org:{id}`.

While in this file: `_settings = get_settings()` at module import is the
import-time binding trap that has bitten this repo twice (T-223,
`test_middleware.py`). Read it inside the function.

**Tests:** two users behind one IP do not exhaust each other; one user on two
IPs shares a budget; traffic spread across routes hits the org ceiling.

Size: M.

---

## Stage 4 — storage accounting and outbound requests (repo findings 3, 8)

### 4.1 Quota reservation (finding 8)

Three holes in the same place:

1. `total_bytes_for_org` counts only `status == "ready"`, so N pending presigns
   each pass against the same free space, then all confirm.
2. The 60s Redis cache is invalidated on confirm and delete, but not on presign.
3. `size_bytes: int = Field(gt=0)` has no ceiling, and with billing disabled
   `_check_quota` is skipped entirely — so a non-billing product has no limit at
   all.

**Fix:** count `pending` rows younger than the presign TTL toward the total;
invalidate the cache on presign; expire abandoned pending rows on the scheduler;
re-check at confirm; and add a `MAX_UPLOAD_BYTES` setting enforced in the schema
regardless of the billing module. A template that lets someone fill its disk the
moment billing is switched off is a template bug, not a product decision.

**Tests:** ten pending presigns against a quota that fits one; concurrent
presigns; billing disabled.

Size: M.

### 4.2 SSRF (finding 3)

Latent — both modules are off in production — so this is template work, not box
work. Two parts of very different size:

- **Small:** `sso/oidc.py:152` sends the token exchange to
  `discovery["token_endpoint"]` without the guard. Wrap it, require HTTPS, and
  do the same for any other URL taken from a discovery document.
- **Larger:** `url_guard` resolves, checks, then returns the *original URL*,
  which httpx resolves again — the answer can change in between. The real fix is
  a transport that connects to the validated IP while preserving `Host` and TLS
  SNI. Fiddly but self-contained, and it fixes SSO and webhooks at once.

Size: S + M.

---

## Stage 5 — user-facing correctness (repo findings 11, 12, 13; UX-01)

### 5.1 Mobile MFA is a dead end (finding 11) — P1

`apps/mobile/src/lib/auth.ts:98` throws `TwoFactorRequiredError`; the login
screen catches it as an ordinary form error. Enrolling a second factor *locks a
user out of the mobile app*. Build the challenge screen, wire the temp token,
handle backup codes.

Do this **after** Stage 2, so it is built against the unified policy rather than
retrofitted to it.

Size: M.

### 5.2 File UI tells the truth (finding 12)

Gate upload and delete on `files:manage` — members currently see buttons that
403. Catch download errors instead of `void`-ing the promise. Show scan state,
which 1.1 makes meaningful and 1.2 makes accurate.

Size: S.

### 5.3 Only offer sign-in providers that exist (UX-01)

The login page renders Google and GitHub; both endpoints 404 because no client
IDs are configured. Either configure them, or — better for a template — have
`/api/v1/config` report per-provider availability and render from that. Then
this class of mismatch cannot recur on any product built from this template.

Size: S.

### 5.4 Opaque cursors (finding 13)

Soft-deleting a page anchor silently restarts pagination at page one, which an
infinite list appends as duplicates. Encode `(created_at, id)` into a signed
opaque cursor, or return an explicit invalid-cursor error.

**Note the blast radius:** `BaseRepository.list_cursor` is shared by every list
endpoint, and the cursor is part of the published API contract (T-314) — so this
regenerates the client and touches every paginated surface. Worth doing, worth
doing deliberately, and worth doing while T-243's query-budget suite is
watching.

Size: M.

---

## Stage 6 — content and disclosure (UX-02)

The site advertises ForgeSaaS, support points at `support@example.com`, and the
privacy and terms pages still say they are templates — while terms assert that
signup constitutes acceptance and the signup screen links to neither.

Either finish it — real identity, a working support route, a privacy page naming
this deployment's actual processors (Stripe, Resend, Hetzner, Cloudflare, and
whatever scanner decision 0.3 lands on), and links at registration — or label
the deployment unambiguously as a public demo and stop accepting real signups.
Both are defensible. The current state is neither.

Size: M, and mostly not engineering.

---

## Stage 7 — operational assurance (PROD-04, PROD-05, PROD-06)

None of this is code, and all of it is the difference between "it works" and "we
would know if it stopped".

- **Backups (PROD-05).** One dump, four days old, no schedule found, no off-host
  copy, no restore ever executed — and `docs/runbooks/backup-restore.md` says so
  itself. Schedule it, ship it off-host, encrypt it, tighten `0664` to `0600`,
  and **run one timed restore into a scratch database**. An untested backup is a
  belief, not a backup. Include key custody: a restored database is unreadable
  without `ENCRYPTION_KEY`, so the key's recovery path is part of the backup's
  recovery path.
- **Worker liveness (PROD-06).** No healthcheck on the worker, Sentry
  unconfigured, no metrics scrape. `/health/ready` checks DB and Redis, not that
  jobs run — so a wedged worker means silent email, deletion and scanning
  failure. Add a Docker healthcheck, configure `SENTRY_DSN`, add a queue-age
  alert. The Prometheus plumbing already exists (T-202, and T-350 gave the
  worker its own `/metrics`); it simply is not scraped here.
- **Disk and logs (PROD-04).** 68% of 38 GB with 13 GB reclaimable build cache.
  Prune, set `json-file` rotation limits, set per-container memory limits, add a
  disk alert — especially before clamd's database lands (0.3).

Size: M in total, spread thin.

---

## Stage 8 — reproducibility (repo finding 16)

`backend/pyproject.toml` uses open-ended minimums and the Dockerfile runs
`pip install .`, so the same commit resolves differently on different days. The
repo's own comments already record two resolver-driven incompatibilities.

Commit a lock (`uv lock` or `pip-compile`), build from it, update it
deliberately with an advisory scan. This also makes every other stage's
verification mean something — without it, "it passed locally" and "it runs in
production" are claims about different software.

Size: M.

---

## §9 Deferred: CI/CD, and what that costs

CICD-01 … CICD-07 are parked. Two consequences to hold in mind.

**Every gate is manual now.** `main` is unprotected (CICD-03 confirms it), there
is no automated coverage floor, no ZAP, no k6. Until Actions returns, the
practical substitute is a pre-push ritual: `make lint`, `mypy --strict app/`,
the backend suite against real Postgres and Redis, `pnpm lint && pnpm type-check
&& pnpm test`, and Playwright before anything touching auth. Write it down — an
undocumented ritual is not one.

**Do not let deploy automation land first when it returns.** CICD-04 and
CICD-05 are worse than absent: the preflight migration cannot reach a private
database from a hosted runner, and the rollback path would construct a
nonexistent GHCR reference for the current `forge-saas-backend` image — so
automating deploys today would mean a rollback that cannot roll back. The box is
manually deployed; the honest move is to document that and its rollback
procedure now, and treat CICD-04/05 as prerequisites for automation rather than
improvements to it.

The one CI item worth doing while Actions is dark: **CICD-01's permission
mismatch is a source fix.** `deploy-aws` does not grant `id-token: write` while
the called workflow requests it, and a reusable workflow cannot elevate beyond
its caller. One line, fixable now, verifiable when the lights come back on.

---

## Sequencing

Stages 0–2 are the release gate. The rest is ordered by risk, not by effort.

| Stage | Blocks | Why here |
|---|---|---|
| 0 | — | Config only. Removes edge secret exposure and the fail-open scanner the same day. |
| 1 | 5.2 | File safety is one change; splitting it ships a vacuous gate. |
| 2 | 5.1 | Mobile MFA should be built against the unified policy, not retrofitted to it. |
| 3 | — | Independent. Do 3.2 early — it is small, and today every user is limited by IP. |
| 4 | — | 4.2 is template-only; both modules are off in prod. |
| 5 | 1, 2 | Depends on the state the earlier stages make real. |
| 6 | — | Not engineering-bound. Start in parallel. |
| 7 | — | Start the restore drill immediately, in parallel. Longest lead time, worst failure mode. |
| 8 | — | Makes everything above reproducible. Ideally before; realistically alongside. |

If only three things get done: **0.1** (secrets out of the edge), **1.1 + 1.3**
(files stop being certified clean without being scanned), and **Stage 7's
restore drill** (find out now whether the backups work).

## Explicitly not covered

Billing concurrency and idempotency, complete GDPR purge behaviour, and
accessibility on real devices were flagged by the repository audit as needing
integration-level verification and are not addressed here. Neither is a fresh
dependency advisory scan. Nothing in this plan should be read as a clean bill of
health for those.
