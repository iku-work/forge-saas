# Changelog

Template-level changelog. One entry per tag. Products forked from this
template keep their own changes in `CHANGELOG.product.md` (see
[TEMPLATE.md](TEMPLATE.md)).

## [Unreleased]
### Added
- **Outbound webhooks: endpoints, signed delivery, retry, replay (T-320, T-321,
  T-322).** The `outbound_webhooks` module (default off, requires `billing`)
  turns "something happened here" into a signed POST to a customer's URL, with
  a delivery log a developer can debug from without opening a support ticket.
  - **The signature covers the bytes that are sent, not a re-serialisation of
    them.** `X-Forge-Signature: t=<unix>,v1=<hmac-sha256 of "{t}.{raw body}">`,
    Stripe's scheme because an integrator has already written that verifier
    once. The payload is serialised exactly once in the job and those bytes are
    both signed and posted — signing `json.dumps(payload)` and sending a
    differently-ordered serialisation is the failure that costs people days and
    leaves nothing in either log. `verify_signature` ships as a working
    reference implementation, timestamp tolerance included, rather than as a
    paragraph telling consumers to write one.
  - **The retry ladder is explicit and documented**: 1 m, 5 m, 30 m, 2 h, 12 h,
    then dead-lettered. Scheduled with ARQ's `_defer_by` computed from the row's
    own `next_attempt_at`, so the queue and the log can never disagree about
    when the next attempt is — the first thing anyone checks when a consumer
    says "you never retried". Driven in tests by moving timestamps, never by
    sleeping.
  - **SSRF is re-checked at delivery, not only at save.** A hostname that
    resolved publicly yesterday can resolve to `169.254.169.254` today, and DNS
    belongs to whoever registered the URL. T-212's guard runs again immediately
    before the request; a private destination is dead-lettered at once rather
    than retried five more times at an internal address.
  - **The circuit breaker is a row, not a process.** Five consecutive
    *dead-lettered* deliveries (not five failed attempts of one delivery — that
    is a consumer restart) disable the endpoint and notify the org's owners,
    guarded because `notifications` may be off. Re-enabling clears the counter,
    otherwise the first failure after a fix trips it again and reads as "your
    fix did not work".
  - **A replay never touches the original row.** It sends the *stored* payload
    as a new delivery with a new `X-Forge-Delivery`; retries share that id so a
    consumer can dedupe, a replay deliberately does not. "We replayed it and now
    it says delivered" is a sentence this API cannot produce.
  - **Retention ships with the table, not after it.** `webhook_delivery` is the
    fastest-growing table this template adds; `WEBHOOK_DELIVERY_RETENTION_DAYS`
    (30) and a batched prune job are what stop it from being a disk-full
    incident with a date on it. Queue depth is published worker-side through
    Redis, like `forge_active_sessions`, because `/metrics` is served by a
    different process.
- **API key management UI (T-313)** and **the published public API contract
  (T-314).** The key is shown once, held in component state and never in the
  react-query cache — the cache outlives the component and the server keeps no
  copy, so it would be holding the only one. `/api/v1/openapi-public.json`
  emits a *second* OpenAPI document containing exactly the routes that declare
  a `require_scope`, and `/guides/api` on the marketing site renders it, so the
  published surface is derived rather than maintained and cannot drift from
  what the code serves.

- **Analytics and cookie consent, defaulting to neither (T-308).** A marketing
  site is where tracking lands, so this ships the *integration point* rather
  than a vendor: `apps/marketing/src/config/analytics.ts`, one object, a `none`
  default, and two worked examples. The default build is held to that by
  `tests/consent.test.ts` reading real output — zero cookies, zero third-party
  requests, no banner on any page.
  - **The privacy-preserving option is the easy one, on purpose.** A cookieless
    provider needs two lines and no banner; a cookie-setting one needs the same
    two lines and gets the whole consent apparatus already built. A banner
    avoided is worth more than a banner implemented well — it is the layout
    shift, the consent record, the withdrawal flow and the legal review that do
    not happen.
  - **Nothing loads before consent.** Not "loads and waits" — the provider's
    `<script>` element is created *after* the decision, so a refusal means the
    request is never made. Verified by running the shipped inline script in a
    DOM rather than by reading it.
  - **The banner is pre-rendered, and fixed-position.** A banner injected after
    paint is a guaranteed CLS regression and would fail T-306's budget; this one
    is in the HTML and it is the *hiding* that is conditional. Fixed positioning
    takes it out of flow, so neither showing nor hiding it moves a pixel.
  - **Withdrawal deletes the cookies**, from a permanent footer control that
    re-opens the same two buttons. A banner that flips a flag and leaves the
    cookies in place has not withdrawn anything, which is the half most
    implementations skip.
  - **Consent is a first-party cookie for an anonymous visitor and a
    `consent_record` row for a signed-in one**, reconciled by `apps/web` on the
    first render after a session appears — watching for the user rather than
    hooking the login mutation, because a session arrives through five code
    paths and only one of them is a login call. `PUBLIC_CONSENT_COOKIE_DOMAIN`
    is the one knob that lets the cookie cross from the marketing host to the
    app's; unset, the visitor is simply asked again inside the product.
  - **One declaration feeds three gates.** The provider's `hosts` are the
    subresource allowlist `check-budget.mjs` enforces *and* the `script-src`,
    `connect-src` and `img-src` entries `caddy-site.mjs` writes into the CSP.
    A third-party host in `dist/` that is not in that file fails the build; a
    host that is in it cannot be blocked at the edge by accident.
  - The banner is gated on the **`gdpr`** module, not on `marketing`: consent is
    recorded in that module's table, and a banner collecting an answer it cannot
    store is worse than no banner because it looks like compliance.
    `docs/runbooks/analytics-and-consent.md` is the decision as a product owner
    has to make it.

