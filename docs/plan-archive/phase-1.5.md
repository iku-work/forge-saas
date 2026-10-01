<!-- Archived from AGENTS.md on 2026-09-30. Task specs and their completion
notes, kept verbatim as the record of what each task asked for and what
was found doing it. The laws in AGENTS.md §0 still bind. -->

# Phase 1.5 — Template infrastructure

> **Prerequisite:** T-057 (Phase 1 end-to-end verification) complete and green.
>
> **Why this phase exists:** Phase 1 built a working product. This phase restructures it so it can become a _template_ — the root from which every future product is forked. It must happen before Phase 2, because Phase 2 is the first phase that adds optional modules. Retrofitting the module toggle system onto a codebase that wasn't designed for it is significantly harder than adding it now.
>
> **Phase 1.5 acceptance criterion:** You can fork the repo, disable billing and file storage in one config file, run `make dev`, and get a fully working product — correct routes, correct schema, correct UI — with no Stripe keys and no file-storage tables anywhere.

---

## O · Template-fork conventions

> Ref: conversation §"The core mechanic" and §"Making conflicts rare"

### T-058 Upstream remote conventions + TEMPLATE.md

> needs: T-057

**Goal** Establish the Git conventions that keep template code and product code cleanly separated so that `git merge upstream/main` is low-friction forever.

**Build**

- `TEMPLATE.md` at repo root — the contract every product must read before forking:

  ```markdown
  # ForgeSaaS Template Contract

  ## Files the template owns (never edit in a product)

  Every file not listed below. Template updates will overwrite them.
  Resolve merge conflicts by taking the template version unless you have
  a documented reason not to.

  ## Files the product owns (template never touches)

  - product.config.py — module toggles + branding
  - product.config.ts — frontend module flags + branding
  - .env.product.example — product-specific env vars
  - apps/web/src/routes/\_product/ — product-specific routes
  - backend/app/modules/ — product-specific modules (0100+ migration prefix)
  - CHANGELOG.product.md — product-level changelog
  - README.md — overwrite freely

  ## Migration prefix convention

  Template: 0001 – 0099
  Products: assign yourself a prefix block in this file and document it here:
  | Product | Prefix range |
  |--------------|-------------|
  | (your name) | 0100 – 0199 |

  ## Pulling template updates

  git fetch upstream
  git merge upstream/vX.Y.Z # always merge a tag, never upstream/main directly

  # Resolve conflicts; take template version unless product.config.py says otherwise

  make dev && make test # verify before pushing
  ```

- `CHANGELOG.md` at repo root — template-level changelog, one entry per tag:

  ```markdown
  ## [Unreleased]

  ## [1.0.0] — Phase 1 complete

  ### Added

  - Auth, tenancy, billing, GDPR, background jobs, web + mobile shells, CI/CD

  ### Conflict risk for products

  - Low — first release, no products exist yet
  ```

- `Makefile` targets:
  - `update-from-template` — `git fetch upstream && git merge upstream/$(TEMPLATE_VERSION)`
  - `check-template-drift` — `git log upstream/main ^HEAD --oneline` (shows unmerged template commits)

- `.github/CODEOWNERS`:

  ```
  # Template core — requires template-maintainer approval in product PRs
  /backend/app/core/          @your-org/template-maintainers
  /backend/app/modules/auth/  @your-org/template-maintainers
  /packages/                  @your-org/template-maintainers
  /docker-compose.yml         @your-org/template-maintainers

  # Product-owned — no template-maintainer review needed
  /product.config.py          @your-org/product-owners
  /product.config.ts          @your-org/product-owners
  ```

**Tests**

- `make check-template-drift` exits 0 on a fresh clone (no unmerged upstream commits)

**Done when**

- [ ] `TEMPLATE.md` documents the full contract
- [ ] `CHANGELOG.md` has the v1.0.0 entry
- [ ] Both Makefile targets work
- [ ] `.github/CODEOWNERS` in place

---

### T-059 Product config files

> needs: T-058

**Goal** Create the two product-owned config files (Python + TypeScript) that every product customises. The template ships working defaults; products override only what they need.

**Build**

- `backend/product.config.py`:

  ```python
  """
  Product configuration — this file is owned by the product, not the template.
  The template never modifies it. Edit freely.
  """
  from app.core.modules import EnabledModules, ModuleConfig

  # ── Module toggles ──────────────────────────────────────────────────────────
  # Set enabled=False for any module you do not need.
  # The module's routes, DB tables, and UI will all be excluded automatically.
  # See TEMPLATE.md for dependency rules (e.g. notifications requires billing).
  MODULES = EnabledModules(
      billing=ModuleConfig(enabled=True),
      notifications=ModuleConfig(enabled=True),
      file_storage=ModuleConfig(enabled=True),
      feature_flags=ModuleConfig(enabled=True),
      gdpr=ModuleConfig(enabled=True),
      admin=ModuleConfig(enabled=True),
      oauth=ModuleConfig(enabled=True),
      totp_2fa=ModuleConfig(enabled=True),
      multi_org=ModuleConfig(enabled=True),
      usage_metering=ModuleConfig(enabled=False),
      outbound_webhooks=ModuleConfig(enabled=False),
  )

  # ── Branding ────────────────────────────────────────────────────────────────
  APP_NAME = "ForgeSaaS"
  PRIMARY_COLOR = "#7c6ff7"
  LOGO_URL = "/logo.svg"
  SUPPORT_EMAIL = "support@example.com"
  ```

