# Changelog

Template-level changelog. One entry per tag. Products forked from this
template keep their own changes in `CHANGELOG.product.md` (see
[TEMPLATE.md](TEMPLATE.md)).

## [Unreleased]
### Changed
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
- Magic-link emails pointed at `/magic-link/consume?token=…`, a path no web
  route ever defined, so every magic-link email landed on a 404. They now point
  at the route the SPA actually serves (`/<locale>/magic-link?token=…`).
- Invitation emails were the only transactional email never localised — they
  now use the invitee's own `User.locale` when they already have an account,
  and the inviter's otherwise.
### Conflict risk for products
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
