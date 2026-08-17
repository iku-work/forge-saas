# ForgeSaaS Template Contract

This file is the contract every product forked from this template must read
before customising anything. It defines which files the template owns,
which files the product owns, and how to pull template updates without
fighting your own customisations.

## Files the template owns (never edit in a product)

Every file not listed under "Files the product owns" below. Template
updates will overwrite them. Resolve merge conflicts by taking the template
version unless you have a documented reason not to (note the reason in
`CHANGELOG.product.md`).

## Files the product owns (template never touches)

- `product.config.py` — module toggles + branding + support tool (backend)
- `product.config.ts` — module toggles + branding (frontend)
- `apps/web/src/features/support/SupportWidget.tsx` — the support vendor's widget wrapper, if you wire one (T-262; see `docs/runbooks/support-tool-integration.md`)
- `.env.product.example` — product-specific env vars
- `apps/web/src/routes/_product/` — product-specific routes
- `backend/app/modules/<product-module>/` — product-specific modules (assign yourself a migration prefix block below)
- `CHANGELOG.product.md` — product-level changelog
- `README.md` — overwrite freely

## Module toggles

Every optional module is one boolean in `product.config.py`, mirrored in
`product.config.ts`. Off means gone, not hidden: the routes 404, the tables are
never created, and the UI is absent. `docs/runbooks/module-toggle-guide.md` is
the walkthrough; this table is the contract.

Dependencies are declared by the template (`backend/app/core/modules.py`), not
by your config — enabling a module whose dependency is off is a `RuntimeError`
at boot, whether or not you spelled the `requires` out yourself.

| Module | Default | Requires | Turning it off removes |
|---|---|---|---|
| `billing` | on | — | Stripe checkout/portal/webhooks, `plan`/`subscription`, the Billing tab |
| `notifications` | on | `billing` | In-app + email + push notifications, the notification centre |
| `file_storage` | on | `billing` | Uploads, the S3/MinIO surface, virus scanning |
| `feature_flags` | on | — | Per-org flag evaluation and its admin toggles |
| `gdpr` | on | — | Data export, consent records, self-serve account deletion |
| `admin` | on | — | The whole `/api/v1/admin` surface and the admin UI |
| `oauth` | on | — | Google/GitHub sign-in and account linking |
| `totp_2fa` | on | — | TOTP enrolment and step-up prompts |
| `multi_org` | on | — | The org switcher; users stay pinned to one organisation |
| `usage_metering` | off | `billing` | Usage records and metered/reported quantities |
| `outbound_webhooks` | off | `billing` | Customer-facing webhook endpoints and deliveries |
| `marketing` | on | — | The `apps/marketing` build and the Caddy block serving it (T-290) |
| `public_api` | off | — | API keys as a second kind of principal, `/api/v1/api-keys`, `api_key` (T-290) |
| `enterprise_sso` | off | `oauth` | Per-org OIDC/SAML sign-in, and `python3-saml`'s native `libxmlsec1` from the image (T-290) |

`marketing` is the only one that gates a *build* rather than a router:
`apps/marketing`'s `build` script exits 0 without producing a `dist`, so a
repo-root `turbo build` stays green for a product that turned it off.

## Migration prefix convention

Alembic migrations are numbered so template and product migrations never
collide:

| Range       | Owner                                  |
|-------------|-----------------------------------------|
| 0001 – 0099 | Template (core + optional modules)      |
| 0100 – 0199 | First product to fork — assign yourself a range below |

Each optional module's migrations live on their own Alembic branch, in
`backend/alembic/versions/<branch>/`, so a module nobody enabled is never
recorded in `alembic_version` and applies from scratch if it is switched on
later. Branch per module:

| Branch | Module | Status |
|---|---|---|
| `core` | always applied | auth, tenancy, audit, job records, product settings |
| `billing` / `gdpr` / `notifications` / `file_storage` | the matching module | shipped |
| `public_api` | `public_api` | arrives with the `api_key` table (T-310) |
| `enterprise_sso` | `enterprise_sso` | arrives with the `sso_connection` table (T-331) |
| `product` | yours | use your prefix range from the table above |

A new branch needs three edits together: the directory listed in
`backend/alembic.ini`'s `version_locations`, the label in
`backend/app/core/migrations.py`'s `MODULE_BRANCHES`, and the module's tables
in `backend/alembic/env.py`'s `MODULE_TABLES`. `marketing` gets none of them —
it owns no tables.

Products: assign yourself a prefix block and document it here when you fork:

| Product      | Prefix range |
|--------------|--------------|
| (your name)  | 0100 – 0199  |

## Pulling template updates

```sh
git fetch upstream
git merge upstream/vX.Y.Z     # always merge a tag, never upstream/main directly
# Resolve conflicts; take the template version unless product.config.py says otherwise
make dev && make test          # verify before pushing
```

`make update-from-template` and `make check-template-drift` (see `Makefile`)
automate the fetch/merge and the "are we behind" check respectively.

## Setting up the upstream remote

When you fork this repo to start a product, add the template as a second
remote so you can keep pulling updates:

```sh
git remote add upstream <forgesaas-template-repo-url>
git fetch upstream
```