- `product.config.ts`:

  ```typescript
  /**
   * Product configuration — owned by the product, not the template.
   * Keep in sync with backend/product.config.py.
   * The template never modifies this file.
   */
  import type { ProductConfig } from "@forge/types";

  export const config = {
    name: "ForgeSaaS",
    primaryColor: "#7c6ff7",
    logoUrl: "/logo.svg",
    supportEmail: "support@example.com",
    modules: {
      billing: true,
      notifications: true,
      fileStorage: true,
      featureFlags: true,
      gdpr: true,
      admin: true,
      oauth: true,
      totp2fa: true,
      multiOrg: true,
      usageMetering: false,
      outboundWebhooks: false,
    },
  } satisfies ProductConfig;
  ```

- Add `ProductConfig` type to `packages/types/src/index.ts`
- Add `.env.product.example` (empty file with a comment block explaining it is for product-specific vars)

**Tests**

- `product.config.py` imports cleanly: `python -c "from product.config import MODULES"`
- `product.config.ts` satisfies the `ProductConfig` type: `pnpm type-check` clean

**Done when**

- [ ] Both config files present and importable
- [ ] `ProductConfig` type exported from `@forge/types`

---

## P · Module toggle system

> Ref: conversation §"How it works at each layer"

### T-060 Backend module registry + loader

> needs: T-059, T-003

**Goal** The single source of truth for which modules are active. Validates dependency rules at startup. Every subsequent module registers here.

**Build**

- `backend/app/core/modules.py`:

  ```python
  from __future__ import annotations
  from dataclasses import dataclass, field
  from typing import Literal

  ModuleName = Literal[
      "billing", "notifications", "file_storage", "feature_flags",
      "gdpr", "admin", "oauth", "totp_2fa", "multi_org",
      "usage_metering", "outbound_webhooks",
  ]

  @dataclass(frozen=True)
  class ModuleConfig:
      enabled: bool = True
      requires: list[ModuleName] = field(default_factory=list)

  @dataclass(frozen=True)
  class EnabledModules:
      billing:           ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      notifications:     ModuleConfig = field(default_factory=lambda: ModuleConfig(True,  ["billing"]))
      file_storage:      ModuleConfig = field(default_factory=lambda: ModuleConfig(True,  ["billing"]))
      feature_flags:     ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      gdpr:              ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      admin:             ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      oauth:             ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      totp_2fa:          ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      multi_org:         ModuleConfig = field(default_factory=lambda: ModuleConfig(True))
      usage_metering:    ModuleConfig = field(default_factory=lambda: ModuleConfig(False, ["billing"]))
      outbound_webhooks: ModuleConfig = field(default_factory=lambda: ModuleConfig(False, ["billing"]))
  ```

- `backend/app/core/module_loader.py`:

  ```python
  from functools import lru_cache
  from app.core.modules import EnabledModules, ModuleName

  def validate_modules(modules: EnabledModules) -> None:
      """
      Called at startup. Raises RuntimeError with a clear message if any
      enabled module declares a `requires` dependency that is disabled.
      Fast-fail: developers learn about misconfiguration at boot, not at runtime.
      """
      for name, config in vars(modules).items():
          if not isinstance(config, ModuleConfig): continue
          if config.enabled:
              for dep in config.requires:
                  dep_cfg = getattr(modules, dep)
                  if not dep_cfg.enabled:
                      raise RuntimeError(
                          f"Module '{name}' requires '{dep}' which is disabled.\n"
                          f"Fix: either set {dep}=ModuleConfig(enabled=True) "
                          f"or set {name}=ModuleConfig(enabled=False) "
                          f"in backend/product.config.py"
                      )

  @lru_cache(maxsize=1)
  def get_modules() -> EnabledModules:
      from product.config import MODULES   # imported lazily — survives test overrides
      validate_modules(MODULES)
      return MODULES

  def is_enabled(module: ModuleName) -> bool:
      return getattr(get_modules(), module).enabled
  ```

- Call `get_modules()` in `app/main.py` lifespan startup (before any router is registered) so misconfiguration fails loudly at boot

**Tests** (100% coverage on `module_loader.py`)

- `validate_modules` with `notifications.enabled=True, billing.enabled=False` → `RuntimeError` with clear message naming both modules
- `validate_modules` with valid config → no exception
- `get_modules()` is cached: calling twice returns same object, product.config only imported once
- Override via `pytest` monkeypatch: disable billing → `is_enabled("billing")` returns False

**Done when**

- [ ] Dependency-violation test passes with exact error message
- [ ] Startup wires `get_modules()` call
- [ ] 100% coverage

---

### T-061 Conditional router registration

> needs: T-060, T-022, T-027, T-031

**Goal** Routers for optional modules are only registered when their module is enabled. Disabled modules return 404 on every path — they do not exist.

**Build**

