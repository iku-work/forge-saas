# Module toggle guide (T-066, T-291)

The module toggle system (T-060–T-065) makes every optional module —
billing, GDPR, and whatever the product adds later — a single switch in
`backend/product/config.py` (+ the mirrored `product.config.ts` at the repo
root for the frontend). Flipping a module off removes its routes (404, not
hidden), its DB tables (never created), its UI (absent, not disabled) and —
since T-290 — a whole app's build, together.
`backend/tests/modules/test_module_toggles.py` and CI's `module-matrix` job
are the automated proof of this; this runbook is the manual walkthrough.

## What's actually toggleable today

`EnabledModules` (`backend/app/core/modules.py`) declares fourteen module
names, and they are not all at the same stage. `TEMPLATE.md`'s module table is
the per-module contract; what matters operationally is what a toggle actually
removes today:

- **Routes and tables both.** `billing`, `gdpr`, `notifications`,
  `file_storage` — a real router, `models.py`, and a migration branch each.
  `notifications` and `file_storage` `require` billing, so disabling billing
  disables them too (enforced by `validate_modules` at startup: an enabled
  module that requires a disabled one is a `RuntimeError`, not a silent
  inconsistency).
- **Routes only.** `feature_flags` and `admin` own routers but no tables of
  their own.
- **UI only.** `multi_org` has no routes to remove — the frontend reads it from
  `/config` and hides the org switcher.
- **A build, not a router.** `marketing` is the only one of the fourteen that
  removes no HTTP surface at all and still removes something real: the whole
  `apps/marketing` build (T-300). It is the reason the module matrix had to
  grow a CI job — see Verification.
- **Nothing yet.** `oauth` and `totp_2fa` ship routers in `build_router()`'s
  always-on block, so their toggles do not currently remove anything;
  `usage_metering` and `outbound_webhooks` are Phase-2 slots; and T-290's
  `public_api` and `enterprise_sso` are Phase-4 slots whose surface arrives
  with T-310 and T-331 respectively. Toggling any of these today changes only
  what `/api/v1/config` reports.

`backend/tests/modules/test_module_toggles.py`'s `MODULE_SURFACE` is the
machine-readable version of this list, one entry per module, with a note
explaining every entry that is deliberately empty. If this section and that
table ever disagree, the table is the one that is tested.

Treat the steps below as the worked example; the mechanism is identical for
every module added later.

One rule is worth stating on its own, because it is the one that bites when a
product edits `product/config.py` rather than reading it. **Dependency edges
belong to the template, not to your config.** Writing
`notifications=ModuleConfig(enabled=True)` constructs a fresh `ModuleConfig`
whose own `requires` is empty — the declared edge lives on the field default in
`app/core/modules.py`, and `validate_modules` reads it from there
(`declared_requirements()`), so the boot-time check holds no matter how the
entry is spelled.

## Trigger

- A product doesn't need one of the optional modules — an internal tool with
  no Stripe account, a deployment where GDPR endpoints are handled elsewhere.
- A module that was switched off is needed after all, on a database that has
  already been running without it (that is Rollback, below).
- A new product-specific module needs a config slot.

Do this **before first deploy** wherever possible. Everything here works on a
running system, but disabling a module whose tables already hold rows leaves
those rows in place and unreachable — the toggle skips the migration, it does
not drop anything.

Not this runbook: writing the module itself ([new-module.md](new-module.md)),
or turning a *feature* on and off for one org, which is feature flags
(`app/modules/feature_flags`), not module toggles.

Also not this runbook: turning a module off **right now**, during an
incident, without a deploy. That is T-263's emergency kill switch —
[maintenance-mode.md](maintenance-mode.md). The two are deliberately
different operations: a module toggle is a deploy-time decision about what
the product *is*, and it removes routes, tables and UI together; a kill
switch leaves all of that in place and makes the module's routes answer 503
until you turn it back on. Use the kill switch to stop the bleeding, and this
runbook only if the module is genuinely never coming back.

## Pre-checks

- A clean working tree. Every change here is to committed config
  (`backend/product/config.py`, `product.config.ts`), and the diff is the
  record of what a deployment actually runs.
- Know the dependency edges before flipping anything: `notifications` and
  `file_storage` both `requires=["billing"]`. `validate_modules` turns a
  violation into a `RuntimeError` at startup rather than a silent
  inconsistency, so getting this wrong costs a failed boot, not corrupt data.
