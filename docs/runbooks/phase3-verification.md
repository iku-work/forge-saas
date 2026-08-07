# Phase 3 acceptance runbook

The gate out of Phase 3 (Hardening): observability, OWASP hardening,
end-to-end and load testing, i18n expansion, performance tuning, the module
scaffold, and the operator/support tooling — all of it working together on a
live stack, not just green in CI.

Unlike `docs/runbooks/phase2-verification.md`, which is the *record* of a pass
that happened, this is a procedure to run. Run it before calling Phase 3 done,
and again after any change that touches more than one of the areas below.
Budget half a day; several steps need a live stack, a phone or simulator, and
a Stripe test key.

The per-area detail lives in the runbooks this document points at. Nothing is
restated here — a step that says "see X" means read X, because a copy of X in
this file is a copy that will be wrong in three months.

## Trigger

- Before declaring Phase 3 complete and starting Phase 4.
- After a change that spans areas — a middleware added to `app.main`, a change
  to the caching layer, an upgrade of FastAPI/SQLAlchemy/React/Expo — where
  each area's own tests pass but nothing has exercised them together.
- Before a first production deploy, regardless of which phase you think you
  are in.

**Not this runbook:** a single failing area. Go straight to that area's own
runbook or test suite — `docs/runbooks/slow-queries.md`,
`docs/runbooks/degraded-mode.md`, `docs/runbooks/pentest-prep.md`,
`docs/runbooks/maintenance-mode.md`, `tests/load/README.md`. This document is
for the integrated pass only.

## Pre-checks

Each one is here because skipping it wastes an hour later.

- **`backend/.env` filled in for real** — `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY`,
  `ENCRYPTION_KEY`, `EMAIL_BACKEND=smtp`. Not the example values.
- **A valid `sk_test_...` Stripe key** and `STRIPE_WEBHOOK_SECRET`. The Phase 2
  pass was run with an expired one and had to work around it in four steps;
  check it first with `stripe balance retrieve`.
- **Docker with enough headroom.** The full pass boots the dev stack, an
  ephemeral stack for ZAP, testcontainers for pytest, and k6. 8 GB is not
  enough on Windows.
- **A phone simulator or emulator** for the mobile steps (iOS Simulator or
  Android emulator; physical-device and real-push verification is deliberately
  out of scope — see step 6).
- **A superadmin account with 2FA enrolled.** `require_superadmin` refuses one
  without it, so several steps below are unreachable otherwise.
- **A clean `git status`.** Steps 3 and 9 regenerate files (visual baselines,
  the ADR index); you want to be able to see what they changed.

## Steps

### 1. Static gates

```bash
make lint
make test
```

**Expected:** `ruff check` and `ruff format --check` clean; `mypy --strict
app/` clean; `pnpm lint` and `pnpm type-check` clean; `check_docs.py` prints
`docs check passed`; the backend suite green with `pytest --cov` at ≥85%
overall and 100% on `auth/`, `billing/`, `tenancy/`, `gdpr/`; the web,
`@forge/i18n` and `@forge/email-templates` suites green.

The suite includes the sweeps that are the real point of Phase 3 — each one
fails the build on a *newly written* mistake, not just an existing one:

| Sweep | Fails when |
|---|---|
| `tests/test_query_budgets.py` | a list endpoint has no `BUDGETS` entry, or an N+1 appears |
| `tests/test_cache_keys.py` | a cache key is built with an inline f-string |
| `tests/security/test_impersonation_blocklist.py` | a mutating route has neither a guard nor an `# impersonation-ok:` comment |
| `tests/security/test_tenant_isolation.py` | an endpoint can be reached across tenants |
| `tests/security/test_owasp_mapping.py` | an OWASP Top-10 category has no test mapped to it |
| `tests/test_read_replica.py` | a primary-only table is reachable from a replica read |

### 2. Boot the stack

