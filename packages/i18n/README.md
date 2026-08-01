# @forge/i18n

The single source-of-truth translation package consumed by the backend, web,
and mobile apps. English (`locales/en/`) is the source of truth; every other
locale is a translation of it.

## Key conventions

- **Namespace by surface, not by sentence.** Every key starts with the file
  it lives in (`auth.json` → `auth.*`, `billing.json` → `billing.*`, ...),
  followed by the page/feature, followed by the specific string:
  `auth.login.email_label`, `billing.error.payment_failed`,
  `emails.invitation.subject`. Never a bare key like `submit` or `error` —
  bare keys collide across features and give translators no context.
- **English first.** A key must exist in `locales/en/*.json` before it exists
  anywhere else. Other locales translate it; they never introduce a key `en`
  doesn't have.
- **Missing keys fall back to English, never to the raw key string.** A
  partial locale (Finnish here, ~30% complete as a worked example) is always
  safe to ship.
- **Interpolation uses ICU MessageFormat:**
  `"{count, plural, one {1 message} other {# messages}}"`. Plain
  `{placeholder}` substitution (no plural/select) also works — see
  `notifications.json`'s `messages.count` and `tenancy.json`'s
  `invitation_accept.not_logged_in`.
- **Leaf values are always strings.** No arrays, no numbers, no booleans —
  `scripts/generate-types.ts` enforces this when it walks `locales/en/`.

## Layout

```
locales/<code>/{common,auth,tenancy,billing,notifications,emails,errors,validation}.json
locales/<code>/__review.json      -- optional; dotted keys needing human review (see below)
src/index.ts        -- DEFAULT_LOCALE, loadLocale(), t(), type Translations
src/locales.ts       -- SUPPORTED_LOCALES, SupportedLocale, the LOCALES metadata registry ({code, nativeName, dir})
src/keys.ts          -- generated; do not edit (see below)
src/load-python.py   -- reference loader the backend adapts in T-068
scripts/generate-types.ts -- walks locales/en/, (re)writes src/keys.ts
scripts/check-locales.ts  -- CI gate: >5% of en keys missing, orphan keys, empty en values (see below)
scripts/i18n-transfer.ts  -- shared export/import/CSV/PO logic (see "Translator workflow" below)
scripts/i18n-export.ts    -- `pnpm i18n:export` CLI
scripts/i18n-import.ts    -- `pnpm i18n:import` CLI
scripts/check-roundtrip.ts -- CI gate: en export -> import must be lossless
```

## Adding a key

1. Add it to the relevant `locales/en/<namespace>.json` file first.
2. Run `pnpm --filter @forge/i18n run generate-types` (or `build`, which runs
   it automatically) to regenerate `src/keys.ts`. A stale `keys.ts` is caught
   by this package's own test suite (`pnpm --filter @forge/i18n test`).
3. Add the same key to any locale you're actively translating. Leaving it out
   of a partial locale is fine — `t()` falls back to English.

## Adding a locale