- Rewrite `backend/app/api/v1/__init__.py` to use `build_router()`:

  ```python
  from app.core.module_loader import get_modules
  from fastapi import APIRouter

  def build_router() -> APIRouter:
      m = get_modules()
      router = APIRouter()

      # ── Always-on (never optional) ───────────────────────────────────────
      from app.modules.auth.router     import router as auth_router
      from app.modules.tenancy.router  import router as tenancy_router
      from app.modules.audit.router    import router as audit_router
      router.include_router(auth_router,    prefix="/auth",   tags=["auth"])
      router.include_router(tenancy_router, prefix="/orgs",   tags=["tenancy"])
      router.include_router(audit_router,   prefix="/audit",  tags=["audit"])

      # ── Optional ─────────────────────────────────────────────────────────
      if m.billing.enabled:
          from app.modules.billing.router import router as billing_router
          router.include_router(billing_router, prefix="/billing", tags=["billing"])

      if m.notifications.enabled:
          from app.modules.notifications.router import router as notif_router
          router.include_router(notif_router, prefix="/notifications", tags=["notifications"])

      if m.file_storage.enabled:
          from app.modules.storage.router import router as storage_router
          router.include_router(storage_router, prefix="/files", tags=["files"])

      if m.feature_flags.enabled:
          from app.modules.feature_flags.router import router as ff_router
          router.include_router(ff_router, prefix="/feature-flags", tags=["feature-flags"])

      if m.gdpr.enabled:
          from app.modules.gdpr.router import router as gdpr_router
          router.include_router(gdpr_router, prefix="/me", tags=["gdpr"])

      if m.admin.enabled:
          from app.modules.admin.router import router as admin_router
          router.include_router(admin_router, prefix="/admin", tags=["admin"])

      return router
  ```

**Tests**

- `billing=False` → `GET /api/v1/billing/plans` returns 404
- `billing=True` → `GET /api/v1/billing/plans` returns 200
- Disabled module routes are absent from `GET /openapi.json`
- All always-on routes remain reachable regardless of module config

**Done when**

- [ ] All routing toggle tests pass
- [ ] OpenAPI schema test passes

---

### T-062 Conditional Alembic migrations

> needs: T-060, T-010

**Goal** `alembic upgrade head` only creates tables for enabled modules. A product with billing disabled has no billing tables — ever.

**Build**

- Reorganise `alembic/versions/` into sub-directories by module:

  ```
  alembic/versions/
  ├── core/                    ← always runs
  │   ├── 0001_auth_tables.py
  │   ├── 0002_tenancy_tables.py
  │   └── 0003_audit_events.py
  ├── billing/                 ← only if billing.enabled
  │   ├── 0010_billing_tables.py
  │   └── 0011_dunning_tables.py   (added in Phase 2)
  ├── notifications/           ← only if notifications.enabled
  │   └── 0020_notification_tables.py
  ├── file_storage/            ← only if file_storage.enabled
  │   └── 0030_stored_files.py
  ├── gdpr/                    ← only if gdpr.enabled
  │   └── 0040_gdpr_tables.py
  └── product/                 ← product-specific migrations, 0100+
       └── .gitkeep
  ```

- Update `alembic/env.py` to conditionally import models:

  ```python
  from app.core.module_loader import get_modules

  def load_models_for_migration() -> None:
      """Import only models whose module is enabled so autogenerate
      only produces migrations for active tables."""
      # core — always
      import app.modules.auth.models     # noqa: F401
      import app.modules.tenancy.models  # noqa: F401
      import app.modules.audit.models    # noqa: F401

      m = get_modules()
      if m.billing.enabled:
          import app.modules.billing.models       # noqa: F401
      if m.notifications.enabled:
          import app.modules.notifications.models # noqa: F401
      if m.file_storage.enabled:
          import app.modules.storage.models       # noqa: F401
      if m.gdpr.enabled:
          import app.modules.gdpr.models          # noqa: F401

  load_models_for_migration()
  target_metadata = SQLModel.metadata
  ```

- Alembic's `include_object` hook filters migration objects by module:

  ```python
  # Tables that belong to each optional module
  MODULE_TABLES: dict[str, set[str]] = {
      "billing":       {"plan", "subscription", "webhookevent", "dunningcampaign"},
      "notifications": {"notification", "pushtoken"},
      "file_storage":  {"storedfile"},
      "gdpr":          {"consentrecord", "dataexportjob"},
  }

  def include_object(obj, name, type_, reflected, compare_to):
      if type_ != "table":
          return True
      m = get_modules()
      for module_name, tables in MODULE_TABLES.items():
          if name in tables:
              return getattr(m, module_name).enabled
      return True   # core tables always included
  ```

- `Makefile migrate-plan` target: prints a summary of which module migrations will run before executing

**Tests**

- `billing=False` → autogenerate produces no `plan`, `subscription`, or `webhookevent` tables
- `billing=True` → those tables are present
- Round-trip (up → down → up) with billing disabled → clean
- Enabling billing on an existing disabled-billing DB (run upgrade head) → billing tables created from scratch correctly

**Done when**

- [ ] All migration toggle tests pass
- [ ] `make migrate-plan` shows a human-readable module summary
- [ ] Migration directory structure matches the layout above

---

### T-063 Public config endpoint + frontend module awareness

> needs: T-060, T-038, T-040

