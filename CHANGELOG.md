# Changelog

Template-level changelog. One entry per tag. Products forked from this
template keep their own changes in `CHANGELOG.product.md` (see
[TEMPLATE.md](TEMPLATE.md)).

## [Unreleased]
### Added
- **Global runtime switches an operator can flip without a redeploy
  (T-263).** A `product_settings` singleton row (one row, `id = 1`, enforced
  by a `CHECK` constraint) holds the switches that have to change during an
  incident rather than at the next deploy. Distinct from `product.config.py`
  (deploy-time: which modules exist at all) and from per-org feature flags
  (one customer's entitlements).
  - **Maintenance mode.** `MaintenanceModeMiddleware` answers `503` with the
    operator's own message, translated envelope and a `Retry-After`, for
    every request except the ones needed to undo it: `/health*`, `/metrics`,
    `/api/v1/admin/*`, `/api/v1/config`, the login/refresh/2FA endpoints, and
    CORS preflight. Registered *first* in `app.main`, which makes it
    innermost — locale and request id are already resolved, and the 503 is
    still wrapped by the security-header and CORS layers.
  - **A global banner**, rendered above the app shell on web
    (`GlobalBanner.tsx`) and mobile (`GlobalBannerBar.tsx`), dismissible per
    message. `useGlobalBanner()`/`useMaintenance()` (`@forge/hooks`, and the
    mobile mirror in `src/lib/config.ts`) poll `/config` every 60s, so a
    banner raised mid-incident reaches tabs and phones that are already open.
  - **A maintenance screen** on both surfaces, rendered *instead of* the app
    shell (web) and the routed screens (mobile) — not alongside them, since
    every request they make would answer 503. It removes itself within a
    minute of the window ending and offers no retry button for exactly that
    reason. The admin route tree is deliberately not gated. The sign-up page
    likewise says so up front when `signup_disabled` is set, rather than
    letting someone fill in a form that will 403.
  - **Per-module emergency kill switches** for the modules whose whole HTTP
    surface sits under a prefix of their own (`billing`, `file_storage`,
    `notifications`). A module that cannot be killed cleanly — `gdpr` and
    `feature_flags` mount under paths shared with auth and tenancy, and
    `admin` is the surface that undoes all of this — is rejected with a 422
    rather than accepted as a switch that does nothing.
  - **`signup_disabled`**, enforced as a route dependency on
    `POST /api/v1/auth/register` (not on invitation acceptance: an existing
    customer adding a colleague is not signup).
  - Well-known keys are a *documented, typed registry*
    (`app/modules/product_settings/keys.py`), not a hardcoded schema: a
    wrong-shaped value for a known key is a 422, and an unknown key is stored
    verbatim so a product can ship its own switches without touching the
    template. `GET /api/v1/admin/settings/product` serves that registry, and
    the admin form at **Admin → Settings** is generated from it rather than
    from a copy.
  - Reads go through Redis with a 60s TTL and every write busts the key, so a
    flip propagates on the next request rather than at the end of a TTL. The
    admin page itself reads straight from the database — an operator must
    never be shown a value they are about to change from a stale copy. Every
    change writes one `elevated`-severity audit event with a key-level diff.
  - `GET /api/v1/config` now carries `banner`, `maintenance` and
    `signup_disabled`; its `Cache-Control` came down from `max-age=300` to
    `max-age=60` to match, since nothing can invalidate a browser's own HTTP
    cache.
  - `docs/runbooks/maintenance-mode.md` is the incident-response procedure,
    including the two ways back in when the admin UI is what is broken, and
    `docs/runbooks/phase3-verification.md` is the Phase 3 acceptance pass.

  **Breaking for forks:** every non-admin route now issues one extra
  statement on a cold settings cache (one indexed primary-key lookup per
  minute per deployment, not per request). `tests/test_query_budgets.py`'s
  budgets were raised by one accordingly; a fork with its own budgets needs
  the same adjustment.
- **A tool-agnostic support integration layer (T-262).** The template now
  surfaces who is asking for help, without depending on any support vendor.
  - `GET /api/v1/me/support-context` returns the signed-in user's id, email,
    name, org, plan, subscription status, signup date and locale in one
    authenticated call — and `is_impersonating`, which is `true` while a
    superadmin is acting as that user. It is org-scoped through
    `get_current_org`, so a user in org A can never be handed org B's
    context.
  - `useSupportContext()` (`@forge/hooks`) is the web-side equivalent,
    returning `null` until there is a real context to hand a widget, so
    nothing boots with a half-populated or anonymous identity.
    `apps/mobile/src/lib/support.ts` is mobile's reimplementation, plus
    `openSupport()` and a `setSupportHandler()` seam for a native vendor SDK.
  - `backend/product/config.py` gains `SUPPORT_TOOL`
    (`none | intercom | crisp | plain | helpscout | custom`) and
    `SUPPORT_URL`, both served on the public `GET /api/v1/config`.
    `SUPPORT_URL` defaults to `mailto:SUPPORT_EMAIL`, resolved backend-side
    so web and mobile don't each reimplement it; a bad value fails at boot
    like a bad module or locale does.
  - Both surfaces render a "Contact support" entry. With `SUPPORT_TOOL =
    "none"` it is a plain link, prefilled with the user's account context
    when it resolves to a `mailto:` (`supportMailtoUrl` in `@forge/utils`).
    With a widget tool it dispatches a `forge:support-open` event (web) or
    calls the registered handler (mobile); if nothing handles it — an
    ad-blocked widget, an SDK that failed to init — the link still opens, so
    support is never a dead button.
  - `docs/runbooks/support-tool-integration.md` wires Intercom, Crisp, Plain
    and Help Scout end to end, and states the rule that matters most: a
    wrapper must not identify the user during impersonation.
- **Encryption key rotation is a real, resumable operation (T-245).** The
  stub `reencrypt_column_job` is replaced by `rotate_encryption_keys_job`
  (`backend/app/workers/jobs/encryption.py`), which discovers every
  `EncryptedString` column from `SQLModel.metadata` rather than being told
  which ones exist, walks each with keyset pagination on its primary key, and
  commits each batch together with a `JobRecord` (T-034) checkpoint — so a
  worker killed mid-run resumes from the last committed batch instead of
  starting over.
  - `--dry-run` counts what would move without writing anything at all (no
    row updates, no `JobRecord`); `--only table.column` restricts a run;
    `--throttle` / `--batch-size` bound the load a multi-hour scan puts on
    the pool, defaulting to the new `ENCRYPTION_ROTATION_THROTTLE_SECONDS`
    and `ENCRYPTION_ROTATION_BATCH_SIZE` settings. Driven as a one-off
    process (`python -m app.workers.jobs.encryption`) because
    `WorkerSettings.job_timeout` is 300s.
  - New metrics: `forge_reencrypt_rows_total{table,column}`,
    `forge_reencrypt_batch_seconds{table,column}`, and a
    `forge_reencrypt_remaining` gauge the job publishes through Redis so the
    backend's `/metrics` can serve it — the same split `forge_active_sessions`
    already uses for worker-side numbers.
  - `docs/runbooks/encryption-key-rotation.md` walks the whole operation:
    key swap, dry run, rotation, mid-run crash recovery, the verification
    scan that must show zero rows before `ENCRYPTION_KEY_OLD` is dropped, and
    the rollback at every step.
- **PgBouncer in transaction mode fronts Postgres in the production stack
  (T-244).** `docker-compose.prod.yml` gains a pinned
  `edoburu/pgbouncer:v1.25.2-p0` service; backend and worker connect through
  it, so the number of Postgres backends is now capped by `default_pool_size`
  (75 + 5 reserve, 80% of a default `max_connections=100`) instead of growing
  with the replica count. Configured from `.env.pgbouncer.example`; the dev
  stack stays direct-to-Postgres.
  - New `PGBOUNCER_ENABLED` setting switches `app/core/db.py` to a connection
    setup that survives having its server connection re-assigned at every
    transaction boundary — no prepared-statement reuse at any layer
    (`prepared_statement_cache_size=0`, UUID statement names,
    `statement_cache_size=0`). Left off, nothing changes for a deployment
    that talks straight to Postgres.
  - New `MIGRATIONS_DATABASE_URL` setting is what `alembic upgrade` connects
    with: migrations must bypass the pooler, because Alembic's version-table
    guard is a *session*-level advisory lock and migration scripts issue
    multi-statement DDL. Unset means "same as `DATABASE_URL`" (dev, tests,
    Render — unchanged); startup now fails fast if `PGBOUNCER_ENABLED` is on
    without a distinct migration DSN, and outside dev that DSN carries the
    same `sslmode=require` requirement as `DATABASE_URL`.
  - `docs/adr/0002-pgbouncer.md` records the decision, the
    prepared-statement tradeoff with measured overhead, and what to change if
    the re-parse cost ever matters.
  - `backend/tests/test_pgbouncer.py` runs a real pgbouncer container in
    front of a real Postgres and asserts both directions of the tradeoff (the
    unconfigured engine fails with `InvalidSQLStatementNameError`, the
    configured one doesn't), plus a new `pgbouncer-prod-compose` CI job that
    boots the actual production compose stack end to end.

### Fixed
- **Application log records went silent after any in-process `logging.config`
  call (found while building T-245).** `alembic/env.py` called `fileConfig()`
  and `importlinter.cli.lint_imports()` calls `dictConfig()`, both with the
  stdlib default `disable_existing_loggers=True` — which sets
  `disabled = True` on every logger already created in the process, including
  every `app.*` one, permanently and without a word. `env.py` now passes
  `disable_existing_loggers=False`; the import-linter tests snapshot and
  restore the flags around the third-party call. In the test suite this had
  been swallowing every application log line (and every `caplog` assertion)
  from the first DB-backed test onward; a canary test in `test_logging.py`
  now fails if a third occurrence appears.
- **`docker-compose.prod.yml` could not previously boot (T-244).** Its
  postgres service took credentials from `backend/.env`, which holds no
  `POSTGRES_*` keys, so the image refused to initialise its data directory —
  it now has its own `.env.postgres` (same reasoning as the existing
  `.env.postgres-exporter`). The backend and worker services also built with
  `context: ./backend`, which cannot satisfy `backend/Dockerfile`'s `COPY`s
  of `backend/pyproject.toml` and `packages/i18n/locales`; both now build
  from the repo root, matching how CI builds the image. Both surfaced the
  moment CI started actually booting this stack.

### Changed
- **The member list and the notification centre are virtualized (T-241).**
  Both render a constant number of DOM nodes regardless of how many rows are
  loaded, through a shared `VirtualList` (`apps/web/src/components`) built on
  `@tanstack/react-virtual`. Scroll position is now the pagination driver:
  reaching the end of the loaded rows fetches the next cursor page (the
  "Load more" buttons stay as the non-scroll path). The member list is no
  longer a `<table>` — virtualized rows are absolutely positioned, so it is
  a `role="list"` / `role="listitem"` structure with the column labels
  rendered above the scroll container; each row's controls carry their own
  accessible names (`Remove {user_id}` is new). Rows take a roving tabindex
  and arrow keys walk them, including across a virtualization boundary.
  Back-navigation restores the member list's scroll offset (TanStack Router
  `scrollRestoration` plus the virtualizer's `initialOffset`).
- **Notifications are localised per recipient at send time (T-234).**
  `NotificationService.send()` no longer accepts `title`/`body` strings —
  callers pass the notification `type` and a `params` dict, and the strings
  come from `notifications.types.<type>.title`/`.body` in the recipient's own
  `User.locale`. New `send_many()` for multi-recipient events makes rendering
  once above a fan-out loop impossible to write. The Expo push payload now
  carries `data.locale`, and the day-1/day-3 dunning steps fan out through
  `NotificationService` so an org's owners each get in-app and push in their
  own language, not just email. New `Notification.params` column (migration
  `7b41ad0c9e52`).
- **The email channel for `payment_failed` and `subscription_expiring` can no
  longer be switched off.** Those two carry the dunning sequence — "we
  couldn't charge your card, you are about to lose access" — which is a
  service message, not something the product chose to send. `channels_for()`
  forces the channel on regardless of what's stored, the update endpoint
  coerces the submitted matrix to match, and the settings page renders those
  boxes checked-and-disabled with a note explaining why. In-app and push for
  the same types stay freely toggleable. Products that want dunning fully
  opt-out-able can empty `REQUIRED_CHANNELS` in
  `backend/app/modules/notifications/preferences.py` and its
  `REQUIRED_NOTIFICATION_CHANNELS` mirror in `packages/types`.
- **Locale-aware URL routing on web (T-233).** Every web route is now under a
  `/$locale` prefix (`/en/settings/profile`, `/de/login`). Locale-less URLs —
  including every pre-existing bookmark and every previously-mailed deep link —
  redirect to their prefixed equivalent with query and hash intact; an unknown
  but locale-shaped prefix (`/xx/…`) is a 404. The URL wins over the stored
  preference for rendering (a shared `/de/…` link renders German for an `en`
  user) without overwriting `User.locale` or the local preference; only an
  explicit switch does, and the switcher rewrites the current path's locale
  segment in place. Adds per-page `<link rel="canonical">` + `hreflang`
  alternates (`noindex` and no alternates behind auth), which T-305 builds on.
- Transactional email deep links are built per recipient through the new
  `backend/app/core/urls.py` `frontend_url(path, locale)` helper instead of
  interpolating `FRONTEND_URL` by hand.
### Fixed
- Every notification was written in English regardless of the recipient's
  language: the three call sites (invite sent, file uploaded, file flagged as
  infected) built their own English `title=`/`body=` f-strings, and `send_push`
  posted those to Expo verbatim — so a locale change between an event and its
  (possibly days-deferred) push job had no effect. Both ends are fixed; the
  push job now re-renders at execution time.
- The notification email channel enqueued `template=<notification type>` with a
  `{title, body, action_url}` context no template of that name accepted:
  `file_infected` had no template at all, so the render subprocess failed the
  whole job, and `member_invited` silently sent a different email ("X has
  joined …") with every interpolation `undefined`. Types with a purpose-built
  template keep it; the rest use a new generic `notification` template.
- The in-app notification feed served the strings rendered when each row was
  written, so switching language left the whole history in the old one. It is
  now re-rendered per read from the reader's current locale.
- `file_infected` — sent by the virus scanner since T-102 — was missing from
  `NOTIFICATION_TYPES` in `packages/types`, so the notification-preferences
  page never rendered a row for it: the one type most worth muting or routing
  to push had no controls at all. Added, along with a backend test that reads
  the TypeScript source and fails on the next drift in either direction.
- Dunning and trial emails built their billing CTA link once from
  `settings.DOMAIN`, so it carried no locale segment (T-233) for any recipient.
  It is now built per owner via `frontend_url(path, owner.locale)`.
- Magic-link emails pointed at `/magic-link/consume?token=…`, a path no web
  route ever defined, so every magic-link email landed on a 404. They now point
  at the route the SPA actually serves (`/<locale>/magic-link?token=…`).
- Invitation emails were the only transactional email never localised — they
  now use the invitee's own `User.locale` when they already have an account,
  and the inviter's otherwise.
- `backend/tests/test_middleware.py` passed in CI but failed on any checkout
  whose `backend/.env` wasn't a copy of `.env.test`: five tests drove
  `/api/v1/*` routes through a bare `TestClient`, so slowapi's import-time
  limiter reached for a Redis that only exists under the compose stack, and
  the CORS test asserted a literal origin that only one env file configures.
  They now use conftest's `client` fixture and read the origin off the
  imported app.
- `@forge/i18n`'s `check-roundtrip` gate (and therefore `pnpm lint`) failed on
  every Windows checkout: it byte-compared CRLF working-tree files against the
  LF the importer writes. It now compares content, not line endings.
- `apps/mobile`'s `type-check` regenerates expo-router's typed-routes
  declaration first (new `pnpm typegen`). That file lives under the gitignored
  `.expo/`, so it was absent in CI (typed routes silently checked nothing) and
  stale locally (valid routes reported as type errors).
### Conflict risk for products
- **Medium for any product calling `NotificationService.send()`.** The
  `title=`/`body=` parameters are gone. Each custom notification type needs
  `notifications.types.<type>.title`/`.body` in `locales/en` (and its other
  locales), and its call site changed to pass `params=` instead.
- **Low–medium for products with custom notification types.** The new
  `test_notification_preferences_mirror.py` now fails if `packages/types`'
  `NOTIFICATION_TYPES` / `DEFAULT_NOTIFICATION_PREFERENCES` /
  `REQUIRED_NOTIFICATION_CHANNELS` drift from the Python side — a product
  that added a type on one side only will see it go red on first run.
- **High for web route files.** `apps/web/src/routes/*` were renamed to
  `$locale.*` and every `<Link to>`/`navigate()`/`redirect()` target gained a
  `/$locale` prefix and a `params={{ locale }}`. Products with their own routes
  or links need the same treatment; `apps/web/src/lib/useRouteLocale.ts` and
  `lib/locale-url.ts` are the helpers to use.

## [1.0.0] — Phase 1 complete
### Added
- Auth, tenancy, billing, GDPR, audit, background jobs, web + mobile shells, CI/CD
### Conflict risk for products
- Low — first release, no products exist yet
