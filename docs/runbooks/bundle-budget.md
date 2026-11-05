# Bundle budget runbook (T-242)

## Trigger

The `bundle-budget` CI job is red, or `pnpm --filter web bundle:check` fails on your
machine. It prints what is over, for example:

```text
Bundle budget failed:
  initial JS: 254202 is over 180113
```

Sizes are gzipped bytes.

## Pre-checks

- Read the failure. There are three kinds:
  - **`is over`:** something grew past its budget.
  - **`no budget`:** a route or lazy import is new. Go to step 4.
  - **`budget for something no longer built`:** a route was removed. Go to step 4.
- Get the treemap: download `bundle-stats` from the failed CI run and open
  `stats.html`. Locally it is at `apps/web/dist/bundle-check/stats.html`.

## Steps

1. **Find what grew.** In the treemap, look for a package or file your PR added, and
   for anything that shouldn't load on this route.
2. **Reduce it, if you can.** In this order:
   - Lazy-load it with `import()` or a route of its own. `initial JS` is what every
     visitor pays for, including on the login page.
   - Narrow the import: `import { x } from "lib/x"` rather than the whole package.
   - Replace the dependency with something smaller or a few lines of your own.

   ```bash
   pnpm --filter web bundle:check
   ```

3. **If the growth is worth it, raise that one number.** Edit
   `apps/web/bundle-budgets.json` by hand, and say in the PR what the extra size buys.
   The reviewer should agree to it.
4. **For a new or removed route, or a reset after real changes,** rewrite every budget
   to today's size + 10%:

   ```bash
   pnpm --filter web bundle:check --update
   ```

   Read the diff. This also raises any number that was close to its limit.

## Verification

- `pnpm --filter web bundle:check` exits 0.
- The `bundle-budget` job is green on the PR.

## Rollback

Revert the commit that raised the budget, along with the code that needed it. The
check only reads a file, so nothing deployed depends on it.

## Escalation

If a change can't fit and the reviewer doesn't accept the size, it is a product
decision about what the page should load, so take it to whoever owns that page.
