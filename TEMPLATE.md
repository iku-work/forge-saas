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
| `outbound_webhooks` | off | `billing` | Customer-facing webhook endpoints, the delivery log and its retry/replay surface at `/api/v1/webhooks`, the `webhook_endpoint` + `webhook_delivery` tables, and the delivery job (T-320–T-322) |
| `marketing` | on | — | The `apps/marketing` build, and the generated Caddy block that serves it on its own host (T-290, T-307) |
| `public_api` | off | — | Org-scoped API keys, their management surface at `/api/v1/api-keys`, the `api_key` table, and the ability of a key to authenticate at all (T-290, T-310–T-312) |
| `enterprise_sso` | off | `oauth` | Per-org OIDC/SAML sign-in, and `python3-saml`'s native `libxmlsec1` from the image (T-290) |

`public_api` is the one whose toggle does something a routing table cannot
show. Turning it off removes `/api/v1/api-keys` like any other module — but
the two endpoints a key can *call* (`GET /api/v1/orgs/{org_id}` and its
members list) belong to `tenancy` and stay registered, because a key is a
second way to authenticate against endpoints the product already serves
rather than a parallel API. What the toggle removes there is the key's ability
to authenticate: `app/core/principal.py` checks it on the auth path, so a key
minted while the module was on stops working the moment it is turned off, even
though its row is still in a table that still exists.

`outbound_webhooks` has the same shape of subtlety from the other side. Its
routes go with the toggle like any module's, and so does the *delivery*: the
job checks `is_enabled` before it sends, so a delivery queued while the module
was on is dropped rather than pushed to a third party after it is switched
off. Its rows survive, like every disabled module's, which means turning it
back on resumes delivery for everything still `pending` — worth knowing
before you flip it twice.

`marketing` is the only one that gates a *build* rather than a router:
`apps/marketing`'s `build` script exits 0 without producing a `dist`, so a
repo-root `turbo build` stays green for a product that turned it off. It also
removes the Caddy site block that build generates, so the edge comes up with no
marketing host and an app host that is completely unaffected — absence, rather
than a host that exists and 404s.

Your combination is tested, not assumed. CI's `module-matrix` job runs three
of them from scratch — migrate from an empty database, boot, `GET
/api/v1/config`, `pytest -m "not module_dependent"`, then `pnpm build && lint
&& type-check`. One of the three is **your** `product.config.py` exactly as
you edited it; the other two are template fixtures in
`backend/tests/fixtures/module_configs/`. If your product ends up on a
configuration you want permanently covered, add a fixture pair there and a
leg to that job's matrix.

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
| `public_api` | `public_api` | shipped (T-310, the `api_key` table) |
| `outbound_webhooks` | `outbound_webhooks` | shipped (T-320, the `webhook_endpoint` + `webhook_delivery` tables) |
| `enterprise_sso` | `enterprise_sso` | arrives with the `sso_connection` table (T-331) |
| `product` | yours | use your prefix range from the table above |

A new branch needs three edits together: the directory listed in
`backend/alembic.ini`'s `version_locations`, the label in
`backend/app/core/migrations.py`'s `MODULE_BRANCHES`, and the module's tables
in `backend/alembic/env.py`'s `MODULE_TABLES`. `marketing` gets none of them —
it owns no tables.

**`alembic check` on a product with a module turned off.** It will refuse:
`Target database is not up to date`. That is not drift — it is alembic
declining to compare anything while a *script* head is missing from
`alembic_version`, and a disabled module's branch is a head `make migrate`
deliberately never applies. Apply the disabled branches first, then check:

```bash
cd backend
python -m alembic upgrade public_api@head          # each branch your
python -m alembic upgrade outbound_webhooks@head   # product has off
python -m alembic check
```

The extra tables that leaves behind are not drift either — `alembic/env.py`'s
`include_object` excludes a disabled module's tables from the comparison,
which is the same rule that lets you turn a module off without dropping its
data. `backend/tests/test_conditional_migrations.py` does exactly this, for
exactly this reason.

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