- **Org-scoped API keys (T-310), a second kind of principal (T-311), and a
  per-key rate limit (T-312).** The `public_api` module, off by default,
  becomes real: `api_key` on its own Alembic branch, a session-only management
  surface at `/api/v1/api-keys`, and `Authorization: Bearer fk_live_…` as a way
  for a third party to call the API without a user.
  - **SHA-256, not argon2, and the comment says why.** The secret is 32 bytes
    from `secrets.token_urlsafe`: there is no dictionary for a work factor to
    slow down, and argon2 would put ~100 ms of CPU on the hot path of *every*
    API request — the exact failure T-223's load test caught on login.
    Comparison is `hmac.compare_digest`. The full key is returned once, from
    the create endpoint, and is never stored, logged or put in an error.
  - **A found bug, kept as a test.** The prefix was `token_urlsafe` output,
    whose alphabet includes the `_` that separates the prefix from the secret —
    so roughly one key in six split in the wrong place and could never
    authenticate. It is hex now, and 200 keys are minted per test run to prove
    it.
  - **Opt-in per endpoint, always.** A route accepts a key only by declaring
    `require_scope(...)`; there is deliberately no blanket swap of
    `get_current_user`. The whole public surface today is two reads
    (`GET /orgs/{org_id}` and its members list), it is enumerated from the code
    by walking the dependency tree, and that enumeration is pinned by a test so
    widening it is a review conversation rather than a refactor.
    `require_scope` is dual-principal: a session passes it by holding the
    Permission the scope maps onto, so opening a route to keys never widened
    what a *user* may do.
  - **The toggle is checked on the auth path, not just at the router.** A key
    minted while `public_api` was on stops authenticating the moment it is
    turned off — the rows survive the toggle by design (`MODULE_TABLES`), so
    the routing table alone could not enforce this.
  - **Revocation takes effect on the next request**, not at the end of the
    60-second lookup cache: create, update, revoke and delete all bust that
    entry, and the invalidation matrix in `app/core/cache_keys.py` says so.
    `last_used_at` deliberately does not, and is throttled through Redis to one
    write per key per minute — a write per request on a hot key is an N+1
    wearing a different hat.
  - **Per-key rate limiting**, keyed on `api_key:{id}` rather than on an IP
    (proxies collapse to one) or a user (a key has none), reusing T-223's
    limiter and storage. `X-RateLimit-Limit/Remaining/Reset` on every response
    including the successful ones, `Retry-After` on the 429, and a nullable
    per-key override so one integration can be raised without raising everyone.
    The budget is read through `get_settings()` at request time, with the
    regression test for the import-time-binding trap that bit T-223 and
    `test_middleware.py`.
  - **Lifecycle beyond the happy path**: deleting an organization cascades to
    its keys at the database level (nothing else can, since no endpoint deletes
    an org); a GDPR export lists the keys a user minted, through `ApiKeyPublic`
    so no digest reaches the file; and erasure *nulls* `created_by` rather than
    deleting an organization's still-active credential.
    `docs/runbooks/api-keys.md` says out loud that deactivating a member does
    not revoke their keys, because the natural assumption is the opposite.
  - `list_members`'s query budget went **down**, from 5 to 4: resolving the
    principal through `require_scope` reads the membership but not the
    Organization row the endpoint never used.
  - **The first default-off migration branch turned up an alembic papercut**
    every later one will hit too (T-331 next). `alembic check` refuses to
    compare anything at all while a *script* head is missing from
    `alembic_version` — `Target database is not up to date` — and a disabled
    module's branch is a head `make migrate` deliberately never applies. So
    the drift test was not reporting "no drift", it was reporting that it had
    declined to look. It now applies every branch before checking, which is
    also the workaround a product with a module turned off needs; TEMPLATE.md
    spells it out. The extra tables that leaves behind are excluded by
    `include_object`, which is the same rule that lets a module be switched
    off without dropping its data.

