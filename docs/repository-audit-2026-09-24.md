# Repository audit — 24 September 2026

Reviewed commit: `d59b420`, plus the current working tree. Application code was not changed. Existing untracked files were left untouched.

The repository has a substantial foundation, but I would not treat it as production-ready until the high-priority findings below are resolved. The strongest concerns are authentication policy bypasses, unsafe outbound requests, downloadable unscanned files, and non-atomic security state transitions. Passing lint, types, and the sampled tests does not establish that these controls work end to end.

Priority meanings: **P1** = fix before exposing the affected capability to production users; **P2** = significant reliability, security-hardening, or usability work. Findings are based on code inspection unless explicitly marked as reproduced. No live exploitation or production traffic was attempted.

## Verification and scope

Inspected the build plan and repository laws; backend authentication, authorization, tenancy, storage, SSO, outbound webhooks, representative billing/GDPR paths, worker registration, shared repositories, deployment configuration, CI, and web/mobile flows. This is a risk-focused audit, not an exhaustive line-by-line review or penetration test.

Completed checks:

- `ruff check app`: passed.
- `ruff format --check app`: 175 files already formatted.
- `mypy --strict app/`: passed, 175 source files.
- Web `tsc --noEmit`: passed.
- Selected web tests: **23 passed**, covering FilesPage, auth interception, and stale organization responses.
- Backend URL guard and auth utility tests: **35 passed**, with three testcontainers deprecation warnings.
- Isolated probes using mocked DB/network collaborators reproduced MFA bypass through magic links, download signing for failed/unscanned files, and the missing hard-delete worker registration.

The initial Python and frontend-test attempts were blocked by filesystem sandbox access; both worked when rerun with the approved execution permissions. Docker's engine pipe was unavailable, so Postgres/Redis integration tests, migration cycles, full coverage, and live load tests were not run. No browser/device accessibility walkthrough, fresh dependency advisory scan, or production configuration verification was performed. Dependency comments and historic test results are not treated as current vulnerability or performance measurements.

## Security

### 1. P1 — Unscanned and failed files can receive download URLs

Evidence: [storage service](C:/Users/IK/Documents/saas-template/backend/app/modules/storage/service.py:260), [scan worker](C:/Users/IK/Documents/saas-template/backend/app/workers/jobs/virus_scan.py:65).

`get_download_url()` checks tenant ownership through the repository, then signs the object immediately. It never requires `status == "ready"` and `virus_scan_result == "clean"`. Upload confirmation sets `ready` before the asynchronous scan. This contradicts the scan worker's documented promise that files remain undownloadable while scanning is unavailable.

**Impact:** an organization member can download an uploaded object before a malware verdict, or after scanning fails. Deleting infected objects later does not close the pre-scan exposure window. An isolated probe confirmed signing succeeds for a file with `status="failed"` and no scan result.

**Fix and verification:** require a clean verdict at every download-signing surface, use an explicit quarantine state, and ensure the downloaded object is the same immutable object/version that was scanned. Test pending, failed, infected, and clean states through the HTTP endpoint and actual storage flow.

### 2. P1 — Alternate sign-in paths bypass local MFA

Evidence: [magic-link consumption](C:/Users/IK/Documents/saas-template/backend/app/modules/auth/service.py:506), [social sign-in](C:/Users/IK/Documents/saas-template/backend/app/modules/auth/service.py:525), [SSO sign-in](C:/Users/IK/Documents/saas-template/backend/app/modules/auth/service.py:632), [admin gate](C:/Users/IK/Documents/saas-template/backend/app/core/dependencies.py:163).

Password login challenges accounts with TOTP or WebAuthn. Magic-link and social OAuth login call `_create_session()` directly. SSO checks TOTP when the IdP has not asserted MFA, but does not apply the equivalent check to accounts protected only by WebAuthn. The superadmin gate checks whether a factor is enrolled, not whether the current session completed that factor.

