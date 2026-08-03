# Bundle budget gate failed (T-242)

What to do when the CI job **`bundle-budget`** goes red. The gate exists so
that JS/CSS growth in `apps/web` is a *reviewed* decision made in the PR that
causes it — not something discovered in a Lighthouse report six months later.

The job builds the SPA the same way the `build` job does, then runs
[`size-limit`](https://github.com/ai/size-limit) against the committed
per-chunk budgets in [`apps/web/package.json`](../../apps/web/package.json)
(the top-level `"size-limit"` array). All sizes are **gzipped**, matching how
Caddy serves the assets.

## The two ways it fails

### 1. A chunk exceeded its budget

`size-limit` prints a per-chunk table and fails on the first overage, e.g.:

```
route — settings/billing
Package size limit has exceeded by 2.4 kB
Size limit: 2.3 kB
Size:       4.7 kB gzipped
```

That chunk grew. **This is not automatically a problem to "fix" by raising the
number** — the point of the gate is to make you decide, on purpose, which of
these it is:

- **Accidental growth** — someone pulled a heavy dependency into a route, or
  imported a whole library for one helper. Fix the code (see below), don't
  raise the budget.
- **Deliberate, justified growth** — the feature genuinely needs the bytes.
  Raise the budget in `apps/web/package.json` **in the same PR**, so a
  reviewer signs off on the new number. That committed edit *is* the review.

### 2. A chunk has no budget

The `Enforce every chunk has a budget` step fails first, listing built chunks
that no `"size-limit"` glob matches:

```
Built chunks with no size-limit budget (add one to apps/web/package.json's "size-limit"):
  - dist/assets/_locale._app.reports.exports-abc12345.js
```

This happens when you add a new route (TanStack Router auto-code-splits each
route into its own chunk) or a new i18n namespace. Add a budget entry for it —
size it from the current build plus ~10% headroom (see "Setting a budget"
below). Growth can't slip in under an unmeasured filename.

## Diagnosing *why* a chunk grew

Every CI run of this job uploads a **`bundle-stats`** artifact —
`dist/stats.html`, a `rollup-plugin-visualizer` treemap. Download it from the
failed run's *Artifacts* section and open it in a browser: box area is gzipped
size, so the thing that grew is the thing that got bigger. No need to
reproduce the build locally.

To reproduce locally anyway:

```bash
pnpm build               # or: ANALYZE=true pnpm build  (also writes dist/stats.html)
pnpm --filter web size   # the same table CI prints
open apps/web/dist/stats.html
```

> `ANALYZE=true pnpm build` is the analyze build; `pnpm --filter web build:analyze`
> is the same thing scoped to the web app. A plain `pnpm build` does **not**
> emit `stats.html` (the visualizer only runs under `ANALYZE`), but the budgets
> are still checkable against its output.

## Fixing accidental growth

In rough order of preference:

1. **Lazy-load it.** If the heavy code is only needed on interaction (a modal,
   an export, a charting view), `import()` it at the call site so it lands in
   its own chunk instead of the route's initial payload. The treemap shows
   what's eagerly pulled into the entry/route chunk.
2. **Import narrowly.** Pull the one function you need, not the namespace —
   `import { debounce } from "lodash-es"` (or a single-purpose dep), never
   `import * as _ from "lodash"`. `lucide-react` icons are already per-icon
   imports; keep them that way (an `import * as icons` is exactly what this
   gate catches — verified once by doing precisely that).
3. **Replace the dependency** with something smaller, or with code you own if
   it's a few lines.
4. **Move shared code into a shared chunk** so N routes don't each inline it.

## Setting (or raising) a budget

Budgets are gzipped sizes with clean units (`"150 KB"`, `"5.2 KB"`, `"800 B"`).
For a **new** chunk, size it from the current build + ~10% headroom:

```bash
pnpm build
gzip -c apps/web/dist/assets/<chunk>-*.js | wc -c   # bytes; ×1.1, round up
```

When **raising** an existing budget, say why in the PR description (or a commit
message) — "billing route adds the invoice PDF preview, +2.4 KB, lazy-loading
it would regress the primary flow" is a reviewable claim; a bare number bump is
not. The entry chunk (initial JS the login page loads) is deliberately held
near ~150 KB — treat raising *that* one as a bigger deal than a leaf route.

## What each budget guards

- **`entry`** — the single JS file `index.html` loads on every visit
  (`entry-*.js`). This is the number that gates first paint; it's renamed off
  the default `index-*` precisely so this budget can target it unambiguously.
- **`vendor`** — the shared async chunk (`sonner` + `lucide-react`) most
  authenticated routes pull in.
- **`CSS`** — the single Tailwind stylesheet, total.
- **`route — …`** — one entry per lazy route chunk.
- **`i18n — …`** — one entry per translation namespace, summed across all
  locales (so adding a locale, which grows every namespace, is itself a
  reviewed bump — as it should be).
- **`shared — …`** — small cross-route runtime helpers.

## See also

- [`apps/web/vite.config.ts`](../../apps/web/vite.config.ts) — entry renaming
  and the `ANALYZE` visualizer wiring.
- The `bundle-budget` job in
  [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
