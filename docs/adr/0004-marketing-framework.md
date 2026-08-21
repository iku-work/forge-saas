# ADR-0004: Marketing site framework

## Status

Accepted — **Astro**, `output: "static"`. Replaces this file's own
`Proposed — placeholder`, which deferred the choice to T-300.

**Date:** 2026-08-21

## Context

`apps/web` is the product: an authenticated React 18 SPA behind a login, built
with Vite and TanStack Router, deployed as static assets Caddy serves. It is
the wrong tool for the marketing surface for one reason — a SPA ships an empty
`<div id="root">` and fills it in with JavaScript, and the landing page's job
is to be crawled, previewed, and painted fast. P4-A's own acceptance criteria
say so numerically: T-306 gates the marketing site on Lighthouse LCP < 2.5s,
INP < 200ms, CLS < 0.1, and SEO ≥ 95, and T-303/T-304 need per-page JSON-LD
and a generated `sitemap.xml`. Those are pre-rendering requirements.

So P4-A adds `apps/marketing` as a separate app rather than a route group
inside `apps/web`, and T-300 has to pick what renders it. Four constraints are
fixed by the repo as it stands, and they are the substance of the choice:

1. **It shares `@forge/ui`.** T-300 says so, and the alternative — a second
   button/card/typography implementation that drifts from the product's — is
   the thing a template exists to prevent. `@forge/ui` is React 18 + Radix
   primitives + Tailwind v4, built with tsup, and **ships no `"use client"`
   directives** (verified: zero occurrences in `packages/ui/src`). Every Radix
   component in it is client-only by construction — hooks, refs, portals. A
   framework whose default rendering mode is React Server Components therefore
   cannot consume the package as-is; it needs either directives added at the
   package boundary or a wrapper layer in the marketing app. That is a real,
   bounded cost, and it is the single biggest asymmetry between the two
   candidates.
2. **Locale lives in the path.** T-233 already settled this for `apps/web`
   (`/en/...`, `/fi/...`), and T-305 asks for `hreflang` per locale on the
   marketing side. Translations come from `@forge/i18n`, whose locale JSON is
   the source of truth for both apps (`docs/runbooks/translations.md`).
   Whatever is chosen has to route on a locale segment and emit alternate
   links per page — neither candidate makes this hard, but a framework's i18n
   story is where "shares the same locale files" quietly becomes "reimplements
   them".
3. **Content is MDX.** T-301 wants a blog and docs as MDX, which means the
   framework's content pipeline (collections, frontmatter typing, feed
   generation) is doing real work rather than being a checkbox.
4. **It deploys as static files.** The rest of this repo terminates at Caddy
   in front of a container (`Caddyfile.prod`, `docs/runbooks/deploy-render.md`).
   A framework that needs a Node server at runtime, or a specific hosting
   platform's edge, is a second deployment topology to operate — the thing
   ADR-0002's consequences section calls "one more thing that can be down".
   A pure static export sidesteps it entirely.

## Decision

**Astro**, configured `output: "static"` in `apps/marketing/astro.config.mjs`.
Each constraint, answered:

1. **The `"use client"` boundary never comes up, and that is why Astro wins.**
   Astro's islands model asks for the opposite annotation from RSC: a page is
   static HTML, and a React component becomes interactive only where someone
   writes `client:load` / `client:visible` / `client:idle` on it. So
   `@forge/ui`'s "every component is client-only" property stops being an
   obstacle and becomes the default assumption. **No directives were added to
   `packages/ui`, and no wrapper layer sits between it and the marketing app**
   — `apps/marketing/src/components/IslandCheck.tsx` imports `Button` from
   `@forge/ui` and hydrates it, and `apps/marketing/tests/build.test.ts`
   asserts against the built HTML that it still does. Next.js's
   RSC-by-default would have required directives at the package boundary
   (changing what `apps/web` imports too) or a wrapper layer in the marketing
   app.
2. **`astro:i18n` routes on a locale segment natively**, configured from the
   `locales` block of `product.config.ts` rather than a second hardcoded list.
   `@forge/i18n`'s locale JSON stays the single source of strings — the
   `marketing` namespace added in T-300 is the same shape, in the same files,
   that `apps/web` reads. T-305 pins the prefix policy to T-233's.
3. **`@astrojs/mdx` plus content collections with typed frontmatter**, both
   first-party. T-301 defines the collections; a frontmatter violation fails
   the build, which is the entire reason to prefer collections over a glob.