**Impact:** possession of a magic link can establish a full session for an MFA-enrolled account, including an eligible superadmin. Social identity alone is also accepted without proof of upstream MFA. The code explicitly describes the social bypass as intentional; that is a policy weakness, not evidence that a second factor was verified. An isolated MFA-enabled-user probe reproduced the magic-link bypass.

**Fix and verification:** centralize the session-issuance policy for all login methods and carry verified authentication-method claims. Test TOTP-only and WebAuthn-only users, including superadmins, across password, magic-link, social, and enterprise sign-in.

### 3. P1 — Outbound URL protection does not cover the actual connection reliably

Evidence: [SSO token exchange](C:/Users/IK/Documents/saas-template/backend/app/modules/sso/oidc.py:152), [URL guard](C:/Users/IK/Documents/saas-template/backend/app/core/url_guard.py:61), [webhook request](C:/Users/IK/Documents/saas-template/backend/app/workers/jobs/webhooks.py:126).

There are two distinct gaps:

- SSO discovery validates the discovery/JWKS fetch URLs, but `exchange_code()` sends a request directly to the discovery document's `token_endpoint` without the outbound URL guard. An org settings administrator controlling the configured discovery document can point this endpoint at an internal address. The backend issues the request before verifying the returned ID token.
- The shared guard resolves a hostname, checks those addresses, then returns the original URL. HTTPX resolves it again when connecting. An attacker-controlled DNS response can therefore change between validation and use. Checking again at delivery time does not eliminate this within-request race.

**Fix and verification:** protect every server-side destination, require HTTPS for credential-bearing endpoints, and use an outbound transport/proxy that enforces the allowed destination at connection time while preserving TLS hostname validation. Test private `token_endpoint` values and changed DNS answers between validation and connection. General guidance: [OWASP SSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).

### 4. P1 — Single-use tokens and refresh rotation are not atomic

Evidence: [refresh lookup](C:/Users/IK/Documents/saas-template/backend/app/modules/auth/repository.py:173), [single-use token lookup/update](C:/Users/IK/Documents/saas-template/backend/app/modules/auth/repository.py:476), [rotation](C:/Users/IK/Documents/saas-template/backend/app/modules/auth/service.py:384).

Refresh rotation reads the session, checks `revoked_at`, and later updates it. Single-use tokens similarly perform a qualifying SELECT followed by a separate update that does not condition consumption on the unused state. Neither path locks the row or uses a conditional update with a checked result.

**Impact:** overlapping transactions can both observe a valid token and both proceed. A stolen refresh token can race its legitimate rotation and produce a second valid successor. Concurrent magic-link/reset requests can both consume the same supposedly single-use token. TOTP/backup-code read/modify/write logic also warrants the same correction.

**Fix and verification:** use row locking with state revalidation or atomic conditional consumption. Add real-Postgres tests with two independent connections and a barrier; assert one successful consumption and the intended theft response. Existing sequential replay tests do not prove concurrency safety. This race was established by code inspection, not reproduced against Postgres here.

### 5. P1 — General API rate limiting uses the wrong identity and omits the org budget

Evidence: [rate limiter](C:/Users/IK/Documents/saas-template/backend/app/middleware/rate_limit.py:16), [authenticated user resolution](C:/Users/IK/Documents/saas-template/backend/app/core/dependencies.py:84).

`_key_func()` expects `request.state.user_id`, but no application code assigns it. Authenticated calls therefore fall back to the remote IP. The configured decorators are per-route limits; I found no general 1,000/hour organization budget or explicit sliding-window strategy. The separate API-key budget does not supply these missing user/org controls.

**Impact:** users sharing a NAT/proxy can exhaust each other's budgets, while one account using multiple addresses does not share a user budget. Traffic spread across endpoints/org members is not bounded as the plan specifies.

**Fix and verification:** resolve a trusted identity before the applicable limiter runs, enforce shared user/org budgets, and explicitly select the required algorithm. Merely assigning state in a dependency is insufficient if middleware checks earlier. Test two users on one IP, one user on two IPs, and aggregate traffic across routes.

