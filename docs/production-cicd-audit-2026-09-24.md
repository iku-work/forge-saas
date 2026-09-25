# Live production and CI/CD audit — 24 September 2026

This extends the [repository audit](C:/Users/IK/Documents/saas-template/docs/repository-audit-2026-09-24.md) with read-only checks of `mycalendar.party`, `app.mycalendar.party`, GitHub Actions, and the server reached through `forge-prod`.

**Assessment:** the site is online, but production has material security and operational gaps, and CI/CD is not currently providing a working release gate. The most urgent new finding is the production-selected malware scanner: it is an explicitly documented stub that classifies unknown files as clean.

No production configuration, containers, database records, files, workflows, or GitHub settings were changed. No accounts were created, emails sent, uploads performed, billing actions taken, or load/attack scans run. No secret values or user records were printed. The scanner reproduction used a local mock response.

## Verified deployment state

Observed around 11:22–11:34 UTC:

| Item | Observation |
|---|---|
| Host checkout | `26ad3e3f103d66b4788512a1102895567a3794c9` |
| Difference from audited `d59b420` | GitHub comparison reports no changed files; the newer revision is ahead with the same file content |
| Running backend and worker | Local image `forge-saas-backend`; same image ID; created September 21 |
| Edge | `caddy:2-alpine`, serving host-bound web/marketing build directories |
| Compose configuration | Production + TLS + **host-build** overlays, not the registry overlay |
| Services | Backend, Postgres, Redis, PgBouncer, and MinIO healthy; worker and Caddy running |
| Public listeners | Host sockets on 22, 80, 443; application/DB/cache ports not published by Docker |
| Storage | 38 GB root filesystem, 25 GB used, 12 GB available, 68% utilization |
| Docker build cache | 17.14 GB; 13.16 GB reported reclaimable |
| Enabled public modules | Billing, notifications, file storage, feature flags, GDPR, admin, social OAuth, TOTP, multi-org, marketing |
| Disabled modules | Outbound webhooks, enterprise SSO, public API, usage metering |

Compared six sensitive source files inside the running backend with the host checkout: auth service/repository, storage service/scanner, rate limiter, and worker registration all matched. That provides stronger evidence than relying on the host's Git revision alone, but is not complete image provenance.

## High-priority findings

### PROD-01 — P1: Production uses a fail-open malware-scanning stub

**Observed:** the backend/worker environment selects `VIRUS_SCANNER=virustotal`, and the configured API key is present. The deployed scanner source matches [scanner.py](C:/Users/IK/Documents/saas-template/backend/app/modules/storage/scanner.py:140).

This implementation performs a reputation lookup only. It does not download or scan unknown content, and a VirusTotal 404 becomes `ScanResult(clean=True)`. A local mock reproduced that result. The module's own documentation explicitly says this behavior is unsuitable for a real deployment relying on scanning.

Upload confirmation also populates `checksum_sha256` from the object's ETag, rather than computing a SHA-256 digest. An ETag is not a reliable substitute for that digest. Regardless of hash representation, an unknown lookup must not constitute a clean scan.

**Impact:** new malicious content can be certified clean without scanning. This compounds repository finding 1, where download signing does not require a clean verdict in the first place.

**Action:** quarantine unknown content; deploy a working scanner; require a verified verdict before download; compute/validate the intended content digest and bind the verdict to the exact object version. Add a production startup guard against this stub, not only against `noop`. Retest existing stored files through a controlled remediation plan.

### PROD-02 — P1: The internet-facing Caddy container receives application secrets

**Observed:** presence-only inspection confirmed `JWT_PRIVATE_KEY`, `ENCRYPTION_KEY`, `DATABASE_URL`, `STRIPE_SECRET_KEY`, and the VirusTotal key inside Caddy's environment. Values were not displayed.

Cause: [docker-compose.prod.yml](C:/Users/IK/Documents/saas-template/docker-compose.prod.yml:249) supplies the entire backend environment file to Caddy, which needs only a small set of host/proxy settings.

**Impact:** an edge-container compromise would expose credentials enabling token forgery, database access, decryption, and integration access. Network separation does not compensate for distributing the credentials into the edge process.

**Action:** give Caddy an explicit allowlist of non-secret configuration, recreate it through a reviewed deployment, and verify the sensitive keys are absent. This audit found unnecessary exposure, not evidence of compromise.