- **`apps/marketing` — a pre-rendered marketing site, in Astro (T-300).**
  [ADR-0004](docs/adr/0004-marketing-framework.md) is now `Accepted` and names
  the framework. The deciding constraint was `@forge/ui`: it is React 18 +
  Radix and ships no `"use client"` directives, so every component in it is
  client-only by construction. Astro's islands model asks for the opposite
  annotation — pages are static HTML and a React component is hydrated only
  where one is written — so the package is consumable exactly as it ships,
  with no directives added to it and no wrapper layer. Next.js's
  RSC-by-default would have required one or the other. Astro is also
  Vite-based, so the site reuses this repo's toolchain (`@tailwindcss/vite`
  v4, the same tsconfig idioms, the same turbo graph) rather than adding a
  second build system.
  - **Zero JavaScript on `/`, asserted rather than intended.**
    `apps/marketing/tests/build.test.ts` runs real builds and reads `dist/`:
    the landing copy is in the HTML rather than behind an empty shell, `/`
    contains no `<script>` at all, and a `@forge/ui` component still hydrates
    on the one page that asks for it. JSON-LD is excluded from that count in
    advance, so T-303 can add it without weakening the check.
  - **The design tokens moved to `@forge/ui`.** They lived in
    `apps/web/src/index.css`, which made the SPA their owner while Storybook
    already reached across the workspace to import them and marketing would
    have been the second consumer to do so. They are now
    `packages/ui/styles/tokens.css`, imported by every surface as
    `@forge/ui/styles/tokens.css`. No token value changed. `primaryColor` in
    `product.config.ts` stays the one place the brand colour is written down —
    the favicon is generated from it rather than committed.
  - **The `marketing` toggle gates a build, not a router.**
    `apps/marketing/scripts/guard-enabled.mjs` reads it from
    `product.config.ts`, and when it is off skips `astro build`, removes any
    stale `dist/`, and exits 0 — the behaviour CI's `module-matrix` job has
    been asserting against a placeholder since T-291. `product.config.ts`
    became a `globalDependencies` entry in `turbo.json` at the same time: it
    sits at the repo root, outside every package's inputs, so turbo would
    otherwise replay a cached build from before a toggle flipped.
  - **A `marketing` namespace in `@forge/i18n`**, in all six locales, so
    marketing copy comes from the same locale JSON as the product rather than
    a marketing-only string file. Pages translate at build time and pass
    finished strings to islands as props — no i18next provider, because every
    island is its own React root.

- **`<Seo>` — one meta block per page, and a build that refuses to ship one
  without it (T-302).** `src/components/Seo.astro` emits the title, the meta
  description, the canonical, the full Open Graph set and the Twitter card from
  typed props. No page imports it: `BaseLayout` renders it from the props it
  already took, so a new page cannot forget it, cannot order the tags
  differently, and cannot emit a second `<title>`. A page that needs more than
  the defaults — a blog post is an `article` with a publication date, an entry
  can carry its own card — passes those through the layout.
  - **`scripts/check-seo.mjs` turns the convention into a guarantee.** It runs
    as an Astro integration on `astro:build:done`, walks `dist/**/*.html`, and
    fails the build on any page missing a title, a description or a canonical,
    carrying two of one, or carrying an empty one — the cases the component
    itself cannot see. It is wired into the build rather than into a
    `package.json` script because every build in this repo goes through
    `scripts/guard-enabled.mjs`, the tests' builds included, and a check bolted
    onto the package script would be absent from all of them.
  - **Every URL is absolute, from one origin.** Canonical, `og:url` and
    `og:image` all resolve against `Astro.site`, which is `PUBLIC_SITE_URL`
    read once in `astro.config.mjs` — a crawler has no base URL to resolve a
    root-relative `og:image` against, and there is no second copy of that
    origin in TypeScript to drift from it.
  - **One committed Open Graph card, drawn from `primaryColor`.**
    `scripts/make-og-image.mjs` writes `public/og-default.png` with a PNG
    encoder in ~40 lines of `zlib` rather than a dependency; a test decodes the
    committed file and fails when its ground is no longer the brand colour.
    Per-page image generation (satori, a headless browser) is an explicit
    non-goal, and the card carries no words because a template cannot know the
    product it will become — `og:image:alt` carries the name instead. Entries
    override it with `ogImage` in their frontmatter.
  - **`og:alternate_locale` lists translations that exist**, from
    `entryLocales()`, rather than every enabled locale: a post written only in
    English announcing five translations is the metadata form of a dead link.
    T-305's `hreflang` links read the same list.
  - `src/config/site.ts` is now the one module inside `src/` that names the
    brand — product name, brand colour, support address, all derived from
    `product.config.ts`. The two legal documents gained a real `description`
    in their schema; they had been reusing their own title, which is the
    degenerate form of having none.