**Goal** The backend exposes one unauthenticated endpoint that tells every frontend surface exactly which modules are active. Frontend routes, nav items, and UI sections are driven entirely by this response — no hardcoded flags.

**Build**

- `backend/app/api/v1/config.py`:

  ```python
  from app.core.module_loader import get_modules
  from app.core.config import get_settings

  @router.get("/config", include_in_schema=True, tags=["config"])
  async def get_config() -> ConfigResponse:
      m = get_modules()
      s = get_settings()
      return ConfigResponse(
          app_name=s.APP_NAME,
          primary_color=s.PRIMARY_COLOR,
          logo_url=s.LOGO_URL,
          support_email=s.SUPPORT_EMAIL,
          modules=ModulesResponse(
              billing=m.billing.enabled,
              notifications=m.notifications.enabled,
              file_storage=m.file_storage.enabled,
              feature_flags=m.feature_flags.enabled,
              gdpr=m.gdpr.enabled,
              admin=m.admin.enabled,
              oauth=m.oauth.enabled,
              totp_2fa=m.totp_2fa.enabled,
              multi_org=m.multi_org.enabled,
              usage_metering=m.usage_metering.enabled,
              outbound_webhooks=m.outbound_webhooks.enabled,
          ),
      )
  ```
  - Register this router as always-on (not conditional) in `build_router()`
  - Response is public — no auth — it contains no sensitive data
  - Cache-Control: `public, max-age=300` (5 min) — safe to CDN-cache

- `packages/hooks/src/useModules.ts`:

  ```typescript
  import { useQuery } from "@tanstack/react-query";
  import { apiClient } from "@forge/api-client";

  export interface ActiveModules {
    billing: boolean;
    notifications: boolean;
    fileStorage: boolean;
    featureFlags: boolean;
    gdpr: boolean;
    admin: boolean;
    oauth: boolean;
    totp2fa: boolean;
    multiOrg: boolean;
    usageMetering: boolean;
    outboundWebhooks: boolean;
  }

  export function useModules(): ActiveModules {
    const { data } = useQuery({
      queryKey: ["config"],
      queryFn: () => apiClient.config.getConfig(),
      staleTime: Infinity, // never changes at runtime — requires redeploy
      gcTime: Infinity,
    });
    // deny-by-default: if the config hasn't loaded yet, all optional modules are off
    return {
      billing: data?.modules.billing ?? false,
      notifications: data?.modules.notifications ?? false,
      fileStorage: data?.modules.file_storage ?? false,
      featureFlags: data?.modules.feature_flags ?? false,
      gdpr: data?.modules.gdpr ?? false,
      admin: data?.modules.admin ?? false,
      oauth: data?.modules.oauth ?? false,
      totp2fa: data?.modules.totp_2fa ?? false,
      multiOrg: data?.modules.multi_org ?? false,
      usageMetering: data?.modules.usage_metering ?? false,
      outboundWebhooks: data?.modules.outbound_webhooks ?? false,
    };
  }
  ```
  - No JSX imports (lint rule enforced per §0)
  - Export from `packages/hooks/src/index.ts`

- Mobile: identical query in `apps/mobile/src/lib/config.ts` — same endpoint, same staleTime

**Tests**

- `GET /api/v1/config` reflects the current module config accurately
- `billing=False` → response has `modules.billing === false`
- Response is accessible without an auth token
- Cache-Control header present
- `useModules()` returns `false` for all optional modules before the query resolves (deny-by-default)
- `useModules()` returns the correct values after mock response

**Done when**

- [ ] Endpoint tests pass
- [ ] Hook deny-by-default test passes
- [ ] `pnpm type-check` clean

---

### T-064 Conditional frontend routes + nav

> needs: T-063, T-042, T-044

**Goal** Disabled module routes do not exist in the router (404, not hidden). Nav items, settings tabs, and login options are derived from `useModules()` — zero hardcoded conditionals scattered through the codebase.

**Build**

- `apps/web/src/router.tsx` — replace hardcoded route list with module-aware builder:

  ```typescript
  import { useModules } from "@forge/hooks";

  // Called once at app startup after config is fetched
  export function buildRoutes(modules: ActiveModules) {
    return [
      // Always-on routes
      loginRoute,
      registerRoute,
      verifyEmailRoute,
      forgotPasswordRoute,
      resetPasswordRoute,
      magicLinkRoute,
      acceptInvitationRoute,
      dashboardRoute,
      settingsProfileRoute,
      settingsSecurityRoute,
      settingsOrgRoute,
      settingsMembersRoute,

      // Conditional routes — absent entirely if module is off
      ...(modules.billing ? [settingsBillingRoute, upgradeRoute] : []),
      ...(modules.notifications ? [settingsNotificationsRoute] : []),
      ...(modules.fileStorage ? [settingsFilesRoute, filesRoute] : []),
      ...(modules.gdpr ? [dataPrivacyRoute] : []),
      ...(modules.admin ? [adminRoute] : []),
      ...(modules.oauth ? [oauthCallbackRoute] : []),
    ];
  }
  ```