- Know whether the module's tables already hold rows. `make migrate-plan`
  prints what would be skipped; the rows themselves need a `psql` count.
- Both files change together. `backend/product/config.py` and
  `product.config.ts` are mirrored by hand — the backend 404s and the frontend
  hides, and a half-applied toggle gives you one without the other.

## Steps

Each subsection below is a complete, independent scenario.

### Disabling billing (example: internal tool, no Stripe)

1. In `backend/product/config.py`:
   ```python
   MODULES = EnabledModules(
       billing=ModuleConfig(enabled=False),
       notifications=ModuleConfig(enabled=False),  # requires billing — must go off with it
       file_storage=ModuleConfig(enabled=False),   # requires billing — must go off with it
       # ...leave the rest as-is
   )
   ```
   Leaving `notifications`/`file_storage` enabled while `billing` is
   disabled raises `RuntimeError: Module 'notifications' requires 'billing'
   which is disabled` at startup — fail fast, not a silent gap.
2. In `product.config.ts`, mirror the same flags: `billing: false,
   notifications: false, fileStorage: false`.
3. Remove `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` from `backend/.env`
   (or leave them unset). `Settings` validation (T-065) only requires them
   when `billing.enabled` is `True`; with billing off, the app starts
   cleanly without them.
4. `make migrate-plan` — prints a per-module summary; confirms `billing` is
   listed `disabled -> skipped`.
5. `make migrate` — applies core + `gdpr`'s migrations only. `plan`,
   `subscription`, and `webhook_event` are never created.
6. `make dev` — confirm `curl -sf http://localhost:8000/health/ready`
   returns `200`.
7. `curl http://localhost:8000/api/v1/config` — confirm
   `modules.billing === false` (and `notifications`/`file_storage` are also
   `false`).
8. `curl -i http://localhost:8000/api/v1/billing/plans` — `404`. The route
   doesn't exist; it isn't an authenticated/empty `200`.
9. Open the web app, log in, go to Settings — the Billing tab is absent.
   Navigating directly to `/settings/billing` 404s (`beforeLoad` in
   `apps/web/src/routes/_app.settings.billing.tsx` calls
   `bootstrapModules()` then checks `getModulesSnapshot().billing`).

### Disabling GDPR

Same mechanism, no cascade: `gdpr=ModuleConfig(enabled=False)` in both
config files removes `/api/v1/me/export`, `/api/v1/me/consents`, and
`DELETE /api/v1/me`, and skips creating `consent_record`/`data_export_job`.
Nothing else depends on `gdpr`, so it can be toggled alone.

### Disabling marketing (T-290)

`marketing` is the odd one out: it gates a **build**, not a router. Nothing
about the API changes when it goes off, which is why the in-process test
matrix has nothing to assert for it and CI's `module-matrix` job does.

1. `marketing=ModuleConfig(enabled=False)` in `backend/product/config.py`,
   `marketing: false` in `product.config.ts`.
2. `pnpm build` at the repo root stays **green**. `apps/marketing`'s build
   script runs `scripts/guard-enabled.mjs` first, which reads
   `modules.marketing` from `product.config.ts`, prints
   `marketing is disabled in product.config.ts — skipping astro build`,
   and exits `0` — not `1`, because `turbo build` has to stay green for a
   product that turned it off. No `apps/marketing/dist` is produced, and a
   `dist/` left over from before the toggle flipped is **removed**: a stale
   site is worse than no site, because T-307's Caddy would serve it and
   T-304's sitemap would list it.

   Two things that look wrong and are not. `turbo` prints
   `WARNING no output files found for task marketing#build` — correct, there
   is no output. And `product.config.ts` is a `globalDependencies` entry in
   `turbo.json`, so flipping this toggle invalidates every cached task: the
   file sits at the repo root, outside any package's inputs, and without that
   entry turbo would replay the build from before the flip.
3. Drop the marketing block from the Caddyfile (T-307). With the module off
   that block must be **absent**, not left in place serving 404s from an
   empty `/srv/marketing`.
4. In the app, the footer's "← Back to site" link, the login page's logo
   href, and marketing links in transactional emails all fall back to the app
   root. Nothing to configure — they read the module from `/config`.

