# Production-readiness review — 1 October 2026

Reviewed checkout `e8b58601` on `files-exclude-avatars-and-wordmark-link`, the September 29 remediation plan, September 30 code review, and changes since `705f3924`. Also performed read-only production and GitHub checks. No application code, infrastructure, GitHub settings, or production data was changed.

**Verdict: materially improved, but not ready for an unconditional production sign-off.** Most previous fixes hold. A concurrent-confirmation path still breaks the scanned-content guarantee, and module deactivation can still break user erasure. Off-host recovery and verified release automation remain open operational gates.

## Status (updated 2026-10-01, same day)

| Finding | Status |
| --- | --- |
| OCT-01 | **Fixed.** `confirm_upload` locks the row (`StoredFileRepository.get_for_update`); a second confirmation waits and sees the committed state. Two-session regression test copies twice on the old code, once now. |
| OCT-02 | **Fixed.** Purge and export follow the tables that exist, not module flags. Regression test (notifications switched off after use) fails on the old code with `notification_user_id_fkey`. |
| OCT-03 | **Fixed.** Purge deletes everything under `data-exports/<user>/`; an export orphaned mid-purge deletes its own archive; MinIO expires `data-exports/` after a day (S3/R2: `docs/runbooks/deploy-render.md` step 5). |
| OCT-04 | **Fixed.** `forge_scan_queue_depth`, a `scanner-worker` scrape target, `ScanQueueNotDraining` and `ScannerWorkerDown`; a test requires a down alert for every scraped worker. |
| OCT-05 | **Fixed.** The Hetzner deploy waits for backend, worker, scanner-worker and caddy to run the release image and be healthy before the smoke test; dry-run against the live box. The drill itself (C6) is still to run. Also: an unset `DEPLOY_TARGET` now deploys nothing. |
| OCT-06 | **Fixed.** Polling counts from `updated_at` (confirmation = scan start), now in the file DTO. |
| Off-host backups, branch protection, CI on the release revision | **Open.** Operator decisions; see the recommended order. |

## Findings requiring changes

### OCT-01 · P1 · Concurrent confirmations can overwrite a clean served object

**Evidence:** `backend/app/modules/storage/service.py:200`, `:275`, `:297`, `:314`; `backend/app/core/repository.py:164`; `backend/app/modules/storage/backends/s3.py` (`copy_if_match`). **Reproduced with two real PostgreSQL sessions and a fake object store.**

Moving uploads to `incoming/` fixes the straightforward overwrite after a completed confirmation. However, confirmation reads `pending` without a row lock or atomic claim. Two requests can both retain that state. Promotion conditions the copy on the source ETag, not on the destination being immutable, and replaces an existing served key whenever an incoming object exists.

The diagnostic probe used this interleaving:

1. Confirmation B reads the pending row and pauses immediately before promotion.
2. Confirmation A promotes benign bytes, confirms, and commits.
3. A separate session records a clean verdict and digest for those bytes, simulating scan completion.
4. The still-valid upload authorization is used to replace the incoming object.
5. B resumes, copies the replacement over the served key, and commits.
6. A fresh session still reads the clean verdict and original digest; `get_download_url` succeeds for the replacement bytes.

The probe passed its defect assertions. It did not submit malware or touch production. Re-enqueueing a scan after B's confirmation does not close the interval where a clean verdict authorizes unscanned content. A failure after copying also leaves the replacement behind independently of transaction rollback.

**Fix:** serialize confirmation per file, re-read state under that lock, and make promotion retry-safe without replacing an already-established destination. Account for request failure after object-store writes. Immutable object versions or a write-once promotion design provide a stronger boundary. Add the exact two-session interleaving above as a regression test; preserve tenant scoping.

### OCT-02 · P1 · Disabling a previously used module prevents user purge

**Evidence:** `backend/app/workers/jobs/gdpr.py:152–154`; `backend/app/core/migrations.py` (`upgrade`, `migration_targets`). **Reproduced against the migrated PostgreSQL schema.**

Purge chooses which references to remove using current `is_enabled()` flags. Disabling a module skips its migrations and routes; it does not remove existing tables or rows. A user with notification, file, API-key, or webhook references can therefore retain rows that the purge skips. The final user deletion fails on a foreign key and rolls the transaction back.