- `apps/web/src/layouts/AppLayout.tsx` — sidebar nav derived from modules:

  ```typescript
  export function AppLayout() {
    const modules = useModules()

    const navItems = [
      { label: "Dashboard", to: "/",               icon: LayoutDashboard, always: true },
      { label: "Settings",  to: "/settings/profile",icon: Settings,       always: true },
      { label: "Files",     to: "/files",           icon: Paperclip,      show: modules.fileStorage },
      { label: "Billing",   to: "/settings/billing",icon: CreditCard,     show: modules.billing },
      { label: "Admin",     to: "/_admin",          icon: Shield,         show: modules.admin },
    ].filter(item => item.always || item.show)
    ...
  }
  ```

- `apps/web/src/features/auth/LoginPage.tsx` — OAuth buttons only when `modules.oauth`:

  ```typescript
  const modules = useModules()
  // ...
  {modules.oauth && (
    <>
      <OAuthButton provider="google" />
      <OAuthButton provider="github" />
    </>
  )}
  ```

- Settings page tabs: same pattern — Billing tab absent if `!modules.billing`, Notifications tab absent if `!modules.notifications`

- `apps/web/src/features/settings/SecurityPage.tsx` — TOTP section only when `modules.totp2fa`

- Org switcher in sidebar only when `modules.multiOrg`

**Tests** (Vitest + RTL)

- `modules.billing=false` → Settings renders without Billing tab; navigating to `/settings/billing` is a 404
- `modules.oauth=false` → Login page renders without OAuth buttons
- `modules.multiOrg=false` → Org switcher absent from sidebar
- `modules.admin=true` → Admin link present; `modules.admin=false` → Admin link absent and `/_admin` is 404
- A11y: axe-core clean on login with oauth disabled and oauth enabled

**Done when**

- [ ] All conditional route tests pass
- [ ] All conditional nav tests pass
- [ ] Zero hardcoded `if (BILLING_ENABLED)` — all conditions go through `useModules()`

---

### T-065 Billing-disabled graceful fallback

> needs: T-064, T-031, T-036

**Goal** When billing is disabled, `require_feature()` becomes a no-op (everything allowed), `plan_id` on Organization is ignored, and the seed script skips Plan creation. No code path should crash when Stripe keys are absent.

**Build**

- `backend/app/modules/billing/dependencies.py` — update `require_feature`:

  ```python
  def require_feature(feature: str, minimum: int | None = None):
      async def checker(org = Depends(get_current_org)):
          # When billing is disabled, all features are implicitly available.
          # This makes non-billing products work without a plan in the DB.
          if not is_enabled("billing"):
              return
          # ... existing plan-check logic
      return checker
  ```

- `backend/app/core/config.py` — Stripe keys become optional when billing is disabled:

  ```python
  STRIPE_SECRET_KEY:    str | None = None
  STRIPE_WEBHOOK_SECRET: str | None = None
  ```

  Startup validation: if `billing.enabled` and either key is `None` → `RuntimeError`. If `billing.disabled` and keys are absent → silently skip.

- `backend/app/core/seed.py` — skip Plan creation when billing is disabled:

  ```python
  from app.core.module_loader import is_enabled

  async def seed() -> None:
      if is_enabled("billing"):
          await seed_plans(session)
      await seed_superadmin(session)
      await seed_demo_org(session)
  ```

- `backend/app/modules/tenancy/models.py` — `Organization.plan_id` is already nullable (`UUID | None`); document explicitly that it is intentionally null in non-billing products

**Tests**

- `billing=False`, no Stripe keys in env → app starts and passes `/health/ready`
- `billing=False` → `require_feature("anything")` returns without raising
- `billing=True`, `STRIPE_SECRET_KEY=None` → startup raises with clear message
- Seed with `billing=False` → no Plan rows created, no error

**Done when**

- [ ] App starts cleanly with `billing=False` and no Stripe env vars
- [ ] All graceful-fallback tests pass

---

### T-066 Module toggle smoke test + updated runbook

> needs: T-058, T-059, T-060, T-061, T-062, T-063, T-064, T-065

**Goal** Prove the toggle system works end-to-end with two representative configurations before Phase 2 adds more modules.

**Build**

- `backend/tests/modules/test_module_toggles.py` — parametrized test matrix:

  ```python
  MODULE_CONFIGS = [
      # (config_name, modules_override, expected_absent_routes, expected_absent_tables)
      (
          "no_billing",
          {"billing": False, "notifications": False, "file_storage": False},
          ["/api/v1/billing/plans", "/api/v1/files", "/api/v1/notifications"],
          ["plan", "subscription", "webhookevent", "storedfile", "notification"],
      ),
      (
          "no_admin",
          {"admin": False},
          ["/api/v1/admin/orgs"],
          [],   # admin has no tables of its own
      ),
      (
          "minimal",  # auth + tenancy + gdpr only
          {"billing": False, "notifications": False, "file_storage": False,
           "feature_flags": False, "admin": False, "oauth": False, "totp_2fa": False},
          ["/api/v1/billing/plans", "/api/v1/files"],
          ["plan", "subscription", "storedfile", "notification"],
      ),
  ]

  @pytest.mark.parametrize("name,overrides,absent_routes,absent_tables", MODULE_CONFIGS)
  async def test_module_config(name, overrides, absent_routes, absent_tables, ...):
      # monkeypatch get_modules() with the override
      # assert each absent_route returns 404
      # assert each absent_table is not in pg_tables
  ```