Turning it off does not delete `apps/marketing/`; the source stays in the
workspace and simply never builds. Re-enabling is one boolean and a redeploy.

### Enabling public_api — and what re-enabling it costs

`public_api` ships **off** (T-290): an org-scoped API key is attack surface
nobody asked for. Off, there are no `/api/v1/api-keys` routes, no `api_key`
table, and no second kind of principal that can authenticate — a key is a
credential that is not a user, does not carry a session, and cannot be
stopped by disabling a person's account.

On, you get all three, plus the `public_api` migration branch.

> **The one re-enable with a security consequence.** Every other module in
> this template can be switched off and on again with no more effect than
> "the routes come back". `public_api` cannot. Disabling it removes the
> routes but **leaves every row in `api_key` exactly where it was** — the
> toggle skips migrations, it never drops anything. Turning the module back
> on makes each of those keys a working credential again, immediately, with
> no further deploy: keys issued months ago, to integrations that have since
> been decommissioned, to people who have since left. Nothing in the flip
> notifies anyone or expires anything.
>
> So, before re-enabling on a database that has ever had the module on:
>
> 1. `select id, org_id, name, created_at, last_used_at, revoked_at from api_key
>    order by created_at;` — read it, in full, with the org owners.
> 2. Revoke, don't reason. Anything you cannot positively account for gets
>    revoked *before* the module goes back on, not after.
> 3. Only then flip the boolean in both config files and redeploy.
>
> The same applies to a database restored from a backup taken while the
> module was on. See [backup-restore.md](backup-restore.md).

### Enabling enterprise_sso

`enterprise_sso` also ships **off**, for a build reason as much as a security
one: SAML drags `python3-saml` → `xmlsec` → the native `libxmlsec1` library
into the backend image.

- It **requires `oauth`** — T-331's OIDC SP reuses authlib's client registry
  and T-025's account linking. `enterprise_sso=True` with `oauth=False` is a
  `RuntimeError` at boot naming both modules, which is the whole point of
  `validate_modules`. There is no configuration in which half of it works.
- Off removes per-org OIDC/SAML sign-in (`/api/v1/auth/sso/*`) and the
  `sso_connection` table. Members of an org that was using SSO fall back to
  whatever other sign-in method their account has — which, for an account
  provisioned purely through the IdP, may be none. Check that before turning
  it off on a live system: it is an account-recovery problem, not a routing
  one.
- Unlike `public_api`, an inert `sso_connection` row is not a credential.
  Re-enabling exposes org SSO configuration, not a way in — but the same
  "read the table before you flip it" habit applies.

### Adding a new product-specific module

1. `make new-module NAME=invoices` — generates
   `backend/app/modules/invoices/` and `backend/tests/modules/test_invoices.py`
   from `backend/app/modules/_template/`, and prints the manual follow-ups
   with `file:line` pointers. [new-module.md](new-module.md) walks the whole
   loop; the steps below are only the toggle-specific ones.
2. Add `invoices=ModuleConfig(enabled=True)` to `EnabledModules`
   (`backend/app/core/modules.py`) and to `backend/product/config.py`.
3. Register its router conditionally in `build_router()`
   (`backend/app/api/v1/__init__.py`), following the existing `if
   modules.billing.enabled: ...` / `if modules.gdpr.enabled: ...` pattern.
