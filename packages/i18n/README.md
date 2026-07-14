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
4. A product opts into shipping it by adding `<code>` to
   `SUPPORTED_LOCALES` in `backend/product/config.py` and to
   `locales.supported` in `product.config.ts`. Locales not listed there
   aren't bundled by web/mobile or loadable by the backend, even if this
   package ships data for them.

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

## `SUPPORTED_LOCALES` vs. product config

This package's own `SUPPORTED_LOCALES` answers "what locale data exists."
`backend/product/config.py`'s `SUPPORTED_LOCALES` / `DEFAULT_LOCALE` and
`product.config.ts`'s `locales.supported` / `locales.default` answer "what
does *this product* expose to users" — a product may ship fewer locales than
this package has data for. Keep the Python and TypeScript product config
values in sync by hand; nothing currently enforces it automatically.

## Backend consumption

`src/load-python.py` is the reference implementation T-068 ("Backend i18n —
locale resolution") adapted into `backend/app/core/i18n.py`, which wires
`t()` with real Accept-Language / `User.locale` resolution. The backend's
email job (`backend/app/workers/jobs/email.py`) calls it for the subject
line; the body is rendered by `packages/email-templates` (T-106), which
calls this package's own `t()` directly — both read the same
`locales/<code>/emails.json` catalog, so a key only needs to exist once.