- **The marketing site is served, on its own host (T-307).** Until now
  `apps/marketing` existed only in `dist/`. Caddy now serves it at
  `marketing.localhost` locally and at `{$MARKETING_DOMAIN}` — the apex, with
  the app on `app.` — in production, from a read-only bind mount of that
  directory, alongside the SPA's existing `/srv/web`.
  - **Separate hosts, not a path split.** Splitting on a path would have meant
    moving every `apps/web` route under `/app/*` — TanStack Router's basepath,
    every Playwright spec, every invite link and every transactional email URL
    — or maintaining an allowlist of marketing path prefixes at the edge.
    Keeping `localhost` as the app is what lets every existing E2E spec, ZAP
    config and k6 target carry on unchanged; the app's site block is not edited
    at all.
  - **The site block is generated by the build, not committed.**
    `apps/marketing/scripts/caddy-site.mjs` writes
    `apps/marketing/caddy/{dev,prod}/marketing.caddy` on every `astro build`,
    and `scripts/guard-enabled.mjs` deletes both when `modules.marketing` is
    false, in the same breath as `dist/`. The repo-root `Caddyfile` and
    `Caddyfile.prod` pull them in with `import /etc/caddy/conf.d/*.caddy` — a
    *glob*, which Caddy answers with a log warning rather than a fatal error
    when nothing matches. So "the module is off" and "this stack never built
    the frontend" (the ZAP baseline job, a fresh clone) are the same, safe
    configuration: an edge that serves the app and has no marketing host at
    all. Absence was the requirement; a block that exists and 404s was not.
    It has to be generated because its `Content-Security-Policy` names each
    inline script by SHA-256 digest and its locale rules follow
    `product.config.ts` — only a copy produced from the `dist/` being served
    can be right. `caddy/*/*.caddy` is therefore a turbo output too, or a cache
    *hit* would restore `dist/` and leave the block describing the build before
    it.
  - **Marketing's own security headers, not the app's.** The app's
    `default-src 'self'` in `backend/app/middleware/security_headers.py`
    describes JSON API responses; this is a document policy: `script-src` is
    `'self'` plus one hash per inline script and never `'unsafe-inline'`, plus
    HSTS, `nosniff`, `DENY`, a referrer policy, a permissions policy and
    `-Server`. Verified in Chromium against the running stack — six pages, zero
    violations, JSON-LD intact in the DOM and the pricing island hydrated —
    which closes T-303's open box, and mutation-checked from the other side:
    remove the hashes and the browser blocks those scripts by name.
    `style-src` does allow inline style and cannot avoid it — shiki emits a
    `style="color:#…"` attribute per token in every fenced code block, which no
    CSP hash can cover.
  - **`X-Robots-Tag: noindex` on every host but production**, which closes
    T-304's other open box. In production it is a matcher on the host in the
    request rather than on the build, so a production build deployed to staging
    is still covered.
  - **Cache-Control follows the filename**: a year and `immutable` for
    content-addressed `/_astro/*`, five minutes for everything mutable at a
    stable URL.
  - **A locale-less URL is redirected at the edge** instead of arriving at
    `404.html` and rewriting itself — a 302 with `Vary: Accept-Language`, not
    the 301 T-305's note asked for, because the target depends on a request
    header and a 301 is cached per-URL by the browser.
  - **The session cookie stays on the app host.** T-220 lost a session to a
    cross-origin redirect once; marketing is static and needs none, so the note
    is in both Caddyfiles and `backend/tests/test_caddy_config.py` now fails on
    any `set_cookie(domain=...)` anywhere in the backend. The refresh cookie is
    `__Host-` prefixed, which means a browser would reject it outright if one
    were added.
  - **`MARKETING_DOMAIN` must never be blank**, and `backend/.env.example`
    ships `marketing.localhost` rather than an empty value because of it. Caddy
    substitutes a set-but-empty environment variable as the empty string rather
    than falling back to the `{$VAR:default}` — which leaves a site block with
    no address, and Caddy then reads its opening brace as a global-options
    block and refuses to start the whole edge, app host included.
  - **Two CI jobs gained the assertion.** `e2e-web` checks both hosts through
    the real edge before Playwright runs — TLS, the CSP, the two cache rules,
    the noindex header, no `Set-Cookie`, and the locale negotiation — and
    `module-matrix` now boots Caddy for each combination and proves the app
    host serves the SPA whether or not marketing is enabled.