```bash
make dev
make migrate-plan
```

**Expected:** every service up; `migrate-plan` lists `core -> always applied
(auth, tenancy, audit, job_record, product_settings)` plus one line per enabled
optional module; the browser opens `https://localhost` (self-signed cert
warning once).

### 3. End-to-end journeys

```bash
pnpm --filter web e2e
```

**Expected:** the T-220 acceptance journey (signup → verify → invite →
upgrade) green on Chromium *and* WebKit; T-221's axe-core WCAG A/AA gate with
zero violations; T-224's visual snapshots within `maxDiffPixelRatio 0.001`.

Two known traps, both already documented: regenerating Linux visual baselines
from a Windows host needs the Docker recipe in the T-262 notes, and the `ar`
RTL visual test cannot currently pass. If the `ar` snapshot is the only
failure, that is the pre-existing issue, not a regression.

One expected-to-be-fine-but-check: T-263 wrapped `AppLayout`'s shell in an
extra flex column so the global banner can sit above it. The rendered result
should be pixel-identical when no banner is set (the banner renders nothing),
but `maxDiffPixelRatio` is 0.001 and this is exactly the kind of change that
moves something by a pixel. If `dashboard-*` or `settings-*` snapshots fail,
**look at the diff image before regenerating** — a baseline regenerated
without being looked at is a test that has stopped testing anything.

### 4. Load and budgets

```bash
make load-test
```

**Expected:** all four k6 scripts (`login-burst`, `browse`, `presign`,
`mixed`) pass their asserted thresholds. A budget failure is a real
regression: see `tests/load/README.md` for what each script asserts and
`docs/runbooks/slow-queries.md` for reading the result.

### 5. Security scan

**Expected:** the OWASP ZAP baseline scan green in CI against the ephemeral
deploy (T-210), and the nightly full active scan against staging (T-211)
reviewed. Note that T-211's alert-filter plan has never been validated against
a real staging run — treat a first run as exploratory and expect to tune it,
not as a pass/fail gate.

Also confirm `docs/runbooks/pentest-prep.md`'s sensitive-endpoint inventory
still matches the route table:

```bash
cd backend && python -m app.core.security_endpoint_inventory
```

**Expected:** the printed Markdown table matches the one in the runbook. It is
a point-in-time snapshot, not a CI check, so a mismatch means paste the new
one in.

### 6. Mobile

Run the Maestro flows (login, deep link, logout) against a simulator or
emulator. Physical devices and real push delivery are deliberately out of
scope for this pass.

**Expected:** all flows pass. The impersonation deep-link handoff (T-261) has
never been run on a simulator either — if you are verifying that specifically,
follow `docs/runbooks/mobile-impersonation.md` and record the result.

### 7. Operator switches

Follow `docs/runbooks/maintenance-mode.md` steps 1–6 end to end against the
live stack: raise a banner, turn maintenance mode on, confirm the exemption
table, turn it off, read back the audit trail.

**Expected:** each step's own "Expected" line. The four that prove the
integration rather than the feature:

- the banner appears on **web and on the mobile app** within 60 seconds
  without either being restarted;
- an ordinary user's browser and the mobile app both show the **maintenance
  screen** (not a wall of error toasts), and both leave it by themselves
  within a minute of the window ending — without a reload or a relaunch;
- `/api/v1/config` keeps answering while everything else 503s, and the admin
  surface stays usable throughout;
- `Admin → Audit log` shows one `elevated` `product_settings.changed` row per
  save, with a key-level diff.

### 8. Support tooling

**Expected:** `GET /api/v1/me/support-context` returns the signed-in user's
context; a superadmin can impersonate a user and the red banner appears on
web; a destructive action during impersonation is refused with
`errors.impersonation_blocked`; ending impersonation returns to the admin
session. See `docs/runbooks/support-tool-integration.md`.

### 9. Module scaffold

```bash
make new-module NAME=acme_widgets
```

