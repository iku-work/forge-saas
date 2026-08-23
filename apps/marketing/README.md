# `apps/marketing`

The public marketing site: pre-rendered, sharing `@forge/ui`'s design tokens
and `@forge/i18n`'s locale files with the product, and deployed as a directory
of static files. Astro, `output: "static"` — the reasoning is in
[ADR-0004](../../docs/adr/0004-marketing-framework.md), and it is worth reading
before changing anything structural here.

Built by T-300, with the content surface added by T-301, the meta block by
T-302 and the structured data by T-303. T-304–T-307 add the sitemap filter, the
locale prefix policy, the Lighthouse budget, and the Caddy host.

## Dependencies, and why each one is here

§7 asks for a justification comment on every new dependency, and `package.json`
cannot carry comments — so they live here.

| Dependency                                 | Why                                                                                                                                                                                                                                      |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `astro`                                    | The framework, chosen in ADR-0004. Islands rather than RSC, static output, Vite-based so it reuses this repo's toolchain.                                                                                                                |
| `@astrojs/react`                           | Mounts `@forge/ui`'s React 18 components — as static HTML by default, hydrated only where a page writes a `client:*` directive. Without it the product's component library could not be shared at all, which is constraint 1 of the ADR. |
| `@astrojs/mdx`                             | The blog and guides are MDX. First-party, and pairs with content collections so a frontmatter violation fails the build instead of shipping.                                                                                             |
| `@tailwindcss/typography`                  | Prose styling for that MDX. The alternative is a hand-rolled type scale that drifts from `@forge/ui`'s the first time either changes; its colour variables are pointed at the shared tokens in `src/styles/global.css`. CSS only.        |
| `@astrojs/sitemap`                         | `sitemap.xml`. T-304 replaces its defaults with the filter that drops `noindex` pages and disabled-module routes; until then it indexes what gets built, which is already the right set.                                                 |
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

`PUBLIC_SITE_URL` sets the absolute origin the site is served from (T-304 keys
`robots.txt` and the sitemap off it); `PUBLIC_APP_URL` is where `apps/web`
lives, for the sign-in and sign-up links; `PUBLIC_API_URL` is the backend the
build fetches the plan catalogue from. All three have documented defaults or a
documented fallback, so a fresh checkout builds without an env file.

`pnpm --filter marketing test` runs several real builds and one real dev
server, so it takes about a minute. That is the cost of asserting over `dist/`
rather than over a component in isolation.

## Routes and content

Every page lives under `src/pages/[...locale]/` and is built once per enabled
locale. One file, one copy of the markup — not the usual Astro shape of a
duplicated page tree (`src/pages/pricing.astro` plus
`src/pages/[locale]/pricing.astro`) where the two copies drift.

| Route              | Source                                          |
| ------------------ | ----------------------------------------------- |
| `/`                | `index.astro`                                    |
| `/features`        | `features.astro`, module-aware                   |
| `/pricing`         | `pricing.astro` — only when `billing` is enabled |
| `/blog`, `/blog/*` | the `blog` collection                            |
| `/guides/**`       | the `guides` collection, `[...slug]`             |
| `/privacy`, `/terms` | the `legal` collection                         |
| `404.html`         | `src/pages/404.astro`, default locale, noindex   |

**Where the locale lives in a URL is `src/lib/routing.ts`, and one line of
`astro.config.mjs`.** Today the default locale is unprefixed (`/pricing` is
English, `/fr/pricing` is French). That is *not* what `apps/web` does — T-233
prefixes every URL including `en` — and making the two identical is T-305's
job, which means flipping `DEFAULT_LOCALE_IS_PREFIXED` and
`prefixDefaultLocale` together. Read T-233 before touching either.

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

**Adding a page** means adding it to `src/lib/nav.ts` too. The header, the
footer and the sitemap read that one list, which is what makes a module toggle
remove a page from all three at once instead of leaving a link to a 404.

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
T-305's `hreflang` links need that same list and should read it from there.

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
