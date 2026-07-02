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
src/index.ts       -- SUPPORTED_LOCALES, DEFAULT_LOCALE, loadLocale(), t(), type Translations
src/keys.ts         -- generated; do not edit (see below)
src/load-python.py  -- reference loader the backend adapts in T-068
scripts/generate-types.ts -- walks locales/en/, (re)writes src/keys.ts
```

## Adding a key

1. Add it to the relevant `locales/en/<namespace>.json` file first.
2. Run `pnpm --filter @forge/i18n run generate-types` (or `build`, which runs
   it automatically) to regenerate `src/keys.ts`. A stale `keys.ts` is caught
   by this package's own test suite (`pnpm --filter @forge/i18n test`).
3. Add the same key to any locale you're actively translating. Leaving it out
   of a partial locale is fine — `t()` falls back to English.

## Adding a locale

1. Create `locales/<code>/` with the same 8 filenames as `locales/en/`
   (empty `{}` objects are fine for namespaces you haven't translated yet).
2. Add `<code>` to `SUPPORTED_LOCALES` in `src/index.ts` and to the `LOCALES`
   map's import list.
3. A product opts into shipping it by adding `<code>` to
   `SUPPORTED_LOCALES` in `backend/product/config.py` and to
   `locales.supported` in `product.config.ts`. Locales not listed there
   aren't bundled by web/mobile or loadable by the backend, even if this
   package ships data for them.

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