### 6. P2 — Session revocation leaves issued access tokens usable

Evidence: [logout-all](C:/Users/IK/Documents/saas-template/backend/app/modules/auth/service.py:887), [token resolution](C:/Users/IK/Documents/saas-template/backend/app/core/dependencies.py:94), [blocklist policy](C:/Users/IK/Documents/saas-template/backend/app/core/token_blocklist.py:1).

Logout, device revocation, password-reset revocation, and refresh-token theft handling revoke refresh sessions. Ordinary access tokens are deliberately not blocklisted, have no session identifier, and are accepted without consulting the revoked session. They remain usable until expiry: normally up to 15 minutes.

**Impact:** “sign out all devices” is not immediate containment of a stolen bearer token. This is a documented implementation tradeoff, but stronger security claims and user expectations should not imply immediate revocation.

**Fix and verification:** bind access tokens to a revocable session or check a user/session token version or revocation timestamp. Retain a previously issued bearer token and assert it fails after security-sensitive revocations.

### 7. P2 — Browser documents do not receive the backend's defensive headers

Evidence: [production SPA handler](C:/Users/IK/Documents/saas-template/Caddyfile.prod:88), [edge headers](C:/Users/IK/Documents/saas-template/Caddyfile.prod:114), [API headers](C:/Users/IK/Documents/saas-template/backend/app/middleware/security_headers.py:12).

Caddy serves the SPA directly. Its app-site header block sets HSTS and removes `Server`, but does not set CSP, anti-framing, nosniff, referrer policy, or permissions policy. Backend middleware applies these to API responses; it does not protect the separately served HTML document.

**Impact:** the supplied production SPA configuration lacks the browser protections claimed by the project laws. An external CDN might add them, but that was not verified.

**Fix and verification:** configure document-appropriate headers at the edge, including a tested CSP compatible with required integrations. Assert headers on `/`, a deep SPA route, and error pages in a production-style deployment.

## Performance and resource control

### 8. P1 — Storage quotas can be bypassed by reserving multiple pending uploads

Evidence: [quota check](C:/Users/IK/Documents/saas-template/backend/app/modules/storage/service.py:145), [usage accounting](C:/Users/IK/Documents/saas-template/backend/app/modules/storage/repository.py:45), [optional billing](C:/Users/IK/Documents/saas-template/backend/app/modules/billing/dependencies.py:145), [upload schema](C:/Users/IK/Documents/saas-template/backend/app/modules/storage/schemas.py:12).

Quota checks occur at presign time, but the byte total includes only `ready` files. Multiple pending uploads can each pass against the same available capacity, even sequentially, then all be confirmed without a second quota check. The cached total and lack of atomic reservation also permit concurrent oversubscription. With billing disabled, quota checking is skipped entirely; `size_bytes` has no upper bound in the request schema.

**Impact:** users with upload permission can exceed tenant storage allowances and consume unexpected storage/processing resources. This also affects non-billing products unless another deployment control supplies limits.

**Fix and verification:** reserve pending bytes transactionally, expire abandoned reservations, enforce independent operational ceilings, and reconcile actual uploaded size. Test batches of pending uploads and concurrent reservations against a small quota.

### 9. P2 — Webhook response limits are applied after buffering, and DNS blocks the event loop

Evidence: [delivery request/body truncation](C:/Users/IK/Documents/saas-template/backend/app/workers/jobs/webhooks.py:152), [worker limits](C:/Users/IK/Documents/saas-template/backend/app/workers/main.py:76), [DNS lookup](C:/Users/IK/Documents/saas-template/backend/app/core/url_guard.py:61).

`client.post()` buffers the response before `response.text` is truncated. A consumer can return an oversized body or keep delivering chunks within the read timeout. The worker's global 300-second job timeout provides an eventual bound, but the claimed short whole-request bound is absent. All job types share 20 worker slots. Additionally, synchronous `socket.getaddrinfo()` is called directly from async webhook and SSO paths.