4. **`output: "static"` is a directory Caddy serves.** No Node runtime, no
   second deployment topology, nothing new that can be down.

Not in the original framing, and decisive in practice: **Astro is Vite-based**,
so it reuses the toolchain this repo already runs — `@tailwindcss/vite` v4 (the
same plugin `apps/web` and `@forge/ui`'s Storybook use), the same tsconfig
idioms, the same `turbo` task graph. Next.js would put a second build system in
the monorepo.

**Revisit trigger.** The day marketing needs per-request personalisation or
incremental static regeneration, Astro's `output: "server"` adapter — or a move
to Next — is back on the table. That is a hosting decision, not a rewrite:
pages, content collections and components survive it.

## Consequences

- **A second component model to hold in your head** alongside the SPA's.
  `.astro` files are not JSX: they render once, on the build machine, and
  props passed to an island must be serialisable (no functions, no context).
  Anyone moving code between `apps/web` and `apps/marketing` has to know which
  side of that line they are on.
- **A smaller ecosystem** for the SEO and analytics integrations Next.js
  treats as first-party. T-302's `<Seo>`, T-303's JSON-LD and T-304's
  `robots.txt` endpoint are ours to write.
- **The island rule, enforced by review:** a page may only hydrate a component
  that cannot be expressed as markup. Pricing toggle, mobile nav, FAQ
  accordion — yes; everything else renders in `.astro`. Without it Astro
  degrades into a slower Next, and T-306's performance budget is what
  eventually notices. `/` is held to **zero script bytes** by a test rather
  than by intent.
- **The design tokens moved.** They lived in `apps/web/src/index.css`, which
  made the SPA their owner; `@forge/ui`'s Storybook already reached across the
  workspace to import that file, and marketing would have been the second
  consumer to do so. They now live in `packages/ui/styles/tokens.css`, and
  every surface imports `@forge/ui/styles/tokens.css`. No token value changed.
  The cost is that `packages/ui` now publishes CSS as well as components, and
  its `files`/`exports` say so.
- **Tailwind v4 does not scan `node_modules`.** A `@forge/ui` component
  mounted as an island renders classes that exist only in the package's built
  output, and without an explicit `@source` they are silently absent — the
  island renders unstyled and nothing errors.
  `apps/marketing/src/styles/global.css` carries that `@source` and the reason
  for it.
- **`apps/marketing/src/pages/island-check.astro` is a diagnostic page, not
  marketing copy.** It exists because `/` must ship no JavaScript while a
  `@forge/ui` component must be proven to hydrate; both hold only if the proof
  lives on a page that is not `/`. T-301's pricing toggle is a real island on
  a real page — delete the diagnostic page then, and move the assertion onto
  that one.
- **The marketing build is gated by `modules.marketing`.** Turning it off
  means `astro build` does not run and `dist/` does not exist, which is why
  `product.config.ts` became a `globalDependencies` entry in `turbo.json`: it
  sits at the repo root, outside every package's inputs, so turbo would
  otherwise replay a cached build from before the toggle flipped.

## Alternatives considered

- **Next.js.** Strongest MDX/content and SEO ecosystem, and the App Router's
  metadata and `sitemap.ts` conventions would have covered T-302–T-304 with no
  bespoke code — the one place it is clearly ahead. It lost on constraint 1:
  RSC-by-default means `@forge/ui` is unusable as it ships, and the two ways
  out are both worse than the problem. Adding `"use client"` to the package
  changes what `apps/web` imports for the benefit of an app `apps/web` does
  not use; a wrapper layer in the marketing app is a second surface to keep in
  step with the first. Constraint 4 is a smaller second strike: `next export`
  yields static files only as long as nothing opts into a server-side feature,
  and the framework will happily let a page do so — a discipline to enforce,
  where Astro's `output: "static"` is a setting.
- **Extending `apps/web` with a public route group.** Shares the most code of
  any option, and fails the requirement that made P4-A exist: a Vite SPA
  cannot pre-render, so the landing page ships an empty root div to every
  crawler. A prerendering plugin bolted onto it would be a third rendering
  model maintained by this repo alone.
- **Keeping the tokens in `apps/web` and importing that file from marketing**,
  the way `@forge/ui`'s Storybook does. Rejected: it would make the SPA the
  owner of the brand for two apps that are not it, and "import the other app's
  CSS entry" is a dependency edge pointing the wrong way. Moving the layer
  into `@forge/ui` cost one import line in `apps/web` and removed the
  question.
