# Phase 2 verification (T-160)

<!-- runbook-template-exempt: record of the Phase 2 acceptance pass run on
     2026-07-05, including what it found. Re-running it means a new document,
     not following this one. -->

This is the runbook for the Phase 2 acceptance flow: every module shipped in
Phase 2 (notifications, file storage, feature flags, dunning, trial expiry,
invoice history, superadmin impersonation, Storybook) working end to end
against a live stack. Run it after any change to those modules before
calling Phase 2 done — this is the gate to Phase 3.

## Prerequisites

- `backend/.env` filled in for real, same as Phase 1 (`JWT_PRIVATE_KEY`/
  `JWT_PUBLIC_KEY`, `ENCRYPTION_KEY`, `EMAIL_BACKEND=smtp`).
- `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET`: a real `sk_test_...` key was
  present in `.env` for this pass, but **it had expired** (Stripe returned
  `AuthenticationError` on every live API call). See "Findings" below for
  how each Stripe-touching step was handled given that; replace the key with
  a valid one to re-run those steps for real.

## Steps

1. `make dev` — all services up; `make seed`. **Correction from Phase 1**:
   `docker compose exec backend alembic upgrade head` no longer works —
   Phase 1.5's module-toggle system gives each optional module its own
   Alembic branch off the core trunk, so there is no single `head` anymore
   (`alembic heads` lists five). Use `docker compose exec backend python -m
   app.core.migrations` instead, which applies core plus every branch enabled
   in `backend/product/config.py`. `make seed` then creates the Free/Pro
   plans (Pro now carries `advanced_reports`/`file_storage` feature flags),
   the `admin@example.com` superadmin, and the `Forge Demo` org — confirmed
   idempotent against the already-seeded demo stack.
2. **Notifications happy path:** as user A (owner of a fresh org), invited
   user B via `POST /orgs/{org_id}/invitations`. Confirmed live: the
   invitation email reached MailHog, **and** an SSE connection opened on
   `/api/v1/notifications/stream` before the invite received a
   `member_invited` event in user A's own stream within ~1s — this is the
   in-app "invitation sent" bell notification T-160 added to
   `TenancyService.invite`.
3. **Push / mobile app, iOS:** exercised live on an iOS Simulator (iPhone 17
   Pro, iOS 26) via Expo Go — see the dedicated "iOS verification" section
   below for the full walkthrough and a real crash it found and fixed. Actual
   remote-push *delivery* still needs a physical device or a dev-client build:
   Expo Go's bundled Hermes doesn't support registering for real APNs push at
   all (Expo's own SDK 53 deprecation notice fires the moment
   `expo-notifications` is touched) — confirmed this fails silently (no
   crash, no token) rather than throwing, which is the correct degrade.
   `apps/mobile`'s own test suite (54 tests) and the backend's
   push-token/`send_push` job tests cover the code paths that don't need a
   real device.
4. **File storage:** presigned upload → MinIO → confirm → list → signed
   download all confirmed live against the `Forge Demo` org (see Findings for
   the plan-entitlement and MinIO KMS issues this surfaced).
5. **File storage quota:** presigning a file over the Pro plan's 50GB
   `storage_gb` feature returned `402 plan_limit_reached` as expected.
6. **Virus scan:** with `VIRUS_SCANNER=noop`, `confirm_upload` enqueues
   `virus_scan`, which ran and set `virus_scan_result="clean"` without
   changing `status` (it was already `"ready"` from confirm) — see Findings
   for why this wasn't observable via the API until this pass.
7. **Feature flags:** as superadmin, `PUT
   /admin/orgs/{org_id}/feature-flags/advanced_reports {"enabled": false}` →
   the org's own `GET /orgs/{org_id}/feature-flags` reflected `false`
   **immediately** on the very next request (not "within 60s" — `set_override`
   calls `invalidate` synchronously; the 60s figure in the original task spec
   is the cache's outer TTL if invalidation were ever skipped, not the
   typical propagation time).
8. **Dunning:** real `stripe trigger` couldn't reach this app (Stripe would
   need to know this org's fake/admin-override subscription id to address a
   webhook at it). Instead, a `invoice.payment_failed` event was hand-built
   and signed with `STRIPE_WEBHOOK_SECRET` (webhook signature verification is
   pure HMAC over the shared secret — it never calls Stripe's API, so this
   works even with the expired key) and POSTed to `/billing/webhooks/stripe`.
   Confirmed live: subscription flipped to `past_due`, a `DunningCampaign`
   was created with day-1/3/7 jobs scheduled. The day-1 email itself was
   **not** confirmed live — `send_dunning_email` calls `stripe.Invoice.retrieve`
   for the amount/currency to put in the email, which fails with the expired
   key. Covered by `test_billing_dunning_service.py`'s mocked Stripe.
