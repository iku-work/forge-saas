# `apps/marketing`

The public marketing site: pre-rendered, sharing `@forge/ui`'s design tokens
and `@forge/i18n`'s locale files with the product, and deployed as a directory
of static files. Astro, `output: "static"` — the reasoning is in
[ADR-0004](../../docs/adr/0004-marketing-framework.md), and it is worth reading
before changing anything structural here.

Built by T-300, with the content surface added by T-301, the meta block by
T-302, the structured data by T-303 and `robots.txt` plus the sitemap filter by
T-304, and the locale prefix policy plus its `hreflang` annotations by T-305.
T-306 and T-307 add the Lighthouse budget and the Caddy host.

## Dependencies, and why each one is here

§7 asks for a justification comment on every new dependency, and `package.json`
cannot carry comments — so they live here.

| Dependency                                 | Why                                                                                                                                                                                                                                      |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `astro`                                    | The framework, chosen in ADR-0004. Islands rather than RSC, static output, Vite-based so it reuses this repo's toolchain.                                                                                                                |
| `@astrojs/react`                           | Mounts `@forge/ui`'s React 18 components — as static HTML by default, hydrated only where a page writes a `client:*` directive. Without it the product's component library could not be shared at all, which is constraint 1 of the ADR. |
| `@astrojs/mdx`                             | The blog and guides are MDX. First-party, and pairs with content collections so a frontmatter violation fails the build instead of shipping.                                                                                             |
| `@tailwindcss/typography`                  | Prose styling for that MDX. The alternative is a hand-rolled type scale that drifts from `@forge/ui`'s the first time either changes; its colour variables are pointed at the shared tokens in `src/styles/global.css`. CSS only.        |
| `@astrojs/sitemap`                         | The sitemap. T-304 gives it a filter that reads each built page's robots directive, and a check beside it that holds the sitemap, `robots.txt` and `dist/` to each other.                                                                |
| `@tailwindcss/vite` + `tailwindcss`        | The same Tailwind v4 plugin `apps/web` and `@forge/ui`'s Storybook run. A different CSS pipeline here would mean the shared token layer is interpreted two ways.                                                                         |
| `react`, `react-dom`                       | Peer dependencies of `@forge/ui`, pinned to the 18.x the rest of the workspace is on.                                                                                                                                                    |
| `@forge/ui`, `@forge/i18n`, `@forge/types` | The shared component library, the locale JSON, and `ProductConfig`'s type.                                                                                                                                                               |
| `schema-dts` (dev)                         | Compile-time types for the JSON-LD in `src/lib/structured-data.ts`. Types only — nothing reaches the bundle — and it is what turns `"@type": "SoftwareApp"` or an `Offer` with `currency` instead of `priceCurrency` into a build error rather than a Search Console notice weeks later.                              |
| `jiti` (dev)                               | Reads the repo-root `product.config.ts` from plain Node, in `scripts/`, before Vite exists to compile TypeScript. See `scripts/product-config.mjs` for why the file is loaded rather than pattern-matched.                               |
| `@astrojs/check` + `typescript` (dev)      | `astro check`, wired as this package's `type-check` so the root turbo task covers `.astro` files.                                                                                                                                        |
| `vitest` (dev)                             | The workspace's test runner. The tests here assert over `dist/`, so they run real builds.                                                                                                                                                |
| `@types/react`, `@types/react-dom` (dev)   | Types for the island components.                                                                                                                                                                                                         |

## The module toggle

`marketing` in `product.config.ts` gates this **build**, not a router — it is
the only module in the product that does. `scripts/guard-enabled.mjs` reads the
toggle and, when it is off, skips `astro build`, removes any stale `dist/`, and
exits 0. CI asserts both directions in the `module-matrix` job's "The marketing
build honours its toggle" step.

Two consequences worth knowing:

- With the module off, `turbo` prints
  `WARNING no output files found for task marketing#build`. That is correct,
  not a misconfiguration: there is no output, because there is no site.
- `product.config.ts` is a `globalDependencies` entry in `turbo.json`. It lives
  at the repo root, outside every package's inputs, so without that entry
  turbo would replay a cached build from before the toggle flipped.

`FORGE_PRODUCT_CONFIG` points the whole app — guard, locale list and pages
alike — at a different config file. It is a test seam that moves a _path_,
never a boolean, and CI does not use it: the `module-matrix` job copies its
fixture over the real path instead.

## Conventions