4. Add its branch to `MODULE_BRANCHES` in `backend/app/core/migrations.py`
   so `migration_targets()` only asks Alembic for `invoices@head` when the
   module is enabled, and create its first migration under
   `backend/alembic/versions/product/` with your prefix (see
   `TEMPLATE.md`'s migration prefix table).
5. Mirror the toggle in `product.config.ts` and extend `ModulesResponse` /
   `ActiveModules` (`backend/app/api/v1/config.py`,
   `packages/hooks/src/useModules.ts`) so the frontend can read it.

## Verification

Each scenario above ends in its own checks — steps 4–9 of "Disabling billing"
are exactly that: the `migrate-plan` summary, `/api/v1/config`, a real 404 on
the removed route (not an empty 200), and an absent tab in the UI. Run all
four; the route check and the UI check fail independently, which is precisely
what a half-mirrored toggle looks like.

Beyond the per-scenario checks, two automated layers verify the whole matrix.

**In-process** — `backend/tests/modules/test_module_toggles.py` runs five
configurations end-to-end (its own Postgres container per scenario, since each
drives a distinct `alembic upgrade`):

| Config | Disabled |
|---|---|
| `no_billing` | billing, notifications, file_storage |
| `no_gdpr` | gdpr |
| `no_public_api` | public_api |
| `no_enterprise_sso` | enterprise_sso |
| `minimal` | all fourteen |

What each one removes is not written per scenario. `MODULE_SURFACE` at the top
of that file declares, once per module, every route and table its toggle owns —
and every scenario asserts the route table equals *the full surface minus the
disabled modules'*. Both directions fail: a route that should have gone with
its module and didn't, and a route that vanished though nothing claimed it.
`MODULE_SURFACE` is checked for set-equality against `ModuleName` itself, so a
fifteenth module cannot be added and silently assert nothing. Every scenario
also asserts `/api/v1/auth/login`, `/api/v1/orgs` and `/api/v1/config` survive.

```sh
cd backend && pytest tests/modules/test_module_toggles.py -v
```

**Out of process** — CI's `module-matrix` job (T-291). Everything above happens
inside one Python process with `get_modules()` monkeypatched, which cannot see
a migration run from an empty database, an app booting as a subprocess off a
real `product/config.py`, or the frontend build. Three combinations, each
copying a fixture from `backend/tests/fixtures/module_configs/` over **both**
config files and then migrating, booting, curl-ing `/api/v1/config`, running
`pytest -m "not module_dependent"`, and building/linting/type-checking the
whole workspace:

| Combination | Config | Why it is there |
|---|---|---|
| **A** | `backend/product/config.py` as committed | your product's own configuration, whatever you edited it to |
| **B** | every optional module `False` | the minimum-surface product: most likely to be wanted, least likely to have been tested |
| **C** | the three Phase 4 modules on | two of them ship off, so nothing else ever exercises them |

To run a combination locally, do by hand what the job does — copy the fixture
pair over `backend/product/config.py` and `product.config.ts`, run the checks,
and `git checkout --` both files afterwards.

Backend tests that need an optional module's routes, tables or seed rows carry
a `module_dependent` marker (registered in `backend/pyproject.toml`); that is
what combination B deselects. A test written against a module's surface without
one will fail combination B rather than being skipped by it — which is the
correct outcome for a test nobody classified.

## Rollback

Re-enabling is the rollback, and it is the reason the toggle skips migrations
rather than dropping tables: nothing was destroyed on the way out.

1. Set `enabled=True` back in `backend/product/config.py` and
   `product.config.ts`.
2. `make migrate` — `migration_targets()` (`backend/app/core/migrations.py`)
   now includes `billing@head` again; Alembic applies it from scratch even
   though the DB has already moved past the core trunk's tip, because that
   branch was never recorded in `alembic_version` while billing was off.
3. `make seed` — the Free/Pro plan rows are created again (`backend/app/core/seed.py`'s
   `seed()` only skips that block while `is_enabled("billing")` is `False`);
   idempotent either way.
4. Redeploy / restart the backend so `get_modules()`'s `lru_cache` picks up
   the new `product/config.py` (it's cached per-process, not per-request).

The one thing that does not come back on its own is data written *by* the
module before it was disabled — it was never deleted, so it is still there and
correct. But anything the rest of the app would have written to those tables
while the module was off simply never happened, and re-enabling does not
backfill it.

## Escalation

- **`RuntimeError: Module 'X' requires 'Y' which is disabled` at startup.** The
  dependency edge in Pre-checks. Fix the config; do not work around it by
  weakening `validate_modules` — that error is the only thing standing between
  a partial toggle and a half-working product.
- **A route still answers after disabling its module**, or a table is still
  created: the two config files have drifted apart, or the process is serving
  a cached `get_modules()` from before the change (its `lru_cache` is
  per-process — restart, don't reload).
- **Alembic refuses the branch on re-enable.** The module's branch was never
  recorded in `alembic_version` while it was off, so it applies from scratch.
  If it instead reports the branch as already applied but the tables are
  missing, stop — that is a database that was restored or migrated by hand,
  and guessing at `alembic stamp` makes it worse. `backup-restore.md`.
- **A module needs to come off in production with live rows in its tables.**
  That is a data decision (export? retain? drop?), not a config one. The
  toggle will happily leave orphaned rows behind; decide deliberately what
  happens to them.
