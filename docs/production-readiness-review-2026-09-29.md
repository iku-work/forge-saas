# Follow-up repository review — 2026-09-29

Reviewed HEAD `705f392`, including `production-readiness-report-2026-09-29.md` and the remediation implementation. This is a repository review, not a new attestation of production or GitHub account state. No application code or production state was changed.

**Assessment:** substantial improvements, but the report overstates closure of file safety, refresh-token theft containment, and privileged-session MFA. Further work is required before treating the earlier security findings as closed.

## Findings

### R1 · P1 · A clean scan does not bind downloads to immutable bytes

Evidence: `backend/app/modules/storage/backends/s3.py:88–128`, `backend/app/workers/jobs/virus_scan.py:94–124`, `backend/app/modules/storage/service.py:298–303`.

The upload authorization remains valid for 600 seconds and targets the same object key later used for downloads. Its policy binds content type and size, but neither a content checksum nor an immutable version. The worker records a SHA-256 and a clean verdict, but downloads sign only Bucket/Key; they neither select a scanned VersionId nor otherwise enforce that digest.

An uploader can upload benign bytes, confirm, wait for a clean scan, and reuse the still-valid upload form to replace that key with different bytes of the permitted size/type. The database stays clean and downloads serve the replacement. Re-confirming is a no-op for a ready row. Computing a hash of scanned bytes does not bind subsequent reads to those bytes. S3 documents replacement of existing keys and reuse of presigned authorizations [here](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html). No upload attack was run against production.

Fix: scan and serve a specific immutable version, or promote the exact scanned object into a server-only writable destination. A copy must itself select the scanned version or enforce an equivalent condition; an unconditioned copy introduces another race. Test benign upload → clean scan → overwrite → download, including avatar downloads.

### R2 · P1 · Theft-response revocations are rolled back

Evidence: `backend/app/modules/auth/service.py:445–472`, `backend/app/modules/auth/repository.py:236–251`, `backend/app/modules/auth/router.py:172–175`, `backend/app/core/db.py:248–259`.

Both revoked-token replay and losing the refresh race call `revoke_all_for_user` and immediately raise `AuthenticationError`. The repository only flushes. The router commits only after a successful service return; the session dependency rolls back on the exception. Consequently, revocation of the successor and other sessions does not persist. The replay request gets 401, but the stolen successor can remain usable.

The existing `test_refresh_reuse_of_revoked_token_revokes_all_user_sessions` checks inside the same uncommitted transaction. That explains why the test passes without proving HTTP-level containment. The atomic refresh update correctly prevents two successors; it does not fix this transaction boundary.

Fix: explicitly persist security revocations before returning the rejection, using a transaction design that does not commit unrelated failed-request writes. Add an HTTP replay test and verify the successor and another device from a fresh database session after the response. A disposable-Postgres probe was attempted here but could not start because the local Docker daemon was unavailable; this finding is established by source tracing, not a completed database reproduction.

### R3 · P1 · MFA enrollment still does not prove that an admin session passed MFA

Evidence: `backend/app/core/dependencies.py:195–234`, `backend/app/modules/auth/service.py:1070–1091`, `backend/app/modules/auth/totp.py:54–76`.

`require_superadmin` checks the account's current enrollment, not the session's authentication methods. Enabling TOTP updates the account and invalidates its user cache, but does not invalidate pre-enrollment sessions. A password-only session on device A can therefore remain valid while the user enables MFA on device B; A then passes the admin gate without completing MFA. The new unified login policy applies only to subsequent logins.

This directly contradicts report §3's claim that enrollment now implies a challenge was completed. A temporary isolated probe confirmed the admin dependency accepts an enrolled superadmin without session-factor evidence; it was removed afterward.

Fix: persist the completed authentication methods across refresh and require suitable session assurance at the admin gate. Alternatively, revoke and reauthenticate all pre-enrollment sessions as an interim control, including equivalent WebAuthn transitions. Add a two-session enrollment test.

### R4 · P1 · Concurrent scans can exceed the new worker memory cap

Evidence: `backend/app/workers/main.py:83`, `backend/app/workers/jobs/virus_scan.py:59,183–204`, `backend/app/modules/storage/scanner.py:116–128`, `backend/app/core/config.py:315`, `docker-compose.prod.yml:188`.

The worker permits 20 concurrent jobs and has a 768 MiB cap. The normal upload ceiling is 100 MiB. Each scan accumulates the entire file in chunks, joins those into another full buffer, and the ClamAV client writes all chunks before its first `drain()`. Twenty permitted uploads can retain roughly 2 GiB of original file data alone, before copies, socket buffering, and interpreter overhead. The 256 MiB per-file fetch guard bounds one request, not process memory.