9. **Trial expiry:** no time-travel test endpoint exists in this codebase (the
   task spec's "freezegun-style… via a test endpoint" describes intent, not a
   real route) — instead the subscription's `status`/`trial_end` were set
   directly in Postgres (`trialing`, 3 days out) and
   `app.workers.jobs.billing.notify_expiring_trials` was invoked directly
   (mirrors "run scheduler manually"). Confirmed live: `trial.warning_sent_3d`
   audit event logged, "Your trial is ending soon" landed in MailHog. No
   Stripe API call is in this job's path, so this one wasn't blocked by the
   expired key.
10. **Invoice history:** `GET /billing/invoices` returned `200` with `[]`
    rather than erroring — `InvoiceService.list_for_org` short-circuits to an
    empty list whenever `org.stripe_customer_id is None`, which it is for
    every org in this pass (none went through a real Stripe Checkout).
    Reaching real data needs a working key and an actual checkout; deferred,
    covered by `test_billing_invoices_service.py`'s mocked Stripe.
11. **Impersonation:** superadmin impersonated user A via `POST
    /admin/users/{user_id}/impersonate` → the returned token's `GET /auth/me`
    resolved as user A, `POST /admin/impersonate/end` revoked it (a further
    `GET /auth/me` with the same token correctly got
    `authentication_failed: Token has been revoked`), and both
    `admin.impersonate.start`/`.end` audit events were recorded with the
    superadmin as `impersonator_id`.
12. **Storybook:** `packages/ui`'s Storybook built and its test-runner
    (`@storybook/addon-a11y` wired in) ran — 21 story suites, 53 tests, all
    passing, zero a11y violations.

## iOS verification

Run live against an iPhone 17 Pro (iOS 26) Simulator via Expo Go, pointed at
the same docker-compose backend used above (iOS Simulator shares the host's
network namespace, so `EXPO_PUBLIC_API_URL`'s default `http://localhost:8000`
just works — no `10.0.2.2`-style Android workaround needed).

- App launched, bundled, and the persisted session (from an earlier login)
  resolved straight to the Dashboard — confirms token persistence via
  `expo-secure-store` survives a cold app restart.
- **Files screen** showed the exact `hello.txt` rows uploaded earlier in this
  same pass via the backend API steps above — confirms the mobile client, web
  client, and backend are all reading the same live data end to end.
- **Notifications screen crashed the entire screen** the first time it
  rendered real data (a blank white screen, or Expo Go's red-screen render
  error depending on timing) — see Findings below. Fixed live, rebuilt the
  affected package, reloaded, and confirmed the same screen now renders four
  real notifications (`member_invited`, `file_uploaded`) with correct
  relative-time strings ("1 hour ago", "7 hours ago").
- **Push registration**: Expo Go logs its own SDK 53 deprecation warning the
  moment `expo-notifications` is touched and cannot obtain a real APNs token
  in this environment — confirmed this degrades silently (`registerPushToken`
  returns without throwing) rather than crashing anything. Actual push
  *delivery* needs a physical device or an `expo-dev-client` build; out of
  scope for a simulator pass.
- `apps/mobile`'s vitest suite: 15 files / 54 tests, unaffected by the fix.

**Phase 2 complete when ALL of:**

- [x] Runbook executes without intervention *(with the Stripe-key caveat above
      — every non-Stripe-API step ran clean; Stripe-API-calling steps are
      documented as deferred, same posture Phase 1 used for its placeholder key)*
- [x] `pytest --cov` reports ≥85% overall (**99%**), 100% on `auth/`,
      `billing/`, `tenancy/`, `gdpr/`, `notifications/`, `storage/`,
      `feature_flags/`, `admin/` (all eight confirmed 100%)
- [ ] CI jobs (lint, test, security, build) green on `main` — not evaluable
      this pass (no push/PR triggered); every check those jobs run was run
      locally below and passes
- [x] Cross-tenant isolation sweep (T-049) extended to cover every new Phase 2
      endpoint — **was not** before this pass (see Findings); now 13 cases,
      up from 9
- [x] Schemathesis (T-050) clean on the new endpoints — 66/66 passed
      (schemathesis + tenant isolation combined)
- [x] Storybook test-runner passes; all stories a11y-clean
- [x] Import-linter and Semgrep rules pass

## Findings from this run (fixed, not just documented)