- `docs/runbooks/module-toggle-guide.md`:

  ```markdown
  # Module toggle guide

  ## Disabling billing (example: internal tool, no Stripe)

  1. In `backend/product.config.py`:
     billing=ModuleConfig(enabled=False),
     notifications=ModuleConfig(enabled=True), # OK — notifications works standalone
  2. In `product.config.ts`: set `billing: false, notifications: true`
  3. Remove STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET from .env
  4. `make migrate` — billing tables skipped
  5. `make dev` — confirm /health/ready returns 200
  6. `GET /api/v1/config` — confirm modules.billing === false
  7. Open the web app — confirm Billing tab absent from Settings

  ## Re-enabling a disabled module on an existing product

  1. Set enabled=True in product.config.py and product.config.ts
  2. `make migrate` — module's tables created from existing migrations
  3. `make seed` — module's seed data created if applicable
  4. Redeploy

  ## Adding a new product-specific module

  1. `make new-module NAME=invoices`
  2. Add INVOICES = ModuleConfig(enabled=True) to product.config.py
  3. Register router conditionally in build_router() using is_enabled("invoices")
  4. Create migrations under alembic/versions/product/ with your prefix (see TEMPLATE.md)
  ```

- Update `docs/runbooks/phase1-verification.md` with a step 15:
  ```
  15. Toggle billing off, restart, verify /health/ready still 200 and Billing tab gone.
      Toggle back on, restart, verify /health/ready still 200 and Billing tab returns.
  ```

**Tests** — the parametrized matrix above covers this task's requirements

**Done when**

- [ ] All three config scenarios in the test matrix pass
- [ ] Runbook documents the toggle workflow for disabling and re-enabling
- [ ] `make new-module` generates a module that registers itself correctly in `build_router()`

---

## Q · Internationalisation foundation

> Why now: every task from Phase 2 onwards adds user-facing strings (notification copy, email templates, billing UI, file management). Establishing i18n here means each new string is added the right way from the start. Retrofitting i18n after Phase 2 means rewriting every email template, every notification body, and every UI string.

### T-067 Shared i18n package + key conventions

> needs: T-038 (shared packages exist), T-059 (product config)

**Goal** A single source-of-truth translation package consumed by backend, web, and mobile. English locale is fully populated; one additional locale (Finnish) seeded as a worked example.

**Build**

- New workspace package `packages/i18n/`:

  ```
  packages/i18n/
  ├── package.json              # name: @forge/i18n
  ├── locales/
  │   ├── en/                   # source of truth — every key starts here
  │   │   ├── common.json       # buttons, generic errors, generic UI
  │   │   ├── auth.json         # login, register, verify, reset, magic link, 2FA
  │   │   ├── tenancy.json      # org, members, invitations
  │   │   ├── billing.json      # plans, checkout, errors
  │   │   ├── notifications.json
  │   │   ├── emails.json       # subject + body keys for each template
  │   │   ├── errors.json       # DomainError → user message mapping
  │   │   └── validation.json   # form validation messages
  │   └── fi/                   # Finnish — worked example, ~30% complete is fine
  │       └── (same files)
  ├── src/
  │   ├── index.ts              # exports: SUPPORTED_LOCALES, DEFAULT_LOCALE,
  │   │                         #          loadLocale(), type Translations
  │   ├── keys.ts               # generated typed keys (script regenerates on build)
  │   └── load-python.py        # helper for backend to load locales/<code>/*.json
  └── scripts/
      └── generate-types.ts     # walks locales/en/, generates TypeScript key types
  ```

- Key conventions documented in `packages/i18n/README.md`:
  - Namespace by surface: `auth.login.email_label`, `emails.invitation.subject`
  - Interpolation uses ICU MessageFormat: `"You have {count, plural, one {1 message} other {# messages}}"`
  - Never use a bare English string as a key — keys describe location, values are content
  - Source-of-truth English keys are required before adding any other locale

- `SUPPORTED_LOCALES` is overridable in `product.config.py` and `product.config.ts`:

  ```python
  # product.config.py
  SUPPORTED_LOCALES = ["en", "fi"]        # control which locales ship in this product
  DEFAULT_LOCALE = "en"
  ```

  Locales not in the list are not bundled by web/mobile and not loadable by the backend.

- `scripts/generate-types.ts` walks `locales/en/` and emits a `keys.ts` exporting:

  ```typescript
  export type TranslationKey =
    | "common.button.save"
    | "common.button.cancel"
    | "auth.login.email_label";
  // ... auto-generated from every leaf key in locales/en/*.json
  ```

  This makes typos in translation keys a TypeScript error.

- Migration of existing strings: Phase 1 emails (`packages/email-templates` from T-106 if reached, else the Jinja templates from T-035) are scanned; every visible English string moves to `locales/en/emails.json`. The templates reference the keys, not the strings.

**Tests**

- `pnpm --filter @forge/i18n build` produces valid JSON for every locale
- `keys.ts` is regenerated and matches `locales/en/`
- A missing key in `fi` falls back to the `en` value (not the key string)
- Adding a key to `en` then running build → CI catches if `keys.ts` is stale
- ICU pluralisation: `t("messages.count", {count: 1})` → "1 message", `count: 5` → "5 messages"