- **The marketing performance budget is enforced by CI (T-306).** A new
  `lighthouse` job runs `@lhci/cli` against the built static output served by
  `http-server` — no Docker, no backend, no database, because the site is a
  directory of files. Four real pages (`/en/`, `/en/pricing/`, a blog post, a
  guide), three runs each, asserted at the median: performance >= 0.9,
  accessibility >= 1.0 (the bar T-221 already holds `apps/web` to),
  best-practices >= 0.9, SEO >= 0.95, LCP < 2.5 s, CLS < 0.1. The job is
  skipped outright when `modules.marketing` is off. All four pages currently
  measure LCP 1.1 s, CLS 0 and TBT 0 ms, at 1.0 in every category.
  - **The plan's "INP < 200 ms" is asserted as TBT < 200 ms, and
    `lighthouserc.cjs` says why where the thresholds are.** INP is a field
    metric — the latency of interactions real people performed — and a lab run
    performs none. Total Blocking Time is the lab proxy, and 200 ms is the
    threshold that keeps a page's long tasks short enough for an interaction to
    land inside INP's own "good" bucket. Real INP needs CrUX or RUM, which is a
    post-launch measurement rather than a gate. A test asserts the paragraph is
    still there, so the discrepancy cannot be closed by lowering a number.
  - **The gate has a demonstrated failure.** `pnpm --filter marketing
    lighthouse:fixture` injects one page that hydrates an island for no reason,
    asserts the same budget against it *by reference*, and fails if the budget
    does **not** catch it on `total-blocking-time` and the performance score.
    Naming the assertions matters: the fixture page is `noindex`, so LHCI exits
    non-zero on the SEO category whatever else is true, and an exit-code check
    would stay green with the whole performance budget deleted.
  - **The pricing island now hydrates on `client:visible`, not `client:load`.**
    It is React plus every Radix primitive `@forge/ui` re-exports — 367 kB
    uncompressed — and fetching that immediately pushed the page's first paint
    from 1.1 s to 2.2 s and its LCP to 3.4 s, over budget. Nothing is lost: both
    price sets, the plan cards and the CTA are in the HTML before anything
    hydrates. The other three pages ship no JavaScript at all.
  - **`scripts/check-budget.mjs` refuses the three budget killers on every
    build**, in seconds rather than from a twelve-run job: a `@font-face`
    without `font-display: swap` or served from another origin, a webfont with
    no `<link rel="preload" as="font">` anywhere, an `<img>` missing width or
    height, and any subresource from a host that is not this one. The
    third-party allowlist is empty and adding to it is the
    [ADR-0004](docs/adr/0004-marketing-framework.md) §7 conversation, plus a CSP
    entry (T-307) and a look at consent (T-308). All four rules are vacuously
    satisfied today — the site has no webfonts, no images and no third parties —
    which is the state they exist to keep it in.
  - **`CardTitle` takes an `as` prop** (`packages/ui`), defaulting to the
    `<div>` it has always rendered. The pricing page's plan names are now
    `<h2>`s, which is what the first Lighthouse run caught: three cards under an
    `<h1>`, each containing an `<h3>`, is a skipped heading level and scored
    0.99 on accessibility. A build-time test now checks every one of the site's
    51 pages for skipped levels, so the two-minute job is not the only thing
    standing between that and a release.

