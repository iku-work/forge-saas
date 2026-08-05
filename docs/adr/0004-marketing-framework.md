# ADR-0004: Marketing site framework

## Status

Proposed — **placeholder.** The decision belongs to
[T-300](../../agents.md) and has not been made. This file exists so the
constraints that will decide it are written down now, while they are visible,
rather than reconstructed under deadline by whoever picks up P4-A. Nothing in
the repo depends on it yet: `apps/marketing` does not exist.

**Date:** 2026-08-05

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
already fixed by the repo as it stands today, and they are the substance of
the choice:

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

**Deferred to T-300.** No framework is chosen here, and no code should assume
one.

What this ADR does decide is how the choice gets made: T-300 replaces this
file's Status with `Accepted`, names the framework, and answers each of the
four constraints above concretely — in particular, *what happened to the
`"use client"` boundary*, since that answer determines whether `@forge/ui`
gains directives (affecting `apps/web` too) or the marketing app wraps it.

## Consequences

- Until T-300 lands, `apps/marketing` does not exist and no marketing route
  ships. P4-A's remaining tasks (T-301–T-306) are all blocked on it, which is
  already recorded in their `needs:` fields.
- Writing this placeholder costs a stale-document risk: the constraints above
  are true as of 2026-08-05 and will decay. The dated, `Proposed` status is
  the mitigation — a reader who finds this still `Proposed` a year from now
  should re-derive constraint 1 from `packages/ui` before trusting it.
- Deciding this late is deliberate, not an oversight. The choice depends on
  what `@forge/ui` actually looks like when the marketing site is built, and
  that package is still growing.

## Alternatives considered

Not alternatives to the decision — there is no decision yet — but the two
candidates T-300 names, with what is already known about each:

- **Next.js.** Strongest MDX/content and SEO ecosystem, and the App Router's
  metadata and `sitemap.ts` conventions cover T-302–T-304 with no bespoke
  code. Costs: RSC-by-default collides with constraint 1, and `next export`
  gives back static files only if nothing in the app uses a server-side
  feature — a discipline that has to be enforced, since the framework will
  happily let a page opt into needing a Node runtime.
- **Astro.** Islands architecture is the natural fit for constraint 1: pages
  are static HTML by default and a `@forge/ui` React component is mounted as
  an explicit island with `client:load`, so the "everything is a client
  component" property of the package stops being an obstacle and becomes the
  default assumption. Content collections cover constraint 3 with typed
  frontmatter, and the output is static files with no runtime (constraint 4).
  Costs: a second component model to hold in your head alongside the SPA's,
  and a smaller ecosystem for the SEO/analytics integrations that Next.js
  treats as first-party.
- **Extending `apps/web` with a public route group.** Rejected in T-300's own
  framing and worth recording as rejected: it shares the most code of any
  option and fails the requirement that made P4-A exist, since a Vite SPA
  cannot pre-render. A prerendering plugin bolted onto it would be a third
  rendering model maintained by this repo alone.
