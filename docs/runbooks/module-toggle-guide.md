# Module toggle guide (T-066)

The module toggle system (T-060–T-065) makes every optional module —
billing, GDPR, and whatever the product adds later — a single switch in
`backend/product/config.py` (+ the mirrored `product.config.ts` at the repo
root for the frontend). Flipping a module off removes its routes (404, not
hidden), its DB tables (never created), and its UI (absent, not disabled)
together. `backend/tests/modules/test_module_toggles.py` is the automated
proof of this; this runbook is the manual walkthrough.

## What's actually toggleable today

`EnabledModules` (`backend/app/core/modules.py`) declares eleven module
names, but only two ship a real router + `models.py` + migration as of
Phase 1.5: **billing** and **gdpr**. `notifications` and `file_storage` both
declare `requires=["billing"]` and exist only as empty module directories —
disabling billing disables them too (enforced by `validate_modules` at
startup: an enabled module that requires a disabled one is a `RuntimeError`,
not a silent inconsistency). `feature_flags`, `admin`, `oauth`, `totp_2fa`,
`multi_org`, `usage_metering`, and `outbound_webhooks` are declared now so
Phase 2+ modules have a config slot to land in, but nothing reads them yet —
toggling them today has no observable effect. Treat the steps below as the
worked example; the mechanism is identical for every module added later.

## Disabling billing (example: internal tool, no Stripe)

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

## Re-enabling a disabled module on an existing product

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

## Disabling GDPR

Same mechanism, no cascade: `gdpr=ModuleConfig(enabled=False)` in both
config files removes `/api/v1/me/export`, `/api/v1/me/consents`, and
`DELETE /api/v1/me`, and skips creating `consent_record`/`data_export_job`.
Nothing else depends on `gdpr`, so it can be toggled alone.

## Adding a new product-specific module

1. `make new-module NAME=invoices` — copies
   `backend/app/modules/_template/` to `backend/app/modules/invoices/`.
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

## Verifying the whole matrix at once

`backend/tests/modules/test_module_toggles.py` runs three representative
configs end-to-end (its own Postgres container per scenario, since each
drives a distinct `alembic upgrade`):

| Config | Disabled | Routes asserted absent | Tables asserted absent |
|---|---|---|---|
| `no_billing` | billing, notifications, file_storage | `/api/v1/billing/*` | `plan`, `subscription`, `webhook_event` |
| `no_gdpr` | gdpr | `/api/v1/me/export`, `/api/v1/me/consents` | `consent_record`, `data_export_job` |
| `minimal` | all of the above | both sets | both sets |

Every scenario also asserts `/api/v1/auth/login`, `/api/v1/orgs`, and
`/api/v1/config` stay reachable — the always-on surface must survive every
optional module toggle. Run it directly with:

```sh
cd backend && pytest tests/modules/test_module_toggles.py -v
```
