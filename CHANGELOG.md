# Changelog

Template-level changelog. One entry per tag. Products forked from this
template keep their own changes in `CHANGELOG.product.md` (see
[TEMPLATE.md](TEMPLATE.md)).

## [Unreleased]
### Added
- **Per-route bundle budgets gate CI (T-242).** A new `bundle-budget` job
  builds `apps/web` and runs [`size-limit`](https://github.com/ai/size-limit)
  against explicit gzipped budgets committed in `apps/web/package.json` — the
  entry chunk (held near ~150 KB), the shared vendor chunk, every lazy route
  chunk, each i18n namespace, and total CSS. Raising a budget is a reviewed
  edit to that file in the same PR. A meta-check fails the build if any built
  chunk has no budget (new routes auto-split into new chunks), and every run
  uploads a `rollup-plugin-visualizer` treemap (`dist/stats.html`, via
  `ANALYZE=true pnpm build`) as a CI artifact so regressions are diagnosable
  without reproducing locally. The Vite entry chunk is renamed `entry-*` so
  its budget targets it unambiguously. Runbook:
  [`docs/runbooks/bundle-budget.md`](docs/runbooks/bundle-budget.md).

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