**Done when**

- [ ] `@forge/i18n` builds cleanly
- [ ] Both `en` and `fi` locales present (fi may be partial — worked example, not full coverage)
- [ ] Type generation works and is wired into the turbo pipeline
- [ ] README documents the key-naming conventions

---

### T-068 Backend i18n — locale resolution + retrofit of Phase 1 strings

> needs: T-067, T-006, T-007

**Goal** Backend resolves the user's locale per request and uses it for every user-facing string (errors, emails, validation). Phase 1's hardcoded English is migrated to translation keys.

**Build**

- `backend/app/core/i18n.py`:

  ```python
  _translations: dict[str, dict[str, Any]] = {}   # loaded once at startup

  def load_translations() -> None:
      """Called from app lifespan startup.
      Reads packages/i18n/locales/<code>/*.json for every code in SUPPORTED_LOCALES.
      Merges all files in a locale into one nested dict."""

  def t(key: str, locale: str = DEFAULT_LOCALE, **params) -> str:
      """Lookup with dot-notation, ICU interpolation, fallback to en.
      Raises KeyError only if the key is missing in both target and en
      (this is a developer error — keys must exist in en first)."""

  def get_supported_locales() -> list[str]:
      """Returns list from product.config — frontend reads this via /config endpoint."""
  ```

- `backend/app/middleware/locale.py`:

  ```python
  async def resolve_locale(request: Request, call_next):
      """Resolves locale in this order, attaches to request.state.locale:
      1. request.state.user.locale if authenticated and set
      2. Accept-Language header, matched against SUPPORTED_LOCALES (use babel.negotiate_locale)
      3. DEFAULT_LOCALE
      """
  ```

  Registered after auth middleware so user.locale takes precedence when available.

- New dependency `get_locale(request: Request) -> str` for use in routers.

- `DomainError` extended to carry a translation key, not a hardcoded English message:

  ```python
  class DomainError(Exception):
      status_code: int = 400
      code: str = "domain_error"
      message_key: str | None = None   # e.g. "errors.plan_limit_reached"

      def __init__(self, message_key: str | None = None,
                   *, fallback: str = "An error occurred",
                   details: dict | None = None): ...
  ```

  The global exception handler from T-006 now calls `t(error.message_key, locale=request.state.locale)` and falls back to `error.fallback` if the key is missing.

- Update T-006 subclasses: `PlanLimitError("errors.plan_limit_reached", fallback="Plan limit reached")` etc. All existing English literals retrofit to keys defined in `locales/en/errors.json`.

- Email templates updated:
  - If T-106 (React Email) is in scope: each template component receives a `locale` prop and reads its strings from `@forge/i18n`
  - If Phase 1 used Jinja templates: each Jinja file is split per locale, or the body becomes `{{ t("emails.invitation.body", inviter_name=name) }}` style
  - Email job (T-035) reads `recipient.locale` and selects the matching template/key set before rendering

- `GET /api/v1/config` (from T-063) extended to return `supported_locales: list[str]` and `default_locale: str` so the frontend knows which language switcher options to render.

- Backfill: every email template subject/body, every `DomainError` raise, every validation message in Pydantic schemas (use `Field(... description="users.email.invalid")` pattern or a translation hook on the global validation handler) — all converted.

**Tests**

- `resolve_locale` middleware: authenticated user with `locale="fi"` → `request.state.locale == "fi"`
- `Accept-Language: fi,en;q=0.5` → resolves to `fi` if `fi` in SUPPORTED_LOCALES, else `en`
- `Accept-Language: de` (not supported) → falls back to default
- `DomainError("errors.plan_limit_reached")` raised under `locale=fi` → response message in Finnish
- `DomainError("errors.unknown_key")` → falls back to `fallback` string, logs a warning
- Email job: user with `locale="fi"` receives invitation email with Finnish subject + body
- `GET /api/v1/config` returns the supported locales list
- 100% coverage on `i18n.py` and the locale middleware

**Done when**

- [ ] All Phase 1 user-facing strings live in `@forge/i18n` keys
- [ ] No hardcoded English in error messages, email templates, or validation messages
- [ ] Locale resolution test matrix passes
- [ ] Email job sends localised emails based on `recipient.locale`
- [ ] `mypy --strict` clean

---

### T-069 Web i18n — react-i18next + language switcher + retrofit

> needs: T-067, T-043, T-044

**Goal** The web app reads translations from `@forge/i18n`, switches language live, persists the user's choice, and Phase 1 UI strings are migrated.

**Build**

- `apps/web/src/i18n.ts`:
  - Initialise `react-i18next` with `@forge/i18n` as the resource source
  - Backend `/config` response provides the supported locale list at runtime
  - Detection order: explicit user choice in localStorage → user's profile `locale` → browser `navigator.language` → default
  - ICU plugin (`i18next-icu`) for plural/select support
  - Suspense mode disabled — locale resources are bundled, not lazy-loaded for the first locale; additional locales lazy-load on switch

- `packages/hooks/src/useLocale.ts`:

  ```typescript
  export function useLocale(): {
    locale: string;
    supportedLocales: string[];
    setLocale: (locale: string) => Promise<void>; // updates User.locale + i18n
  };
  ```

  Persists by calling `PATCH /api/v1/me { locale }` so the choice survives device changes. No JSX in this package (lint rule).