- **The locale is in every marketing URL, and `hreflang` says so (T-305).**
  `apps/marketing` now prefixes every page with a locale segment, the default
  locale's included — `/en/pricing`, not `/pricing` — which is `apps/web`'s
  policy since T-233. The point is continuity: a visitor reading `/fi/pricing`
  who clicks "Sign in" lands on the app's `/fi/register` rather than watching
  their language disappear and come back. `appLink(path, locale)` takes the
  locale as a required argument so that cannot regress quietly, and the policy
  itself is one decision in two places (`DEFAULT_LOCALE_IS_PREFIXED` in
  `src/lib/routing.ts`, `prefixDefaultLocale` in `astro.config.mjs`) with a
  test that fails if only one of them moves.
  - **A locale-less URL resolves one and redirects**, as `index.tsx` and
    `$.tsx` do in the app. `/` is where the apex domain lands and the 404 is
    the one file a static host can fall back to for `/pricing`, so
    `src/components/LocaleRedirect.astro` serves both. It matches the browser's
    languages against the enabled ones and falls back to the default; the app's
    other two inputs — a stored preference, an in-session choice — do not exist
    on a static, session-less origin that is not even the same origin as the
    app. `/` also renders a language chooser in each locale's own name, so the
    page works without JavaScript. A 301 at the edge is better and belongs to
    T-307's Caddy block.
  - **Every indexable page announces the locales it exists in, its own
    included**, plus `x-default`. All of it comes from one list — the same one
    `og:alternate_locale` and the footer's new language switcher read — so a
    post written only in English cannot advertise a Finnish translation, and a
    reader is offered exactly what a crawler is.
  - **`scripts/check-hreflang.mjs` fails the build** on an annotation whose
    target was never built, is on another origin, or is written in another
    language; on a cluster that is not confirmed both ways, which a crawler
    discards whole; on a page that annotates every locale but its own; on an
    annotation for a locale `product.config.ts` does not enable; and on
    `lang`/`dir` disagreeing with `@forge/i18n`'s registry or with the locale
    in the URL — which is where `ar` is held to `dir="rtl"` on every page,
    rather than by a second copy of a visual test that is known not to pass.
  - **A page that is a copy or a door carries no annotation and is
    `noindex`**: the 404, the locale-resolving `/`, and a legal document in a
    locale nobody has translated it into. That last is the price of T-301's
    deliberate exception — legal documents fall back rather than 404, because
    they are linked from every footer — and the copy is now kept out of the
    index instead of being annotated as a translation it is not. None of the
    three pairs `noindex` with a canonical pointing elsewhere, which is how a
    site accidentally deindexes the page it points at.

- **`robots.txt` and a sitemap that cannot disagree with it (T-304).**
  `robots.txt` is now an endpoint (`apps/marketing/src/pages/robots.txt.ts`)
  rather than a file in `public/`, because the one thing it exists to decide —
  is *this* build the production site? — is not knowable until a build has an
  origin. `src/config/indexing.ts` holds that decision and the paths no
  crawler may follow.
  - **Indexing is opt-in.** A build is offered to crawlers only when
    `PUBLIC_SITE_URL` matches the `PRODUCTION_ORIGIN` declared in
    `src/config/indexing.ts`, which ships as the template's placeholder
    domain. Every other build — a fresh checkout on `http://localhost:4321`, a
    preview deploy, a staging host — serves `User-agent: *` / `Disallow: /`,
    offers no sitemap, and says so loudly in the build log. An un-indexed
    launch is one line away from being fixed; a staging site that outranks
    production is a reconsideration request and a month of waiting, so that is
    the direction to be wrong in. The other half of the staging rule, an
    `X-Robots-Tag: noindex` response header, belongs to T-307 — marketing has
    no served host yet.
  - **The disallow list covers paths this host does not serve.** The API's
    surface and every private `apps/web` route are listed even though T-307
    puts marketing on its own origin, because `robots.txt` is per-origin *and*
    per-deployment: a fork that ever serves the app under this domain gets the
    right rules without having to remember the file exists. Each prefix
    becomes two patterns (`/api$` and `/api/`), so `Disallow: /api` cannot
    quietly swallow a blog post about an API, and the app's routes carry a
    `/*/` because T-233 gives every one of them a locale segment.
  - **The sitemap filter reads the built HTML.** A page leaves the sitemap
    because its own markup carries `noindex`, not because it is named in a
    list somebody has to maintain. Drafts and a disabled module's routes need
    no filtering at all — they are never built, and the sitemap only ever sees
    pages that were.
  - **`scripts/check-indexing.mjs` fails the build on a disagreement.** A
    filter can only ever *remove*, so the check beside it catches what one
    cannot: a sitemap URL that resolves to no built page, a listed page that
    asks not to be indexed, a built and indexable page that is in no sitemap,
    a sitemap URL `robots.txt` disallows, a URL on an origin other than the
    one the build was given (the shape of a deploy that forgot
    `PUBLIC_SITE_URL`), and a `Sitemap:` line naming a file that was not
    written. "Every URL in the sitemap resolves" is now a build gate rather
    than something checked once by hand.
  - **The `Sitemap:` line points at `/sitemap-index.xml`.**
    `@astrojs/sitemap` always writes an index plus numbered chunks and never a
    `sitemap.xml`; a sitemap index is a first-class thing to submit, and a
    `Sitemap:` line naming a 404 is not. The check asserts the file it names
    exists.
  - `scripts/html.mjs` now holds the quote-aware tag reading both build gates
    do, so `check-seo` and `check-indexing` share one parser — and one set of
    the escaping mistakes T-303 already found once.