1. Create `locales/<code>/` with the same 9 filenames as `locales/en/`
   (empty `{}` objects are fine for namespaces you haven't translated yet —
   see "Coverage requirements" below for how partial that's allowed to be).
2. Add `<code>` to `SUPPORTED_LOCALES` in `src/locales.ts` and to `LOCALES`'s
   import list in `src/index.ts`.
3. Web lazy-loads locale resources per-code: add a `load<Code>()` function and
   a `LOCALE_LOADERS` entry in `apps/web/src/i18n.ts` (mobile needs no
   equivalent change — it loads every `SUPPORTED_LOCALES` entry eagerly via
   `loadLocale()`).
4. A product opts into shipping it by adding a `<code>: true` entry to
   `LOCALES` in `backend/product/config.py` (an `EnabledLocales` toggle,
   mirroring how `MODULES` enables/disables modules there) and to `locales`
   in `product.config.ts`. Locales not enabled there aren't bundled by
   web/mobile or loadable by the backend, even if this package ships data
   for them -- flip the toggle to `false` rather than deleting the locale's
   data if you want to keep it around without shipping it.
5. If it's a right-to-left script, add it to the `RTL_LOCALES` set in
   `src/locales.ts` (see `ar`, added for T-231) -- everything else (web's
   `<html dir>`, `packages/hooks`'s `useDirection()`, the logical-properties
   convention in `packages/ui`) reads `dir` from this registry, not a
   separate list.

Web's `/<locale>/…` URLs (T-233) and its `hreflang` alternates need no
further change — both read `SUPPORTED_LOCALES`/`LOCALES` from this registry.

## Locale in the URL (web, T-233)

Every web URL carries the active locale as its first path segment —
`/en/settings/profile`, `/fi/login`, `/de/accept-invitation/<token>`. That
makes a page shareable, bookmarkable and indexable per language, and it is
the convention T-305 (marketing SEO) builds on.

- **Route tree**: `apps/web/src/routes/$locale.tsx` is the layout every other
  route hangs off. It validates the segment against this package's registry
  (`isSupportedLocale`) — an unknown-but-locale-shaped segment (`/xx/login`)
  is a 404, not a redirect.
- **Legacy URLs keep working**: a locale-less path (`/settings/org`, every
  bookmark and every email mailed before T-233) is redirected to its
  prefixed equivalent, query and hash intact — by `$locale.tsx` for one-
  segment paths and by the root splat `routes/$.tsx` for deeper ones. There
  is no HTTP-level 301: the web app is a static SPA behind Caddy's
  `try_files … /index.html`, so there's no SSR/edge layer to issue one, and
  the signed-in user's preference (an input to the resolution) isn't known
  until the client boots. Put the same rule in front as a 301 if you deploy
  this behind an SSR or edge runtime.
- **Resolution order** for a locale-less URL, mirroring the backend's:
  `User.locale` → an explicit in-session choice (`localStorage`, T-069) →
  `navigator.languages` → `en`. See `apps/web/src/lib/locale-url.ts`.
- **The URL wins for rendering, but is not a preference**: opening a shared
  `/de/…` link shows German even to an `en` user, and does *not* write `de`
  into `localStorage` or `User.locale`. Only an explicit switch does — and
  the switcher rewrites the current path's locale segment in place rather
  than sending the user back to the dashboard
  (`apps/web/src/lib/RoutedLocaleSwitcher.tsx`).
- **Links into the app from email** are built by
  `backend/app/core/urls.py`'s `frontend_url(path, locale)`, per recipient.
  Never interpolate `settings.FRONTEND_URL` by hand.
- **`<link rel="canonical">` + `hreflang` alternates** are emitted per page
  by `apps/web/src/lib/LocaleHead.tsx`. Pages behind auth get a canonical
  URL and `noindex, nofollow`, with no alternates.

Mobile is unaffected — it has no URL bar; `useLocale()` and the platform
i18n bootstrap remain its source of truth.

## Coverage requirements (`check-locales`)

`pnpm --filter @forge/i18n run check-locales` (wired into `pnpm lint`) enforces,
for every locale under `locales/` other than `en`:

- **No orphan keys** — a key present in the locale but absent from `en` fails.
- **≥95% coverage** of `en`'s keys — unless the locale is listed in
  `PARTIAL_LOCALES` in `scripts/check-locales.ts`. `fi` is the only current
  member: it's a deliberately partial worked example of graceful fallback
  (see above), not a locale that's supposed to reach full coverage. A new
  locale should not be added to `PARTIAL_LOCALES` — it exists for that one
  documented case, not as a general escape hatch.
- `en` itself must have no empty string values.

Machine-translating a first pass (as `de`/`es`/`fr` were for T-230) is fine —
flag anything that needs a human translator's review by writing its dotted
key to a `locales/<code>/__review.json` sibling file, mapping each key to
`true`:

```json
{ "billing.error.card_declined": true }
```

`check-locales` doesn't fail on a non-empty `__review.json` (a first pass
should ship, not block CI) — the goal state is simply for that file to become
empty (or absent) as translations get human-reviewed.

## Translator workflow (`i18n:export` / `i18n:import`)

A non-developer translates via CSV, no git required:

```bash
pnpm --filter @forge/i18n run i18n:export -- --locale de          # -> i18n-export/de.csv, de.po
pnpm --filter @forge/i18n run i18n:export -- --locale de --missing-only
pnpm --filter @forge/i18n run i18n:import i18n-export/de.csv       # merges translation column back in
```

`i18n:import` fails (non-zero exit, offending key named) on any CSV key not
present in `en`, and never partially writes on failure. An unmodified
export → import round-trip is a no-op — `check-roundtrip` (wired into `pnpm
--filter @forge/i18n run lint`, same as `check-locales`) enforces this against
`en` in CI. Full details, plus the Crowdin/Lokalise TMS setups, are in
`docs/runbooks/translations.md`.

## `SUPPORTED_LOCALES` vs. product config

This package's own `SUPPORTED_LOCALES` answers "what locale data exists."
`backend/product/config.py`'s `LOCALES` (an `EnabledLocales` per-locale
toggle, T-231 -- `en=LocaleConfig(enabled=True)` etc., same shape as
`MODULES`/`ModuleConfig`) / `DEFAULT_LOCALE`, and `product.config.ts`'s
`locales` (`{ en: true, ..., default: "en" }`), answer "what does *this
product* expose to users" — a product may ship fewer locales than this
package has data for by setting a locale's `enabled` to `false`, without
deleting its translation files. Keep the Python and TypeScript product
config values in sync by hand; nothing currently enforces it automatically.
`get_supported_locales()` (`backend/app/core/i18n.py`) fails fast at startup
if `DEFAULT_LOCALE` names a disabled or unknown locale.

## Backend consumption

`src/load-python.py` is the reference implementation T-068 ("Backend i18n —
locale resolution") adapted into `backend/app/core/i18n.py`, which wires
`t()` with real Accept-Language / `User.locale` resolution. The backend's
email job (`backend/app/workers/jobs/email.py`) calls it for the subject
line; the body is rendered by `packages/email-templates` (T-106), which
calls this package's own `t()` directly — both read the same
`locales/<code>/emails.json` catalog, so a key only needs to exist once.