The probe seeded a user and notification, simulated the notifications module being disabled, and invoked `purge_user_data`. PostgreSQL raised `notification_user_id_fkey`. The new metadata coverage test lists that foreign key correctly, but does not cover the enabled → populated → disabled lifecycle.

**Fix:** base purge/export coverage on the schema and retained data, not current feature availability. Distinguish a module never installed from one disabled after use. Test both configurations, including stored-object deletion and the nulling of org-owned references.

### OCT-03 · P2 · Export archives survive expiry and account purge

**Evidence:** `backend/app/workers/jobs/gdpr.py:102`, `:181`, `:354–367`; lifecycle configuration in `docker-compose.prod.yml:375`. **Source-confirmed; no production exports were inspected.**

Exports are uploaded to `data-exports/<user>/<job>.zip`. Their 24-hour signed-link expiry does not delete the object. Purge deletes `DataExportJob` rows and objects represented by `StoredFile`, but export archives are not `StoredFile` records and have no corresponding object deletion. The configured bucket lifecycle only covers `incoming/`; no export-retention cleanup was found.

Thus even an otherwise successful purge can leave a copy of the user's exported profile, sessions, activity, and other data in storage, while removing the job record that identified it. This also creates unbounded retained storage. The private bucket still controls access; this is not a claim of public exposure.

**Fix:** delete export objects before deleting their records during purge, configure a documented archive retention policy, and reconcile failed/orphaned exports. Coordinate with exports already in flight so they cannot recreate an archive after purge. Test object absence, not only database row absence.

### OCT-04 · P2 · Scanner isolation left monitoring behind

**Evidence:** `backend/app/core/jobs.py:82`, `backend/app/core/metrics.py:65`, `docs/grafana/prometheus.yml`, `docs/grafana/alerts.yml:98–117`. **Source-confirmed.**

Scans now run on `forge:queue:scan` in a separate scanner worker. The queue-depth gauge still measures only `arq:queue`; the checked-in scrape targets omit `scanner-worker:9100`; `WorkerDown` watches only `forge-worker`. A stopped scanner can therefore leave every new upload unavailable while those worker metrics and alerts stay healthy.

The production scanner was healthy during this review. No Prometheus container appeared in the local container inventory; an external scraper/alert receiver was not independently verified.

**Fix:** expose per-queue depth and oldest eligible-job age, scrape both workers, and alert on scanner liveness and scan backlog. Test the actual notification route by stopping a scanner on a disposable deployment, not on production.

### OCT-05 · P2 · Hetzner smoke test does not establish that the whole release is healthy

**Evidence:** `.github/workflows/deploy-hetzner.yml:180–197`, particularly the sole `/health/ready` check. **Source-confirmed; automatic deployment remains undrilled.**

`docker compose up -d` can return before containers pass healthchecks. The workflow then declares success as soon as backend readiness responds. It never checks worker/scanner health, intended image identity, or the served frontend release. A scanner or worker that starts and then crashes can leave the deploy green, so the improved rollback trigger never runs.

**Fix:** verify intended images and health of backend, worker, scanner, and edge, plus the frontend release marker and an appropriate harmless background-job smoke check. Retain the migration compatibility requirement. Run the planned partial-switch and failed-worker drills before enabling automatic deploys.

### OCT-06 · P2 · Scan polling times out from presign rather than scan start

**Evidence:** `apps/web/src/features/files/fileStatus.ts:35–45`; `backend/app/modules/storage/service.py` (row creation at presign). **Source-confirmed.**

Polling is a real improvement, but its five-minute cutoff uses `file.created_at`. That timestamp starts before the upload. A valid upload that takes over five minutes—still within the ten-minute signing period and hour-long reservation—arrives on the page already too old to poll. Its scan may have just begun, yet the page immediately shows the timeout note and will not automatically enable download on completion.

**Fix:** use a scan/confirmation timestamp or a bounded client observation window for scanning state. Test a slow upload followed by a quick successful scan. Keep the backoff and stop-polling behavior for genuinely stalled scans.

## Previous findings: disposition