- Language switcher component in `packages/ui/`:

  ```tsx
  <LocaleSwitcher /> // dropdown of supported locales with native names
  ```

- Settings → Profile page: language preference dropdown using `<LocaleSwitcher />`.

- Retrofit pass on Phase 1 web surfaces:
  - All auth pages (T-043): every label, button, error message replaced with `t("auth.login.email_label")` etc.
  - App shell, settings tabs, navigation labels (T-044)
  - Form validation messages: react-hook-form resolvers updated to return translation keys, rendered via `t()` at the form level
  - Error toasts mapping `envelope.error.code` → `t("errors.<code>")`

- `apps/web/eslint.config.ts`: enable `react/jsx-no-literals` for JSX children (with the `// i18n-ok` escape hatch documented).

**Tests**

- Vitest + RTL: render login page → assert `screen.getByLabelText(t("auth.login.email_label", { lng: "en" }))`
- **First-visit detection:** mock `navigator.language = "fi-FI"`, render the app with no localStorage and no auth → app initialises in Finnish; `<html lang="fi">` attribute set correctly
- **Unsupported system language falls back:** mock `navigator.language = "de-DE"` with `SUPPORTED_LOCALES = ["en", "fi"]` → app initialises in English (DEFAULT_LOCALE)
- **Authenticated user override:** `navigator.language = "fi-FI"` but `User.locale = "en"` → English wins (explicit choice beats automatic detection)
- Locale switch: change to `fi` → input label re-renders in Finnish, `PATCH /api/v1/me` called with `{ locale: "fi" }`
- A11y: axe-core clean on language switcher (`lang` attribute updates on `<html>`)
- Lint guard: a deliberate `<button>Save</button>` literal in test fixture fails lint

**Done when**

- [ ] All Phase 1 web auth + settings strings live in `@forge/i18n`
- [ ] Language switcher works end-to-end against the backend
- [ ] User's choice persists across sessions and devices (stored on `User.locale`)
- [ ] `react/jsx-no-literals` lint enabled and passing
- [ ] axe-core clean

---

### T-070 Mobile i18n — expo-localization + retrofit

> needs: T-067, T-052

**Goal** Mobile auto-detects device locale on first launch, allows manual override in settings, and shares the exact same translation keys as web.

**Build**

- `apps/mobile/src/lib/i18n.ts`:
  - Same `react-i18next` setup as web, with mobile-specific detector
  - First-launch flow: `expo-localization.getLocales()[0].languageCode` → match against `SUPPORTED_LOCALES` from `/config` → fallback to `DEFAULT_LOCALE`
  - Subsequent launches: read from `expo-secure-store` (user has logged in → use their `User.locale`; not logged in → use the previously-selected locale stored in `AsyncStorage`)
  - Re-renders on system language change via `expo-localization` listener

- Shared `useLocale()` hook from `packages/hooks` works on mobile unchanged — the underlying i18n instance is platform-specific but the API is identical. This is why the hook went to the shared package in T-069.

- Settings screen: language picker that calls `useLocale().setLocale(...)`.

- Retrofit pass on Phase 1 mobile screens (T-047, T-052): every visible string moves to `@forge/i18n` keys.

- Push notification body: backend already sends localised content (per T-068's `recipient.locale` lookup), so no client-side change needed — the notification arrives pre-translated.

**Tests**

- Mock `expo-localization` returning `fi` → on first launch i18n initialises in Finnish
- User logs in with `User.locale = "en"` → app re-renders in English (user preference overrides device default)
- Mocked system language change event → app re-renders
- React Native ESLint rule (`react-native/no-raw-text` with allowlist for the `<Text>` component) catches literal text in non-Text components

**Done when**

- [ ] All Phase 1 mobile screens use `@forge/i18n` keys
- [ ] Device locale detected on first launch
- [ ] User profile locale overrides device locale after login
- [ ] Language picker in mobile settings works end-to-end

---

## Phase 1.5 complete when ALL of the following are true

- [ ] T-057 (Phase 1 verification) remains green — Phase 1.5 must not regress anything
- [ ] `billing=False` configuration passes the T-066 module-toggle matrix
- [ ] `GET /api/v1/config` reflects the active modules correctly and returns `supported_locales`
- [ ] Disabled module routes return 404 and are absent from OpenAPI schema
- [ ] Disabled module tables are absent from the DB schema
- [ ] Frontend shows no nav items, tabs, or buttons for disabled modules
- [ ] App starts with no Stripe keys when `billing=False`
- [ ] `TEMPLATE.md` and `CHANGELOG.md` checked in; `make update-from-template` works
- [ ] `docs/runbooks/module-toggle-guide.md` complete
- [ ] All Phase 1 user-facing strings (errors, emails, web UI, mobile UI) live in `@forge/i18n`
- [ ] Language switcher works end-to-end on web; persists to `User.locale`
- [ ] Mobile detects device locale on first launch
- [ ] Email job sends localised emails based on recipient's locale
- [ ] `jsx-no-literals` lint rule active and passing
- [ ] `pytest --cov` thresholds not regressed (85% overall, 100% on core modules)
- [ ] CI green on `main`
