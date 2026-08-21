# `apps/marketing`

The public marketing site: pre-rendered, sharing `@forge/ui`'s design tokens
and `@forge/i18n`'s locale files with the product, and deployed as a directory
of static files. Astro, `output: "static"` — the reasoning is in
[ADR-0004](../../docs/adr/0004-marketing-framework.md), and it is worth reading
before changing anything structural here.

Built by T-300. T-301–T-307 add the content surface, `<Seo>`, JSON-LD, the
sitemap, locale routing, the Lighthouse budget, and the Caddy host.

## Dependencies, and why each one is here

§7 asks for a justification comment on every new dependency, and `package.json`
cannot carry comments — so they live here.

| Dependency                                 | Why                                                                                                                                                                                                                                      |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `astro`                                    | The framework, chosen in ADR-0004. Islands rather than RSC, static output, Vite-based so it reuses this repo's toolchain.                                                                                                                |
| `@astrojs/react`                           | Mounts `@forge/ui`'s React 18 components — as static HTML by default, hydrated only where a page writes a `client:*` directive. Without it the product's component library could not be shared at all, which is constraint 1 of the ADR. |
| `@astrojs/mdx`                             | T-301's blog and guides are MDX. First-party, and pairs with content collections so a frontmatter violation fails the build instead of shipping.                                                                                         |
| `@astrojs/sitemap`                         | T-304's `sitemap.xml`. Present from the start so the routes T-301 adds are indexed as they appear rather than in a later sweep.                                                                                                          |
| `@tailwindcss/vite` + `tailwindcss`        | The same Tailwind v4 plugin `apps/web` and `@forge/ui`'s Storybook run. A different CSS pipeline here would mean the shared token layer is interpreted two ways.                                                                         |
| `react`, `react-dom`                       | Peer dependencies of `@forge/ui`, pinned to the 18.x the rest of the workspace is on.                                                                                                                                                    |
| `@forge/ui`, `@forge/i18n`, `@forge/types` | The shared component library, the locale JSON, and `ProductConfig`'s type.                                                                                                                                                               |
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
committing an asset that would have to be kept in step.

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
```

`PUBLIC_SITE_URL` sets the absolute origin the site is served from (T-304 keys
`robots.txt` and the sitemap off it); `PUBLIC_APP_URL` is where `apps/web`
lives, for the sign-in and sign-up links. Both have documented local defaults,
so a fresh checkout builds without an env file.

## `src/pages/island-check.astro`

A diagnostic page, not marketing copy, and unlinked from the site. It is the
executable form of ADR-0004's constraint 1: a `@forge/ui` component hydrating
in Astro with the package unmodified — no `"use client"` directives added, no
wrapper layer. It exists on its own page because `/` ships no JavaScript.

T-301 lands the pricing toggle, which is a real island on a real page. Delete
this page then and move the assertion in `tests/build.test.ts` onto that one:
what has to survive is the assertion, not the page.
