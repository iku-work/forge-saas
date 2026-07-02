# @forge/email-templates

Type-safe React Email components for every transactional email ForgeSaaS
sends. Replaces the Jinja2 templates `backend/app/workers/jobs/email.py` used
before T-106 — the worker now renders by shelling out to this package's
compiled CLI instead of templating HTML in Python.

## Layout

```
src/
  components/BaseLayout.tsx -- shared header/footer/CTA-button chrome
  templates/<Name>.tsx      -- one file per email, named export + default
                                export wrapping it with example data (used
                                by the preview server below)
  templates/index.ts        -- TEMPLATES registry, keyed by the snake_case
                                name the Python worker passes as `template`
  render.ts                 -- renderEmail(name, input) -> {html, text}
  cli.ts                    -- argv/stdout wrapper around renderEmail, built
                                into a single dependency-free dist/cli.js
  index.ts                  -- the package's normal JS/TS entry point
```

## Adding a template

1. Add the copy to `packages/i18n/locales/en/emails.json` (namespaced
   `emails.<name>.*`, plus `emails.subject.<name>`), then the same keys to
   any other locale you're translating — see that package's own README.
   Run `pnpm --filter @forge/i18n build` afterward; this package imports
   `@forge/i18n`'s **compiled** output, not its JSON source directly, so a
   rebuild is required before new/changed copy shows up here.
2. Create `src/templates/<Name>.tsx`: a `<Name>Props` interface (field names
   match the Python caller's `context` dict, snake_case, verbatim — there's
   no camelCase translation layer), the component itself, an
   `<name>ExampleProps` constant, and a default-exported `<Name>Preview`
   wrapper that renders the component with the example props (this is what
   `pnpm dev`, below, actually displays — `email dev` renders each file's
   default export with no arguments).
3. Register it in `src/templates/index.ts`'s `TEMPLATES` map.
4. Add a case to `backend/app/workers/jobs/email.py`'s caller if this is a
   new send path, or point an existing `template="..."` string at the new
   name. The Python caller is also where `locale` and `branding` come from —
   templates never source those themselves.

## Render contract

`renderEmail(name, { branding, locale?, props? })` returns
`{ html, text }`. `branding` is always required and is meant to be passed
straight through from `app.core.module_loader.get_branding()` — see
`Branding` in `components/BaseLayout.tsx`. `locale` defaults to
`@forge/i18n`'s `DEFAULT_LOCALE` when omitted.

`dist/cli.js` (built by `pnpm build`) wraps this for the Python worker, which
has no node_modules and no pnpm at runtime — only a bare `node` binary, so
the CLI bundle inlines every dependency (see `tsup.config.ts`'s
`noExternal: [/.*/]`):

```
node dist/cli.js <template_name> '<json: RenderEmailInput>'
# stdout (exit 0): {"html": "...", "text": "..."}
# stderr (exit 1): a single error message line
```

## Preview server

```
pnpm --filter @forge/email-templates dev
```

Serves live-reloading previews of every template (using each file's example
props) at `http://localhost:3030`.

## A note on dependencies

Don't add `@react-email/components` or any individual `@react-email/*`
primitive package (`@react-email/text`, `@react-email/button`, ...) back to
this package. React Email 6.0 (April 2026) folded all of them into the
single `react-email` package — every version of the old scoped packages now
shows `deprecated: "Package no longer supported"` on npm. Import components
(`Html`, `Body`, `Text`, `Button`, ...) and `render()` from `"react-email"`
directly, as `components/BaseLayout.tsx` does.