| September 29 item | Current assessment |
| --- | --- |
| R1: downloads not tied to scanned bytes | Direct overwrite fixed; **not closed** because of OCT-01 |
| R2: security writes rolled back on rejection | Fixed in reviewed paths; `persist_then_reject` and committed HTTP tests cover persistence |
| R3: enrollment-only admin gate | Fixed; stored session AMR is required and survives refresh; enrollment revokes other sessions |
| R4: scan-memory amplification | Substantially fixed: streaming/backpressure, dedicated queue/process, bounded concurrency; documented production burst result was not repeated |
| R5: missing shared budgets | Implemented with current 100/min user and 5,000/hour org policy; API-key exclusion is documented. Redis calls remain synchronous but have bounded waits and stand-down |
| R6: unbounded webhook responses | Fixed in reviewed implementation: raw streamed cap and total deadline, no DB connection held across outbound I/O |
| R7: scanning UI never updates | Normal path fixed and tests pass; slow-upload edge remains OCT-06 |
| CI source hardening | Lock-based installs, deploy dependencies, permission grants, secret handling, and partial-failure rollback improved; runtime evidence remains missing |

The synchronous limiter still briefly blocks the event loop during Redis operations. Its timeout/stand-down addresses the indefinite-hang defect, but async migration and realistic load measurements remain performance work. No new load benchmark was run here.

## Live production and release evidence

Observed on 1 October 2026, approximately 13:20 Helsinki time:

- Production checkout: `e8b5860`, matching the reviewed checkout. This alone is not proof of every running image's contents.
- Backend, ordinary worker, scanner, ClamAV, Redis, Postgres, PgBouncer, and MinIO report healthy; Caddy is running.
- App login and marketing pages return HTTP 200; backend readiness returns `status: ok`.
- App HTML includes CSP, HSTS, frame denial, content-type protection, referrer policy, and permissions policy.
- Root disk: 51% used, about 18 GiB available.
- Backup timer is active. The latest service run succeeded at 06:30 Helsinki time, but its own log reports **`BACKUP_REMOTE unset -- local copy only`**. September 29 and 30 runs report the same. Loss of this host still loses these backup copies. Off-host replication and an independently accessible restore/key-recovery drill remain production gates.

GitHub evidence:

- Remote `main` remains at `26ad3e3f`, not the reviewed release. The local branch is 26 commits ahead of the local `origin/main` reference, and the live GitHub main SHA matches that older reference.
- Main reports `protected: false`; repository rulesets are empty.
- Latest CI workflow result is `startup_failure` from September 21. Recent scheduled ZAP/load runs fail against the older main revision. This review did not establish a fresh root cause for those failures.
- Repository variables are empty. Therefore the documented instruction that `DEPLOY_TARGET` stays `none` is not established at repository scope; the checked-in workflow still defaults an unset value to Render. This does not mean a Render deployment succeeded or has usable credentials.

Keep manual deployment explicit until the reviewed source is integrated, CI succeeds on that exact revision, required checks protect the release branch, deployment target is explicitly configured, and the C6 failure-injection drill passes. No workflow reruns or settings changes were made during this audit.

## Verification

**292 existing backend tests passed**, across:

- Storage service/scanner, auth cryptographic utilities, CI/deploy checks, and shared rate budgets: 126 tests.
- Committed-database auth router/TOTP, GDPR purge, virus scan, and webhook delivery: 166 tests.

One shell test initially selected the Windows WSL launcher, which had no `/bin/bash`. Rerunning that test with the installed Git Bash first on PATH passed; no repository fix was needed for that environment issue.

**37 frontend tests passed** across FilesPage, fileStatus, useAvatarSrc, and authInterceptor.

**Two additional diagnostic probes passed their defect assertions** for OCT-01 and OCT-02. They used the real migrated PostgreSQL schema; the concurrent-confirm probe used a fake object store and a simulated committed scan verdict. Temporary probe files were removed after execution. The interleaving and observed foreign-key failure are documented above for regression-test implementation.

This was a targeted re-review, not a full-suite or coverage certification. No fresh dependency-advisory audit, load run, full accessibility audit, production exploit, restore drill, or CI deployment drill was performed. Product identity/support/legal content, mobile WebAuthn support, and off-host key custody remain previously acknowledged work, not newly certified by this report.

## Recommended order

1. Close OCT-01 and OCT-02 with failure-oriented regression tests.
2. Complete off-host backup and restore/key recovery; align the actual release branch and CI configuration with the deployment policy.
3. Correct export retention, scanner monitoring, full-release smoke verification, and the slow-upload polling edge.
4. Run the full required quality/coverage gates and disposable deployment failure drills on the resulting exact release revision.