### CICD-01 — P1: Current CI and scheduled assurance are not executing

The latest [CI run](https://github.com/iku-work/forge-saas/actions/runs/35598050203) ended in `startup_failure` with **zero jobs**. Recent preceding CI runs show the same state.

Today's [load test](https://github.com/iku-work/forge-saas/actions/runs/35982379942) and [ZAP full scan](https://github.com/iku-work/forge-saas/actions/runs/35974057624) each failed in seconds with **zero steps**. Their check annotations explicitly attribute the block to failed account payments or a spending limit needing an increase. The API does not distinguish which of those account conditions applies.

There is also a concrete workflow permission mismatch: the calling `deploy-aws` job in [ci.yml](C:/Users/IK/Documents/saas-template/.github/workflows/ci.yml:1667) does not grant `id-token: write`, while the called [AWS workflow](C:/Users/IK/Documents/saas-template/.github/workflows/deploy-aws.yml:58) requests it. Repository defaults are read-only. Reusable workflows cannot elevate permissions beyond the caller. [GitHub documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations).

**Evidence distinction:** the billing/spending block is confirmed by annotations for load/ZAP. The permissions mismatch is confirmed in source and is a likely CI startup cause; the exact startup diagnostic was not available through the inspected run API, so it is not asserted as the sole cause.

**Action:** resolve Actions account funding/limits, fix the caller/callee permission contract, and obtain fresh successful runs. These red badges currently mean “not tested,” not measured security or performance failures. No workflow was rerun by this audit.

### CICD-02 — P1: The documented production deployment is not configured

**Observed through GitHub API:** repository Actions variables = none; repository Actions secrets = none; environments = none; Hetzner deployment workflow runs = zero. Organization-level inherited secrets were not inspected.

The workflow chooses Render when `DEPLOY_TARGET` is unset. The actual server is using the Hetzner/Compose host-build arrangement. Thus repairing CI alone would not cause a correct deployment to this box; the declared default target is wrong for it. Public-origin build variables are also absent.

**Action:** explicitly choose the intended deployment target, configure its required inputs/secrets, and prove the release path against a non-production target first. Until that is done, treat production as manually deployed and document its exact rollback procedure.

### CICD-03 — P1: Main has no enforced merge gates, and deploy does not wait for all release tests

**Observed:** the branch-protection API reports `main` is unprotected; repository rulesets and effective rules for `main` are empty. There is no configured production environment with deployment restrictions.

Independently, [build](C:/Users/IK/Documents/saas-template/.github/workflows/ci.yml:1150) depends on `test` and `security`, and deploy depends only on `build`. Web E2E and ZAP baseline also start after `build`, so deployment can proceed while those checks are still running or eventually fail. Other independent assurance jobs are not necessarily in the deployment dependency chain either.

**Action:** enforce the chosen required checks on main and make deployment depend on a complete release gate. Configure deployment branch restrictions and any desired approvals explicitly. A workflow existing in the repository does not make it mandatory.

### CICD-04 — P1: Runner-side migrations cannot reach the current private database topology

The shared [preflight action](C:/Users/IK/Documents/saas-template/.github/actions/deploy-preflight/action.yml:74) runs the production image and migrations directly on a GitHub-hosted runner, before SSH is configured.

**Observed production topology:** `DATABASE_URL` addresses Compose service `pgbouncer:6432`; Postgres/PgBouncer have no public host port. The action creates no SSH tunnel, private-network route, or service-name mapping. The host's exact env file therefore cannot support this migration command from that runner. Redis settings also refer to host-mounted certificate paths which the generic runner container does not mount.

**Action:** execute the migration image on the server's private Compose network, or provide a deliberately secured network path and required certificates. Preserve migration-before-switch behavior without making the database public.

### CICD-05 — P1: Partial deployments can escape rollback, and rollback does not restore a complete release

Evidence: [Hetzner deployment](C:/Users/IK/Documents/saas-template/.github/workflows/deploy-hetzner.yml:108).

- Rollback runs only when the entire `switch` step reports success. A Compose update that replaces some containers and then fails leaves `switch` failed, so rollback is skipped despite partial mutation.
- The new environment and checkout replace the old ones before the switch. Rollback restores image references only; it leaves the new configuration/Compose checkout behind. Previous images may be incompatible with those settings.
- The current backend image reference is `forge-saas-backend`, not a SHA-tagged registry image. The rollback tag extraction would treat that entire name as a tag and construct a nonexistent GHCR reference. Caddy is also a different image family today.
- The smoke test checks only `/health/ready`. It does not establish the deployed revision, web bundle, or worker health, and can accept a healthy old backend while a replacement is still coming up.

**Action:** capture exact per-service image digests and the matching configuration before any mutation; trigger recovery for partial switch failures; verify the intended revision plus edge and worker health. Exercise rollback in a disposable environment before enabling automated production deployment.

## Other production and release findings

### PROD-03 — P2: The app's HTML is missing defensive browser headers

Live response inspection confirmed the earlier repository finding:

| Surface | Result |
|---|---|
| Marketing HTML | CSP, anti-framing, nosniff, referrer policy, permissions policy, HSTS present |
| App login HTML | HSTS and cache control present; **CSP, anti-framing, nosniff, referrer policy, permissions policy absent** |
| API health JSON | Backend defensive headers present |

**Action:** add a tested document policy to the app's Caddy handler. The marketing site's protections do not apply to the separate app origin. Verified at [app login](https://app.mycalendar.party/en/login).

### PROD-04 — P2: Disk growth and resource isolation remain weak

The 38 GB host is 68% full, with a 17.14 GB Docker build cache. This is not an immediate disk-full incident, but the live deployment still uses the build-on-host pattern that the deployment runbook attributes previous outages to. A weekly Docker prune cron exists; it does not make builds immutable or prevent an intervening spike.

Inspected containers have no memory limits and use the `json-file` logging driver with no per-container rotation options. No daemon-level policy was independently established. Backend/worker/edge share the same small host with the database and object store.

**Action:** move to tested prebuilt releases, bound logs and resource consumption, set disk alerts, and retain a deliberate image rollback window. Do not indiscriminately prune recovery images. Current low CPU/memory usage is an idle snapshot, not a load-capacity result.

### PROD-05 — P2: Recoverability is not demonstrated

One local SQL backup was found under `/home/forge/backups`, dated **September 20 at 16:50 UTC**, about four days before inspection. No backup-related user cron or system timer was observed. The sole user cron is Docker maintenance. The backup file mode is `0664`, its directory `0775`, and the home directory `0750`; the home restriction limits access, but the dump itself is more permissive than necessary.

No off-host backup, object-store backup, point-in-time recovery, encryption-key recovery, or successful restore drill was established. Root cron and provider-level snapshots were not accessible, so **this is an assurance gap, not proof that no other backups exist**.

**Action:** document and verify scheduled off-host DB/object backups, key custody, retention, restore-point objectives, and a timed restore drill. Tighten backup permissions. The [runbook](C:/Users/IK/Documents/saas-template/docs/runbooks/backup-restore.md:1) itself still labels its restore procedure unexecuted.

### PROD-06 — P2: Worker failures lack demonstrated detection

The worker has no Docker health check, Sentry is not configured in the backend environment, and no Prometheus/Grafana/Alertmanager or equivalent service was found in the running containers or filtered host service inventory. The worker log sample returned no lines; that does not demonstrate successful job execution. The readiness endpoint checks DB/Redis, not that jobs are being processed.

External monitoring may exist and was not inspected. **Action:** verify worker heartbeat and queue-age alerts, scanner failure reporting, disk alerts, and an externally tested notification path. A process marked “Up” is insufficient for email, deletion, and scanning assurance.

### CICD-06 — P2: Secrets and manual inputs are interpolated directly into shell programs

Evidence: [env-file construction](C:/Users/IK/Documents/saas-template/.github/actions/deploy-preflight/action.yml:50), [remote checkout](C:/Users/IK/Documents/saas-template/.github/workflows/deploy-hetzner.yml:114).

The entire production env secret is substituted into a double-quoted shell literal. Characters such as `$`, backticks, quotes, or command-substitution syntax in legitimate secret values can be interpreted by the shell, corrupting the materialized file or executing unintended commands. The manual image tag is also inserted into shell code without validating that it is a commit SHA.

This is not an unauthenticated remote exploit: changing those inputs requires privileged workflow/secret access. It is nevertheless unsafe handling of deployment inputs.

**Action:** transfer secrets through environment variables or a safe file interface, write with restrictive permissions, validate allowed image refs, and keep inputs out of generated shell syntax. Ensure secret-file and registry-login cleanup also occurs on failure.

### CICD-07 — P2: The edge image skips the final image vulnerability gate

The backend image is scanned before push, but the [edge image step](C:/Users/IK/Documents/saas-template/.github/workflows/ci.yml:1309) builds and pushes directly. The final Caddy/OS image is not subjected to the same Trivy gate. Workflow actions are referenced by mutable version tags; repository policy allows all actions and does not require SHA pinning.

**Action:** scan both final images before publication/deployment, identify releases by digest, pin third-party actions deliberately, and retain scan/SBOM evidence per release. No specific current image CVE is asserted; a fresh advisory scan was not performed.

## Live usability findings

### UX-01 — P2: Both advertised social login providers are unavailable

[The login page](https://app.mycalendar.party/en/login) displays Google and GitHub buttons. Both corresponding production start endpoints return **404 `oauth_unknown_provider`**. Presence-only inspection confirmed their client IDs/secrets are absent while the generic OAuth module toggle is enabled.

**Action:** expose provider-specific availability from the backend and render only configured providers, or complete provider setup and verify the callback flow. This was checked with GET requests; no OAuth consent or account creation was performed.

### UX-02 — P2: Public content and onboarding still contain template placeholders

The site advertises ForgeSaaS, the public config and footer link support to `support@example.com`, and both [privacy](https://mycalendar.party/en/privacy/) and [terms](https://mycalendar.party/en/terms/) explicitly remain template documents. The privacy page instructs the operator to fill in actual processors rather than identifying this deployment's providers.

The registration screen offers immediate sign-up but displays no terms/privacy links, even though the published terms say account creation signifies acceptance. This is an observed content/onboarding mismatch, not a legal determination.

**Action:** replace placeholders with the intended product identity, a functioning support route, and reviewed deployment-specific disclosures; link those documents at registration. If this is intentionally a public template demonstration, identify it clearly and align its account/data expectations accordingly.

## Positive observations and limits

- HTTPS worked with normal certificate validation. HTTP redirected to HTTPS. Marketing has a restrictive CSP and appropriate immutable/static cache behavior; app HTML uses `no-cache`.
- `/health/ready` returned 200; `/metrics` and `/openapi.json` returned 404 publicly.
- Production mode, forced HTTPS, and a specific allowed app origin are configured. Backend and worker run as the `app` user. Inspected env files and the internal private-key file have mode `0600`.
- Redis uses TLS with a configured CA. PostgreSQL's app-facing DSN uses `sslmode=require`; this establishes encryption configuration, not hostname verification. Consider `verify-full` with the appropriate CA/name if the internal threat model requires peer authentication.
- Only a small number of ordinary HTTP requests were made. Indicative single samples: marketing TTFB 234 ms, login HTML 164 ms, readiness 201 ms. These are network-path samples, not percentiles, Core Web Vitals, or load results.
- The prior repository findings for magic-link MFA, non-atomic token handling, rate-limit identity, scan/download gating, and missing physical-deletion worker remain relevant to enabled production capabilities because the inspected deployed source matches. Enterprise SSO and outbound-webhook SSRF findings are **latent**, not presently exposed through enabled modules on this deployment.
- No authenticated account workflow, real upload/scan/delete, email delivery, checkout, database restore, or rollback was exercised. Firewall rules, root schedules, provider snapshots, external alerting, and Cloudflare configuration were not verified. Passwordless sudo is unavailable to the SSH user; no attempt was made to bypass that restriction.

## Suggested remediation sequence

1. Fix the production scanner/download gate and remove application secrets from Caddy; address the enabled authentication/rate-limit issues from the repository report.
2. Restore working CI execution and required branch gates. Explicitly configure the correct deploy target.
3. Test private-network migration, immutable release rollout, revision-aware smoke tests, and full rollback before moving this host onto automation.
4. Establish verifiable backups, worker/queue alerts, disk/log limits, and a restore drill.
5. Fix app document headers, social-login availability, support details, and registration disclosures; then perform authenticated acceptance testing with a dedicated test account.