**Expected:** the generator writes a complete module, prints a follow-up
checklist with `file:line` pointers, and the backend suite still passes with
the new module in the tree. Then **revert it** — this step proves the
generator works, it is not meant to leave a module behind:

```bash
rm -rf backend/app/modules/acme_widgets backend/tests/modules/test_acme_widgets.py
```

The generator also prints a follow-up checklist naming the shared files a real
module would have to edit (`BUDGETS` in `tests/test_query_budgets.py`, the
permissions matrix, `MODULE_BRANCHES`). Nothing here edits them, so there is
nothing else to revert.

### 10. Documentation

```bash
make check-docs
```

**Expected:** `docs check passed` — every ADR has its five sections and a
parseable status, `docs/adr/README.md`'s index matches the ADRs on disk, and
every runbook has the template's six headings.

## Verification

The pass is complete when all of the following are true — checked from
outside the steps that produced them, not by trusting a step that said OK:

- [ ] `make lint` and `make test` green on a clean checkout
- [ ] coverage ≥85% overall, 100% on `auth/`, `billing/`, `tenancy/`, `gdpr/`
- [ ] CI green on `main` — **check the dates on the runs, not just the badges.**
      GitHub Actions has not necessarily been executing; a green badge from a
      run three weeks ago is not evidence about today's code. This is the
      negative check for this whole document.
- [ ] Playwright green on Chromium and WebKit, axe-core clean, visual
      snapshots within tolerance (or only the known `ar` RTL failure)
- [ ] k6 budgets met on all four scripts
- [ ] ZAP baseline green; nightly full-scan result reviewed
- [ ] Maestro flows green on a simulator/emulator
- [ ] maintenance mode, the global banner and a kill switch each verified on a
      live stack, and each verified to switch **off** again
- [ ] the audit trail contains a row for every operator action taken during
      this pass
- [ ] `§7`'s cross-cutting checklist in `agents.md` ticked for any change made
      while running this

The negative check worth repeating: several of these can appear to pass while
doing nothing. A CI badge can be stale, a visual baseline can have been
regenerated to match a bug, and a maintenance-mode flag can be written to a
database nobody is reading. Each verification above is phrased to be checked
from the outside for exactly that reason.

## Rollback

Nothing here changes production, so there is normally nothing to undo. Four
steps do leave state behind on the machine you ran them on:

1. **Step 9** writes a module into the tree — `git clean` it, as the step says.
2. **Step 7** leaves switches in `product_settings`. Confirm they are off:
   `SELECT settings FROM product_settings WHERE id = 1;` should be `{}`.
3. **`make prod-compose-check`**, if you ran it, **overwrites `backend/.env`**.
   Restore your own.
4. **Step 3** may rewrite visual baselines. `git status` before committing —
   a regenerated baseline that nobody looked at is a test that has stopped
   testing anything.

## Escalation

**A step fails in an area you did not change.** Go to that area's runbook
before assuming a regression; several of these suites have documented,
pre-existing failure modes (the `ar` RTL snapshot; T-211's unvalidated alert
filters; anything Stripe-touching with an expired key).

**A test passes alone and fails in the full suite.** Two known traps in this
codebase produce exactly that, and both are documented rather than
mysterious: a `logging.config` call that disables every `app.*` logger for the
rest of the process (which makes `caplog` assertions vanish), and settings
bound at import time from `backend/.env` before `conftest` applies
`.env.test`. Check those two before bisecting.

**A just-regenerated symbol "doesn't exist" in the frontend.** The generated
API client is gitignored and rebuilt by `make gen-client`; turbo has been
known to serve a stale `dist` for it. Rebuild before believing the error.

**You cannot complete the pass.** Record which steps ran, which did not, and
why — a partial pass with an honest list is worth more than a complete one
that quietly skipped the parts that needed a phone. Do not tick a box in
Verification for a step you did not run.