**The island rule.** A page may only hydrate a component that cannot be
expressed as markup — a pricing toggle, a mobile nav, an FAQ accordion.
Everything else renders in `.astro`, including `@forge/ui` components, which
Astro will render to plain HTML when no `client:*` directive asks otherwise.
The landing page is held to **zero script bytes** by `tests/build.test.ts`;
that assertion is the rule's teeth, and T-306's performance budget is what
notices when it goes.

**Translate at build time.** Pages read `@forge/i18n` through `src/lib/i18n.ts`
and pass finished strings to islands as props. No i18next provider: every
island is its own React root, so a provider would ship i18next to a static page
and initialise it once per island. An island that genuinely switches language
in the browser is the only reason to revisit that, and it needs justifying in
review.

**One brand.** Colours, radii and the light/dark sets come from
`@forge/ui/styles/tokens.css`, which `apps/web` and Storybook import too.
`primaryColor` in `product.config.ts` is the only place the brand colour is
written down — `src/pages/favicon.svg.ts` draws the favicon from it rather than
committing an asset that would have to be kept in step, and
`scripts/make-og-image.mjs` draws the social card from it. Inside `src/`, the
product's name, brand colour and support address are read from
`src/config/site.ts` and nowhere else; `src/lib/product.ts` stays the reader of
the config file itself (modules, locales, the app's URL).

**Tailwind does not scan `node_modules`.** `src/styles/global.css` carries an
explicit `@source` for `packages/ui/dist`. Remove it and an island's classes go
missing silently: it renders, unstyled, and nothing errors.

## Commands

```bash
pnpm --filter marketing dev          # astro dev, through the toggle guard
pnpm --filter marketing build        # astro build, through the toggle guard
pnpm --filter marketing preview      # serve the built dist/
pnpm --filter marketing type-check   # astro check
pnpm --filter marketing test         # build-output assertions (runs real builds)
pnpm --filter marketing og-image     # redraw public/og-default.png from primaryColor
```

`PUBLIC_SITE_URL` sets the absolute origin the site is served from — every URL
a crawler is given resolves against it, and whether `robots.txt` allows
anything at all depends on it; `PUBLIC_APP_URL` is where `apps/web` lives, for
the sign-in and sign-up links; `PUBLIC_API_URL` is the backend the build fetches
the plan catalogue from. All three have documented defaults or a documented
fallback, so a fresh checkout builds without an env file.

`pnpm --filter marketing test` runs several real builds and one real dev
server, so it takes about a minute. That is the cost of asserting over `dist/`
rather than over a component in isolation.

## Routes and content

Every page lives under `src/pages/[...locale]/` and is built once per enabled
locale. One file, one copy of the markup — not the usual Astro shape of a
duplicated page tree (`src/pages/pricing.astro` plus
`src/pages/[locale]/pricing.astro`) where the two copies drift.

| Route                       | Source                                                |
| --------------------------- | ----------------------------------------------------- |
| `/<locale>/`                | `index.astro`                                          |
| `/<locale>/features`        | `features.astro`, module-aware                         |
| `/<locale>/pricing`         | `pricing.astro` — only when `billing` is enabled       |
| `/<locale>/blog`, `/blog/*` | the `blog` collection                                  |
| `/<locale>/guides/**`       | the `guides` collection, `[...slug]`                   |
| `/<locale>/privacy`, `/terms` | the `legal` collection                               |
| `/`                         | `src/pages/index.astro` — resolves a locale, noindex   |
| `404.html`                  | `src/pages/404.astro`, default locale, noindex         |
| `/robots.txt`               | `src/pages/robots.txt.ts`, an endpoint                 |
| `/favicon.svg`              | `src/pages/favicon.svg.ts`, drawn from `primaryColor`  |

**Every URL carries a locale segment, the default locale's included** —
`/en/pricing`, not `/pricing`. That is `apps/web`'s policy (T-233), and T-305
made the two identical so that a visitor crossing from `/fi/pricing` to the
app's `/fi/register` never watches their language disappear. It is one decision
expressed in two places, `DEFAULT_LOCALE_IS_PREFIXED` in `src/lib/routing.ts`
and `prefixDefaultLocale` in `astro.config.mjs`, and `tests/routes.test.ts`
fails if only one of them moves.

**A locale-less URL resolves one and redirects**, which is what `index.tsx` and
`$.tsx` do in the app. `src/components/LocaleRedirect.astro` is both halves: on
`/`, where the apex domain lands, and on the 404, which is the one file a
static host can fall back to for `/pricing`. It matches the browser's languages
against the enabled ones and falls back to the default; the app's other two
inputs — a signed-in user's stored preference and an in-session choice — do not
exist on a static, session-less origin that (T-307) is not even the same origin
as the app. `/` also renders a language chooser, so the page works without
JavaScript. **T-307 should promote this to a 301 at the edge.**

**Sign-in and sign-up links carry the locale.** `appLink(path, locale)` in
`src/lib/product.ts` takes it as a required argument rather than defaulting,
because the failure it prevents is silent: a locale-less `/login` hands the
decision back to the app, which answers it from inputs this site cannot see.

**Content is keyed by locale in its path**: `src/content/blog/<locale>/<slug>.mdx`,
`src/content/guides/<locale>/<section>/<slug>.mdx`,
`src/content/legal/<locale>/<slug>.mdx`. An untranslated post is *absent* from
that locale rather than served in English under a `hreflang` annotation that
says otherwise. Legal documents are the deliberate exception — they are linked
from every footer, so they fall back to the original with a visible notice
rather than 404ing. `legalDocument()` in `src/lib/content.ts` says why.

**A frontmatter mistake fails the build**, naming the file. That is the entire
reason these are collections rather than a glob — see `src/content.config.ts`.

**Drafts** (`draft: true`) render in `astro dev` and never reach `dist/`. The
filter keys off Vite's `MODE`, not `import.meta.env.PROD`: `PROD` is derived
from `NODE_ENV`, and anything that sets `NODE_ENV` to something other than
`production` — `vitest` does, and so do plenty of CI images — would otherwise
publish them.

**Adding a page** means adding it to `src/lib/nav.ts` too — the header and the
footer both read that one list, so a module toggle takes a link out of both at
once instead of leaving one pointing at a 404. The sitemap is the other half of
the same rule and works the other way round: it lists what was *built*, so a
page whose `getStaticPaths` is gated on a module leaves the sitemap by itself.
`/pricing` with `billing` off is the worked example, and `tests/routes.test.ts`
asserts all three places together.

## The sitemap and `robots.txt`

**Nothing is indexable until a product says where production is.**
`PRODUCTION_ORIGIN` in `src/config/indexing.ts` is that one line, and it ships
as the placeholder `https://example.com`. A build whose `PUBLIC_SITE_URL` is
anything else — a fresh checkout on `http://localhost:4321`, a preview deploy,
a staging host — serves a `robots.txt` that disallows every crawler from
everything and offers no sitemap. That is the safe direction to be wrong in: an
un-indexed launch is one line away from being fixed, while a staging site that
outranks production is a reconsideration request and a month of waiting. Every
build says which way it decided, in the build log.

`robots.txt` is an **endpoint** (`src/pages/robots.txt.ts`), not a file in
`public/`, because that decision is not knowable until a build has an origin.
A committed `public/robots.txt` would also win over the route and then rot.

**The disallow list covers paths this host does not serve**, and that is
deliberate — the API's own surface and every private `apps/web` route. T-307
gives marketing its own origin, so a crawler here will never see an `/api/`;
the rules are there because `robots.txt` is per-origin *and per-deployment*, and
a fork that ever puts the app under this domain gets them for free. The build
gate is what keeps that free: it fails if any of those patterns ever matches a
page this site actually built.

**The `Sitemap:` line points at `/sitemap-index.xml`**, not `/sitemap.xml`.
`@astrojs/sitemap` always writes an index plus numbered chunks, and there is no
`sitemap.xml` here to point at; a sitemap index is a first-class thing to
submit, and a `Sitemap:` line naming a file that 404s is not.

**`scripts/check-indexing.mjs` is two things wired together in
`astro.config.mjs`.** The filter drops a page from the sitemap when the page's
own built HTML carries a `noindex` — read from the markup rather than from a
list kept beside it, because a list is a second place to remember. The check
that follows it fails the build on anything a filter cannot catch, because a
filter can only ever *remove*:

- a sitemap URL that resolves to no built page (this is "every URL in it
  resolves", as a gate rather than a thing somebody checked once);
- a page listed in the sitemap that asks not to be indexed;
- a page that was built, wants to be indexed, and is in no sitemap;
- a sitemap URL that `robots.txt` disallows;
- a URL on an origin other than the one the build was given — the shape of a
  deploy that forgot `PUBLIC_SITE_URL`;
- a `Sitemap:` line naming a file that was not written, or a staging build
  offering one at all.

Runnable by hand over any built output:
`node scripts/check-indexing.mjs [dir] [origin]`.

**Drafts and a disabled module's routes need no filtering.** They are never
*built* — `isPublished()` in `src/lib/content.ts` drops a draft from the
collection, and a module-gated page returns no paths from its own
`getStaticPaths` — and the sitemap only ever sees pages that were.

**Per-locale `<xhtml:link rel="alternate">` entries** come from the `i18n`
option passed through `indexing()`. `@astrojs/sitemap` builds them from the
URLs that are actually in the sitemap, so they cannot advertise a translation
nobody wrote — the same rule the `hreflang` tags in the head follow, arrived at
from the other end.

**The half T-304 cannot do:** `X-Robots-Tag: noindex` on non-production hosts.
It is a response header, marketing has no served host until T-307, and T-307's
task carries the requirement. `robots.txt` is the half that needs no server
configuration, and it is done.

## The meta block

Every page's `<title>`, description, canonical, Open Graph and Twitter card
come from `src/components/Seo.astro`, which `BaseLayout` renders from the props
it already takes. **No page imports it and no page can forget it** — a page
that needs more than the defaults (a post is an `article` with a date; an entry
can carry its own card) passes them through the layout. Defaults live in
`src/config/site.ts`.

`scripts/check-seo.mjs` is what makes that a guarantee rather than a
convention. It runs as an Astro integration on `astro:build:done`, walks
`dist/**/*.html`, and **fails the build** on any page missing a title, a
description or a canonical, carrying two of one, or carrying an empty one — the
cases the component cannot see, like a page written without the layout. It is
wired into the build rather than into `package.json` because every build here
goes through `scripts/guard-enabled.mjs`, the tests' builds included. Run it by
hand over any directory of HTML with `node scripts/check-seo.mjs [dir]`.

Every URL it emits is absolute, resolved against `Astro.site` — which is
`PUBLIC_SITE_URL`, read once in `astro.config.mjs`. A crawler has no base URL
to resolve a root-relative `og:image` against.

**The Open Graph card** is one committed file, `public/og-default.png`, drawn
from `primaryColor` by `scripts/make-og-image.mjs` (a PNG encoder in ~40 lines
of `zlib`, not a dependency). Generating a card per page at build time is an
explicit non-goal: satori or a headless browser is a rendering dependency and a
CI browser for a marginal gain over a good default. The card carries no words —
a template does not know the product it will become, and the only text it could
draw is `product.name`, which would mean shipping a font. `og:image:alt` carries
the name instead. A product with a designer replaces that one file; a product
that only changes its brand colour runs `pnpm --filter marketing og-image`,
which a test in `tests/seo.test.ts` will remind it to do.

Individual entries override the card with `ogImage` in their frontmatter, and
`og:alternate_locale` lists the locales an entry was **actually** translated
into (`entryLocales()` in `src/lib/content.ts`) rather than every enabled one.
The `hreflang` links below are built from that same list.

## Locale annotations

Every indexable page carries `<link rel="alternate" hreflang="…">` for each
locale it exists in — **its own included**, because a cluster Google will act
on is one where every member points at every member — plus `x-default` for the
default locale's version. `<Seo>` derives all of it from `alternateLocales`,
the one answer to "where does this page exist", which is also what the footer's
language switcher renders. A reader and a crawler are offered the same set by
construction.

Three kinds of page carry none of it, and all three are `noindex`: the 404, the
locale-resolving `/`, and a legal document in a locale it has not been
translated into. A copy is not the canonical anything, and putting one into a
cluster is how a duplicate ends up outranking the page it copied. (`noindex`
with a canonical pointing *elsewhere* is deliberately avoided — Google applies
the directive to whatever a page canonicalises to, which is how a site
accidentally deindexes its own home page. Every one of these is
self-canonical.)

`scripts/check-hreflang.mjs` fails the build on:

- an annotation pointing at a URL that was not built, or is on another origin;
- an annotation pointing at a page written in a different language — the exact
  claim `hreflang` exists to prevent;
- a cluster that is not confirmed both ways, which a crawler discards whole;
- a page that annotates every locale but its own;
- an annotation for a locale `product.config.ts` does not enable;
- an indexable page under a locale that annotates nothing, or a `noindex` page
  that still does;
- `lang` or `dir` on any page disagreeing with `@forge/i18n`'s registry or with
  the locale in the URL. This is where `ar` is held to `dir="rtl"`, on every
  page — T-305 rules out a visual regression test for it, since T-262's is
  known unpassable.

Runnable by hand over any built output:
`node scripts/check-hreflang.mjs [dir] [origin]`.

## Structured data

Every page carries JSON-LD, and all of it is built from the data the page
already renders. `src/lib/structured-data.ts` is the only place a node is
constructed and `src/components/JsonLd.astro` the only place one is
serialised.

| Node                  | Where                                | Built from                                     |
| --------------------- | ------------------------------------ | ---------------------------------------------- |
| `Organization`        | every page, via `BaseLayout`         | `src/config/site.ts`                           |
| `SoftwareApplication` | `/`                                  | the hero copy, plus a price range from the same catalogue |
| `Product` + `Offer`   | `/pricing`, one per plan             | the same `MarketingPlan[]` the cards render    |
| `FAQPage`             | `/` and `/pricing`                   | `src/lib/faq.ts` + `@forge/i18n`               |
| `BlogPosting`         | a post                               | its frontmatter                                |
| `BreadcrumbList`      | a guide                              | the trail the page displays                    |

**Two rules decide where a node lives.** A schema that describes the *page*
travels through `BaseLayout`'s `schema` prop into the head, beside `<Seo>`'s
meta — no page imports `<JsonLd>` for it. A schema that describes a *block of
markup* is emitted by the component that renders that markup, from the same
array: `FaqSection.astro` produces the `<dl>` a visitor reads and the
`FAQPage` node a crawler reads in one pass, and `Breadcrumbs.astro` does the
same for the trail. Google requires structured data to reflect visible content,
and building both from one array is what stops that being a thing reviewers
have to check.

**A payload cannot end its own script block.** `serialiseJsonLd` rewrites
every left angle bracket as its JSON unicode escape, which closes both ways out
of a raw-text element — a closing script tag and a comment opener. A blog post
about exactly that is one of the test fixtures.

**These blocks need no CSP exception.** A `<script>` whose `type` is not a
JavaScript MIME type is a *data block* in the HTML spec: the browser never
prepares or executes it, so `script-src` does not gate it and no nonce or hash
is involved. T-307 owns marketing's header; what it must not do is forbid
inline markup wholesale.

**Google's Rich Results Test needs a public URL**, which this repository does
not have until T-307 puts the site on a host. What can be a build gate instead
is the part of that tool which reports *errors*: `tests/structured-data.test.ts`
carries a table of the properties Google documents as required per type and
walks every node on every built page against it. It is why
`SoftwareApplication` here carries an `AggregateOffer` — Google's Software App
result requires one of `offers`, `aggregateRating` or `review`, and a template
has no honest ratings to give. The range points at `/pricing`, where the
per-plan `Product` and `Offer` nodes sit beside the numbers a reader can check.

**Adding a question** to the FAQ means adding its two keys to
`packages/i18n/locales/*/marketing.json` and naming it in `src/lib/faq.ts` —
the same shape as `src/lib/nav.ts`, and for the same reason: a question about
billing has to leave the page *and* the JSON-LD when the module does.

## Pricing data

Plans live in the `billing` module's database, and `output: "static"` means
there is no request-time code that could query it. So `src/lib/plans.ts`
fetches `/api/v1/billing/plans` **once, during the build**, and falls back to
the committed catalogue in `src/content/pricing.ts` when `PUBLIC_API_URL` is
unset or the fetch does not work. Set `PUBLIC_API_URL` in the environment that
builds a deploy; leaving it unset is the normal case for a fresh checkout and
for CI, neither of which has a backend running during the frontend build.

**The staleness contract: prices change on the next deploy, not the next
`UPDATE`.** Whoever changes a plan's price has two jobs, and the second is easy
to forget. There is no cache to bust and no webhook that will rebuild the site.
When the page rendered from committed data it says so on itself, so a wrong
price can be diagnosed without a build log.

## The pricing island

`src/components/PricingPlans.tsx` is the site's only island, and it is the
executable form of ADR-0004's constraint 1: Radix tabs imported straight from
`@forge/ui`, hydrating with the package unmodified — no `"use client"`
directives added, no wrapper layer. It replaced T-300's `island-check.astro`,
which existed only to make that claim checkable before there was a real island
to make it with. What has to survive is the assertion in
`tests/pricing.test.ts`, not any particular page.

Monthly-versus-yearly is the one thing on this site that markup cannot do.
Everything else — including `/`, which is held to zero script bytes — renders
in `.astro`.