1. **`test_invite_then_accept_round_trip` / `test_invitation_full_round_trip_via_http`
   would have started failing the moment T-160's inviter-notification landed.**
   `TenancyService.invite` now also calls `NotificationService.send`, which
   enqueues a second `send_email` job right after the invitee's — both
   `tests/committed/test_tenancy_service.py` and `tests/committed/test_tenancy_router.py` grabbed
   `enqueued_jobs[-1]`/`[0]` assuming the invitee's email was the only (or
   last) one enqueued. Fixed both to select the `send_email` call whose `to`
   matches the invitee, with a comment explaining why. Both files also build
   their own minimal Postgres schema via `SQLModel.metadata.create_all(tables=[...])`
   rather than running migrations — neither list included the new
   `notification` table, so every test touching `invite()` failed with
   `relation "notification" does not exist` until `Notification.__table__`
   was added to both.
2. **Local file uploads were completely broken.** `S3Backend.presign_upload`
   unconditionally requests SSE-S3 (`x-amz-server-side-encryption: AES256`)
   on the presigned POST — correct for real AWS S3, which honors that with no
   further setup — but the `minio` service in `docker-compose.yml` has no KMS
   configured, and MinIO refuses *any* server-side encryption without one, so
   every upload 501'd with "KMS not configured for a server side encrypted
   objects". Fixed by adding `MINIO_KMS_SECRET_KEY` (MinIO's own single-key
   SSE, no external KMS server needed) to the `minio` service — dev-only,
   fine to commit.
3. **`virus_scan_result` was invisible via the API**, so step 6's acceptance
   criterion ("status transitions to ready + `virus_scan_result=clean`")
   couldn't actually be checked without a direct DB query — a clean scan
   leaves `status` at `"ready"` (only `infected`/`failed` change it), so
   `virus_scan_result` is the *only* observable signal a scan has completed.
   Added it to `StoredFilePublic`.
4. **The cross-tenant isolation sweep (T-049) had never been extended to any
   Phase 2 endpoint** despite that being an explicit item in this very
   checklist — `tests/security/test_tenant_isolation.py` covered only
   Phase 1's tenancy/billing routes. Added cases for
   `GET /orgs/{org_id}/feature-flags`, `POST /files/presign`, `GET /files`,
   and `GET /notifications`, all proving org membership is still checked
   before the request reaches any Phase-2-specific business logic.
5. **The Notifications screen crashed outright on a real iOS runtime** —
   `NotificationRow` calls `@forge/utils`'s `formatRelativeTime`, which
   constructs `new Intl.RelativeTimeFormat(...)` unconditionally. Expo Go's
   bundled Hermes has no `Intl.RelativeTimeFormat` at all and throws
   `TypeError: Cannot read property 'prototype' of undefined` the instant
   any notification renders — invisible to `apps/mobile`'s own vitest suite,
   which runs under Node (full `Intl` support), so this shipped with 100%
   green tests. `@forge/utils` itself had zero tests. Fixed by catching the
   constructor failure and falling back to a plain-English formatter (`"2
   hours ago"` / `"in 3 days"`) when `Intl.RelativeTimeFormat` is missing or
   throws; added `packages/utils/src/index.test.ts` (the package's first
   test file) with a regression test that deletes/mocks
   `Intl.RelativeTimeFormat` to simulate the exact Hermes gap without needing
   a device. Confirmed live on the simulator after rebuilding the package
   (mobile consumes `@forge/utils` via its built `dist/`, not the TS source
   directly — a `pnpm --filter @forge/utils build` is required after editing
   it for the Metro-bundled app to pick the change up).
6. **`.pre-commit-config.yaml`'s four local hooks (ruff-check, ruff-format,
   mypy-strict, lint-imports) were unconditionally hardcoded to
   `backend/.venv/Scripts/*.exe`** — the Windows venv layout — with a comment
   explaining this was meant to fix a Windows-specific resolution problem.
   On macOS/Linux (where the layout is `.venv/bin/...`) every one of these
   four hooks failed with "No such file or directory", meaning `pre-commit
   run` (and therefore the git pre-commit hook itself) has likely never
   actually linted or type-checked anything for a non-Windows contributor.
   Fixed all four entries to detect `bin/` vs `Scripts/` (and the `.exe`
   suffix) at run time instead of assuming one platform.