Impact: an upload burst can OOM the shared worker, interrupting email, deletion, scanning, and other jobs. This is a capacity defect derived from bounds and concurrency, not a load-test result.

Fix: stream with incremental hashing and backpressure, and give scans a separate concurrency/memory budget or worker. Verify peak RSS with concurrent maximum-size permitted uploads and confirm other job types still progress.

### R5 · P2 · Rate-limit identity is fixed; shared budgets are still absent

Evidence: `backend/app/middleware/rate_limit.py:81–99`.

JWT-based identity fixes the earlier dead `request.state.user_id` branch. However, `api_limit` remains an endpoint-scoped `limiter.limit`, with no application-wide/shared user budget and no org-wide 1,000/hour budget. The limiter still uses the default fixed-window strategy. A user can distribute requests across endpoints to exceed 100/minute overall; multiple users in one org have no aggregate org ceiling.

Fix: shared user and org budgets with the specified window behavior, while retaining endpoint-specific auth limits. Verify by alternating routes, users within one org, and requests across a window boundary. Report §5 should mark the broader finding partially resolved.

### R6 · P2 · Webhook responses remain unbounded

Evidence: `backend/app/workers/jobs/webhooks.py:159–175`.

DNS resolution and address pinning improved, but `client.post()` still buffers the full response before `response.text` is sliced. HTTPX's per-operation timeout is not a total response deadline. A webhook receiver can return a large body or keep providing chunks to occupy memory and a worker slot until the outer job timeout.

Fix: stream with a byte cap and an overall deadline covering resolution, connection, and reading; stop reading when the cap is reached. Add large-body and slow-trickle tests. The readiness report says outbound webhooks are disabled in the current deployment, so treat this as a template/enabled-module risk rather than a demonstrated live exposure.

### R7 · P2 · The file page can remain stuck in “Scanning”

Evidence: `apps/web/src/features/files/FilesPage.tsx:116–128,170–176,253`, `backend/app/workers/jobs/virus_scan.py:117–124`.

Upload confirmation triggers one list invalidation, normally before the background scan finishes. The page disables download while the verdict is absent, but has no polling or scan-completion invalidation. The clean worker path only commits the verdict. A user who stays on the focused page can see “Scanning” indefinitely after the file becomes downloadable. A later refocus/remount may refresh it, but that is not completion feedback.

Fix: poll while any displayed file awaits a verdict, stop after completion, or publish and consume a scan-completion event. Test a null-to-clean transition while the page remains mounted and focused. The existing state-rendering tests do not establish this transition.

## CI/CD and acknowledged gaps

The report openly defers CI/CD, off-host backups, recovery-key redundancy, product/legal content, and mobile WebAuthn completion. These remain open; this review does not recast them as newly discovered fixes.

Source inspection confirms CI still installs floating editable dependencies. Deployment jobs in `.github/workflows/ci.yml:1653–1669` depend on build rather than E2E/ZAP completion. The AWS reusable-workflow caller still lacks the `id-token: write` grant described in the readiness plan. `.github/actions/deploy-preflight/action.yml:49` still interpolates the production env file directly into shell source, and its migration container still executes on the runner. Hetzner rollback at `.github/workflows/deploy-hetzner.yml:151` still requires a successful switch step, excluding a partially failed switch.

Do not enable automatic production deployment until those paths are corrected and exercised, including failure injection and rollback. This review did not re-query Actions billing, branch protection, repository secrets, or production monitoring/backup state. The reported restore drill and live checks remain evidence supplied by the readiness report, not independently repeated here.

## Improvements supported by this review

The central scan predicate now rejects non-clean records; the scanner no longer treats an unknown VirusTotal hash as clean; supported outbound requests use checked-address pinning; pending uploads reserve quota under an org advisory lock; single-use credentials use conditional updates; login entry points share an MFA challenge policy; ordinary session-bound access tokens consult revocation state. These are meaningful fixes even where end-to-end gaps above remain.

## Verification and limits

- 46 focused backend tests passed: `test_url_guard.py`, `test_storage_scanner.py`, `test_worker_registration.py`, `test_requirements_lock.py`.
- The isolated admin-gate probe passed, confirming the current enrollment-only behavior.
- The transaction-boundary probe could not start its disposable Postgres container because Docker was unavailable. No SQLite substitute or production database was used.
- 27 frontend tests passed across `FilesPage.test.tsx` and `authInterceptor.test.ts`.
- No full-suite/coverage rerun, fresh dependency-advisory audit, live upload exploit, load test, or real-device accessibility audit was performed in this pass. The earlier report's 2,636-test result is not a result of this review.

Priority: address R1–R4 first; preserve the valid remediation work; close R5–R7 with behavior-level regression tests; then validate the deployment pipeline and disaster recovery outside the source tree.