**Impact:** malicious or slow endpoints can consume memory, hold worker/DB resources, and delay emails and scans; DNS stalls can pause unrelated async work. HTTPX documents its read timeout as a wait for a chunk, not a total response deadline: [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/).

**Fix and verification:** stream with an enforced byte limit and total elapsed-time deadline, use asynchronous/off-loop DNS, and consider isolating delivery capacity. Test oversized and slowly streamed responses and stalled DNS.

### 10. P2 — Load-test evidence is incomplete and cannot establish current capacity

Evidence: [recorded results](C:/Users/IK/Documents/saas-template/tests/load/RESULTS.md:1), [trend generator](C:/Users/IK/Documents/saas-template/.github/workflows/load-test.yml:149).

Committed results end on 1 September and repeatedly report login-burst/mixed failures with missing p95/error-rate numbers. They are historical evidence of an unresolved verification gap, not proof that this checkout currently fails. The workflow runs `api-key`, but its trend-append loop lists only the four older scripts.

**Fix and verification:** obtain a fresh successful run, retain actual numerical summaries and failure causes, include every workload in the trend, and verify the summary parser against the emitted k6 format. Establish a reproducible capacity baseline before claiming performance targets.

## Usability

### 11. P1 — MFA-enabled users cannot finish password login on mobile

Evidence: [mobile auth](C:/Users/IK/Documents/saas-template/apps/mobile/src/lib/auth.ts:98), [login screen](C:/Users/IK/Documents/saas-template/apps/mobile/app/(auth)/login.tsx:35).

The mobile auth helper throws `TwoFactorRequiredError` when the backend returns a challenge. The screen catches it as an ordinary form error; it does not navigate to a second-factor challenge or submit the temporary token. The source explicitly says the verification screen is absent.

**Impact:** enabling an authenticator or security key blocks the normal mobile password-login journey.

**Fix and verification:** implement challenge continuation and supported recovery/backup-code handling. Add device-level tests for valid, invalid, expired, and cancelled challenges. Do not use the magic-link MFA bypass as a workaround.

### 12. P2 — File actions do not reflect permissions or actionable failure states

Evidence: [FilesPage](C:/Users/IK/Documents/saas-template/apps/web/src/features/files/FilesPage.tsx:98), [backend permission requirements](C:/Users/IK/Documents/saas-template/backend/app/modules/storage/router.py:67).

FilesPage always renders upload and delete controls although ordinary members have only `files:read`. Download errors reject from a handler called with `void`, with no local catch, notification, or error state. The badge reads only `status`, so a confirmed file appears ready even while its scan verdict is absent; the page has no scan-completion polling/subscription.

**Impact:** members are offered actions that produce 403s, download failures may appear to do nothing, and users cannot distinguish usable files from files awaiting scanning. These observations come from code, not a visual accessibility assessment.

**Fix and verification:** gate management controls by permission, handle download errors explicitly, and display/update scan state. Test a read-only member, an API failure, and a pending-to-clean transition.

### 13. P2 — Deleting a pagination anchor restarts the list

Evidence: [shared cursor pagination](C:/Users/IK/Documents/saas-template/backend/app/core/repository.py:185), [file pagination](C:/Users/IK/Documents/saas-template/backend/app/modules/storage/repository.py:74).

The API calls its cursor a UUID, then retrieves that row to obtain the sort tuple. If the anchor has been soft-deleted between pages, lookup returns nothing and the cursor predicate is silently omitted. The next request starts again at page one, which infinite lists can append as duplicate entries.

**Fix and verification:** encode the immutable `(created_at, id)` tuple into a validated opaque cursor, or return an explicit invalid-cursor response instead of restarting. Test deleting the final row of page one before fetching page two.

## Implementation and assurance gaps

### 14. P1 — File deletion queues a job that does not exist