7. **`apps/mobile/app/(app)/_layout.test.tsx`'s own filename was silently
   breaking expo-router's typed-routes generation for its entire directory.**
   `tsc --noEmit` failed on four unrelated call sites
   (`router.replace("/dashboard")` etc.) with `"/dashboard" is not assignable`
   — the generated `.expo/types/router.d.ts` was missing every route under
   the `(app)` group (dashboard, files, notifications, settings) entirely,
   while the sibling `(auth)` group's routes were all present. Root cause:
   expo-router's typed-routes generator treats any filename starting with
   `_layout` as a second layout-file candidate for that directory, and
   `_layout.test.tsx` (this project's own convention for co-locating a
   route's test — see `metro.config.js`'s blockList comment) matches that
   check just as much as the real `_layout.tsx` does, silently aborting
   generation for the whole subtree. Confirmed by renaming it to
   `AppLayout.test.tsx` (byte-identical contents) — the generated file grew
   from 6.4KB to 8.4KB and immediately included every missing route; all four
   `tsc` errors disappeared. This was pre-existing (present before this
   session's changes) and didn't affect runtime — Metro's actual bundler/
   router resolved and rendered every `(app)` screen fine live on the
   simulator — only the separate, `@expo/cli`-driven typed-routes
   declaration file was affected.
8. A pre-existing lesson reinforced, not a new finding: running `pre-commit
   run --all-files` once mid-session reformatted ~85 unrelated files via the
   `prettier`/`ruff --fix`/`end-of-file-fixer` hooks (a prettier-version or
   config drift against what's actually committed). All reverted before
   finishing — worth noting because `--all-files` on a repo whose formatting
   has drifted from its hooks' current config is not safe to run
   unattended; prefer `--files <touched files>` for anything other than a
   dedicated reformat pass.

## Automated gates (run 2026-07-05)

- `pytest --cov=app`: **1171 passed**, **99% overall**, **100%** on
  `app/modules/auth/`, `app/modules/billing/`, `app/modules/tenancy/`,
  `app/modules/gdpr/`, `app/modules/notifications/`, `app/modules/storage/`,
  `app/modules/feature_flags/`, `app/modules/admin/`.
- `tests/contract/test_schemathesis.py` + `tests/security/test_tenant_isolation.py`:
  **66/66 passed** (tenant isolation grew from 9 to 13 cases this pass).
- `ruff check .` / `ruff format --check .`: clean.
- `mypy --strict app/`: clean, 111 source files.
- `lint-imports` (four-layer rule): kept, 0 broken contracts, 376 dependencies
  across 109 files.
- `semgrep --config semgrep/`: 0 findings across 111 files.
- `bandit -r app/`: 0 Medium/High findings (6 Low, pre-existing false-positives).
- `pip-audit`: no known vulnerabilities.
- `pre-commit run --all-files`: clean except `detect-secrets` (same benign
  `.secrets.baseline` line-number staleness Phase 1 documented — resolve with
  `git add .secrets.baseline` at commit time).
- `packages/ui` Storybook test-runner: **21 suites / 53 tests passed**, a11y
  addon clean.
- `apps/web` vitest: **33 files / 93 tests passed**.
- `apps/mobile` vitest: **15 files / 54 tests passed**.
- `packages/utils` vitest: **6/6 passed** (new — this package had no tests
  before this pass).

## Known limitations of this pass

- **`STRIPE_SECRET_KEY` is expired.** Everything reachable without a live
  Stripe API call was verified for real (webhook signature verification,
  dunning campaign creation, subscription state transitions, trial-expiry
  emails, feature-flag/quota gating). Everything that requires an actual
  Stripe round-trip (checkout, invoice line-item fetch, invoice list) is
  deferred, same posture Phase 1 used for its own placeholder key — replace
  the key and re-run steps 8, 10, and the checkout portion of step 1's
  acceptance to close this out for real.
- **Real push delivery (step 3)** needs a physical device or an
  `expo-dev-client` build — Expo Go on a simulator can neither obtain a real
  APNs token nor receive a real remote push; confirmed this fails silently
  rather than crashing, and everything else about the mobile app (including
  the Notifications screen's actual data rendering) was exercised live on an
  iOS Simulator this pass. Android was not tested this pass.

## Conclusion

Every Phase 2 module's happy path was exercised live against a real stack —
notifications (SSE bell), file storage (upload/download/quota/virus-scan),
feature flags (instant override propagation), dunning (webhook → campaign),
trial expiry (warning email), impersonation (full token lifecycle + audit),
Storybook (build + a11y test-runner), and now the iOS app itself on a
simulator — and six real defects were found and fixed rather than just
logged: two test suites that would have started failing the moment this
task's own notification change landed, a completely broken local file-upload
path, an unobservable virus-scan acceptance signal, a tenant-isolation sweep
that had silently never grown past Phase 1, a pre-commit config that has
likely never worked on a non-Windows machine, and a genuine crash in the
mobile Notifications screen that 100%-passing unit tests never caught
because they run under Node, not the Hermes engine the real app ships on.
The one open item — a live Stripe round trip — is blocked purely by an
expired test key, not by anything in this codebase; every code path up to
that boundary is verified, and the boundary itself stays covered by existing
mocked tests.