- **JSON-LD, built from the data the pages already render (T-303).**
  `src/components/JsonLd.astro` is the only place this site serialises
  structured data, and `src/lib/structured-data.ts` the only place it builds
  any: `Organization` site-wide, `SoftwareApplication` on the landing page,
  a `Product` with a monthly and a yearly `Offer` per plan on `/pricing`,
  `FAQPage` on both, `BlogPosting` on a post and `BreadcrumbList` on a
  guide. Everything is typed with `schema-dts` — types only, zero runtime
  bytes — so an `Offer` with `currency` instead of `priceCurrency` is a
  compile error rather than a Search Console notice six weeks later.
  - **The Rich Results Test needs a public URL; its error rules do not.**
    Google's hosted tool cannot see this site until T-307 gives it a host, so
    the part of it that reports *errors* — the properties each type must
    carry — is a table in `tests/structured-data.test.ts` walked over every
    node on every built page. It is why the landing page's
    `SoftwareApplication` carries an `AggregateOffer`: Google's Software App
    result requires one of `offers`, `aggregateRating` or `review`, and a
    template has no honest ratings. The range comes from the same catalogue
    and points at `/pricing`, where the per-plan detail lives.
  - **Nothing is a second copy of anything.** The offers quote the same cents
    `src/lib/plan-view.ts` formats into the cards, the post metadata is the
    frontmatter the article renders, and the product name comes from
    `src/config/site.ts` like every other mention of it. A test takes the
    price out of a rendered `Offer`, formats it with the page's own
    formatter, and requires the result to be on the page — in three locales,
    where the same cents render as `$49`, `49 $` and `49 $US`.
  - **Structured data and visible markup come out of one array.**
    `FaqSection.astro` emits the `<dl>` a visitor reads and the
    `FAQPage` node a crawler reads from the same list of questions, and
    `Breadcrumbs.astro` does the same for the trail. Google's rule is that
    FAQ structured data must reflect what is on the page; the usual way that
    breaks is a hand-maintained block drifting from copy someone edited, and
    here there is nothing to drift. The FAQ itself is new visible content, in
    all six locales, and its billing question follows the `billing` toggle.
  - **A payload cannot end its own script block.** Every left angle bracket is
    written as its JSON unicode escape, which closes both ways out of a
    raw-text element (`</script` and `<!--`). A blog post whose title
    contains a closing script tag is a test fixture, and the assertion scans
    the built HTML exactly as a parser would.
  - **The plan catalogue is fetched once per build, not once per page.** Both
    `/` and `/pricing` need it now, in six locales each, so `loadPlans()`
    memoises its promise — one process builds the whole site, and a dozen
    identical requests (and a dozen copies of the fallback warning) were the
    alternative.
  - **On CSP** (T-307 owns marketing's header): a `<script>` whose `type`
    is not a JavaScript MIME type is a *data block* in the HTML spec — never
    prepared, never executed — so `script-src` does not gate these and they
    need no nonce or hash. What would break them is a header that forbade
    inline markup wholesale.
  - **`check-seo.mjs` no longer truncates a tag at a `>` inside an
    attribute value.** Only `&` and `"` need escaping there, so a
    description containing `>` is valid markup that the old `<meta[^>]*>`
    pattern cut in half — reading the `content` attribute as empty and
    failing the build over a page that was fine. Found by T-303's escaping
    fixture; pinned by `tests/fixtures/seo/clean/angle-brackets.html`.
  - The generated favicon now declares an intrinsic size, because it is also
    the `Organization` logo and Google asks for at least 112×112.
    `product.config.ts`'s `logoUrl` is deliberately not used: it points at
    `/logo.svg`, which nothing in this repository serves.

- **The marketing content surface: pages, MDX collections, and pricing (T-301).**
  Every route now exists in every enabled locale — landing, features, pricing,
  blog, guides, privacy, terms and a 404 — built from one page file each under
  `src/pages/[...locale]/` rather than a duplicated page tree. Where the locale
  sits in the URL is `src/lib/routing.ts` and one line of `astro.config.mjs`,
  and nowhere else; T-305 flips those two together to match `apps/web`'s T-233
  policy.
  - **Content collections with zod schemas**, so a frontmatter mistake fails
    the build with a message naming the file instead of rendering `undefined`
    in production. `blog`, `guides` and `legal`, each keyed by locale in its
    path (`src/content/blog/<locale>/<slug>.mdx`) — the layout T-305 needs, and
    what makes "this post is not published in Finnish" expressible at all. A
    missing date says `publishedAt: is required, and must be a date like
    2026-08-22`, because `z.coerce.date()` on its own reports a *missing* field
    as "expected date, received object".
  - **`/guides`, not `/docs`.** The repo-root `Caddyfile` routes `/docs*` to the
    backend's Swagger UI. T-307 gives marketing its own host, which technically
    frees the path, but a template whose product docs and API docs differ only
    by hostname is a trap. The collection is called `guides` too.
  - **Pricing is fetched at build time, with a committed fallback.** Plans live
    in the billing module's database and a static site cannot query at request
    time, so `src/lib/plans.ts` reads `/api/v1/billing/plans` once during the
    build and falls back to `src/content/pricing.ts` when it cannot — an API
    that is restarting during a deploy must not mean a pricing page with no
    prices. The staleness that buys is written down in that file: **prices
    change on the next deploy, not the next `UPDATE`**, and the page says so on
    itself when it rendered from the committed catalogue.
  - **The pricing period toggle is the site's one island**, and it replaces
    T-300's `island-check.astro` as the executable form of ADR-0004's
    constraint 1 — Radix tabs out of `@forge/ui`, hydrated with the package
    unmodified. Everything else on the site is markup, and `/` still ships zero
    script bytes.
  - **A disabled module disappears from all three places at once.** The header,
    the footer and the sitemap read one list (`src/lib/nav.ts`), so with
    `billing` off there is no `/pricing` page, no link to one, and no sitemap
    entry — asserted together, because a half-applied toggle is exactly what
    survives when they are checked separately.
  - **Drafts render in `astro dev` and never reach `dist/`.** The filter keys
    off Vite's `MODE` rather than `import.meta.env.PROD`: `PROD` is derived
    from `NODE_ENV`, which `vitest` and plenty of CI images set to something
    other than `production`, and under those a build would have shipped the
    drafts.
  - `@tailwindcss/typography` for MDX prose, with its palette variables pointed
    at `@forge/ui`'s tokens so a guide is the same ink as a `<Card>` beside it.

- **The module matrix covers the build now, not just the process (T-291).**
  `backend/tests/modules/test_module_toggles.py` proved that a disabled
  module's routes and tables disappear together, inside one Python process
  with `get_modules()` monkeypatched. What it could not see is a migration
  run from an empty database, an app booting off a real `product/config.py`,
  or the frontend build — and from T-300 the build is itself gated by a
  toggle, since `marketing` switches off an app's build rather than a router.
  - **`MODULE_SURFACE`** declares, once per module, every route and table
    that module's toggle owns. Each scenario now asserts that the route table
    equals the baseline minus the disabled modules' surfaces, so a route that
    outlives its module *and* a route that vanishes with nothing claiming it
    both fail. That declaration is checked for set-equality against
    `ModuleName` itself, which is what makes "all fourteen toggles are
    covered" a fact rather than a claim. Five scenarios, ending in `minimal`
    — every optional module off.
  - **CI's `module-matrix` job** runs three whole-repo combinations: the
    committed `product/config.py`, every optional module off, and the three
    Phase 4 modules on. Each migrates an empty database through
    `migration_targets()`, boots uvicorn as a subprocess, compares
    `GET /api/v1/config` field-for-field against the config on disk, runs
    `pytest -m "not module_dependent"`, and builds, lints and type-checks the
    whole workspace.
  - **`module_dependent`** (registered in `backend/pyproject.toml`) marks the
    backend tests that need an optional module's routes, tables or seed rows
    — 361 of 2081 today. A test *without* it must pass with every module off,
    so one written against a module's surface and left unmarked fails the
    minimum-surface combination instead of being quietly skipped by it.
  - `backend/tests/fixtures/module_configs/` holds the config pairs that job
    copies — each a whole `product/config.py` replacement plus its
    `product.config.ts` twin — guarded in-process by
    `backend/tests/test_product_config_parity.py`: every fixture loads,
    passes `validate_modules()`, agrees key-for-key with its twin, and
    differs from the shipped config in nothing but its modules. There is
    deliberately no `default` fixture: the first combination runs the
    product's own committed config, which is the file a template user
    actually edits.
  - `docs/runbooks/module-toggle-guide.md` gains a section per Phase 4
    module, including the one re-enable in this template that carries a
    security consequence — turning `public_api` back on makes every surviving
    `api_key` row a working credential again, immediately, with no deploy and
    no notice to anyone.
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
- **The TypeScript client is generated offline, from an all-modules-on schema
  (T-313).** `packages/api-client` used to be generated from a *live*
  `/openapi.json`, which meant it contained only the routes the committed
  `product.config.py` happened to enable — so a default-off module had no typed
  symbols for its own UI to import, and generating from a smaller configuration
  deleted symbols `apps/web` imports unconditionally. `backend/scripts/dump_openapi.py`
  now writes the document with every module enabled, and the generated client
  is the union of every configuration, which is what the SPA's runtime module
  gating already assumed. Five CI jobs stopped booting a uvicorn (and one
  stopped racing itself for port 8000) purely to serve a schema, and
  `make gen-client` no longer needs Docker.

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