Evidence: [delete enqueue](C:/Users/IK/Documents/saas-template/backend/app/modules/storage/service.py:274), [registered jobs](C:/Users/IK/Documents/saas-template/backend/app/workers/main.py:57), [existing enqueue-only test](C:/Users/IK/Documents/saas-template/backend/tests/test_storage_service.py:505).

Deletion soft-deletes the row and enqueues `hard_delete_stored_file`. No implementation or registration exists in the application. The module comment calls it a future task. A runtime inspection confirmed the function is absent from `WorkerSettings.functions`.

**Impact:** this deletion path never performs its promised physical object cleanup; objects remain in storage absent some separate lifecycle policy or purge. Soft deletion also removes their quota contribution, allowing repeated upload/delete cycles to grow physical usage beyond the logical allowance.

**Fix and verification:** implement and register an idempotent delayed deletion job, add reconciliation for missed jobs, and test actual object absence after the grace period. Validate every string-enqueued job name against the worker registry.

### 15. P2 — CI does not enforce the stated sensitive-module coverage floors

Evidence: [backend coverage command](C:/Users/IK/Documents/saas-template/.github/workflows/ci.yml:225), [test queue fixture](C:/Users/IK/Documents/saas-template/backend/tests/conftest.py:83), [mobile workflow](C:/Users/IK/Documents/saas-template/.github/workflows/maestro-mobile.yml:16).

The backend CI command enforces 85% aggregate coverage, but I found no separate 100% gate for auth, billing, tenancy, and GDPR as required by the plan. Queue submission is mocked globally in ordinary tests; the nonexistent deletion job illustrates what enqueue assertions can miss. Scheduled mobile E2E was explicitly removed after repeated failures and is manual-only.

**Fix and verification:** enforce module-specific coverage from machine-readable reports, add security invariant/concurrency tests rather than relying on percentages, verify worker registration/execution, and restore a functioning scheduled mobile test environment. This finding concerns enforcement; it does not assert the current measured coverage is below the floors.

### 16. P2 — Python runtime builds are not reproducible

Evidence: [dependency declarations](C:/Users/IK/Documents/saas-template/backend/pyproject.toml:6), [production dependency installation](C:/Users/IK/Documents/saas-template/backend/Dockerfile:26).

Most runtime dependencies use open-ended minimum versions. The Docker builder resolves them with `pip install .`; no committed Python lock/fully pinned runtime constraints were found. CI and production can therefore resolve different versions from the same source commit. Several comments already document previous resolver-driven incompatibilities.

**Fix and verification:** commit a reproducible dependency resolution, use it for CI and production builds, and update it deliberately with advisory scanning. Isolate security tooling dependencies from the application runtime where their version constraints conflict. No claim is made here that a particular currently installed package is vulnerable.

## Additional maintenance observations

- The strict “no Any” and four-layer rules are not literally enforced by the implementation. SQLModel adapters use many `Any` casts; services import other modules' repositories, and some execute queries directly. The import-linter contract checks ordering inside each module but does not establish the full cross-module rule. Either narrow and document the accepted exceptions or enforce the intended boundaries explicitly.
- Positive controls worth preserving include Argon2 password hashing off the event loop, RS256 algorithm restrictions, hashed refresh tokens, encrypted credential columns, org predicates in the shared repository, fresh membership checks, separate API request schemas, localized UI infrastructure, and substantial security regression tests. These reduce risk but do not cancel the findings above.
- Billing concurrency/idempotency, complete GDPR purge behavior, deployment TLS/egress configuration, dependency advisories, and accessibility across real devices still need integration-level verification. No clean bill of health is implied for unsampled paths.

## Recommended order

1. Close the download-scan and MFA bypasses; protect all outbound connections; make security token consumption atomic.
2. Repair rate-limit identity and budgets, storage reservations, and physical deletion. Add regression tests that exercise these invariants through real infrastructure.
3. Complete mobile MFA, apply browser-document headers, bound outbound work, and improve file-state feedback.
4. Enforce the missing CI gates, lock Python dependencies, and establish fresh load/mobile E2E evidence before release.
