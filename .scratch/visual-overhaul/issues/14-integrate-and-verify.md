# 14: Integrate and verify (screenshots, accessibility, full suites)

**What to build:** the branch is ready for review: the visual screenshot baselines are
regenerated and pass, the accessibility gate passes in both themes, and every lint,
type-check and test suite is green.

**Blocked by:** 01–13.

**Status:** done

## Read first

1. `apps/web/e2e/visual.spec.ts` (what is screenshotted: dashboard, settings → org,
   settings → profile; desktop + mobile; plus `ar` desktop for dashboard and profile; 16 PNGs).
2. `apps/web/e2e/playwright.config.ts` (the Playwright version is in `apps/web/package.json`,
   `@playwright/test`).

## Steps

1. **Full static and unit checks** from the worktree root:
   ```bash
   pnpm install
   pnpm --filter @forge/i18n build && pnpm --filter @forge/ui build
   pnpm lint && pnpm type-check && pnpm test
   pnpm --filter @forge/ui storybook:build
   ```
   Fix anything red. Note: the dashboard's visual screenshot will now show the Getting
   started checklist, which is intended.

2. **Regenerate the Linux visual baselines with Docker** (works from Windows; bash, not PowerShell):
   1. Back up `backend/.env` if it exists, then `cp backend/.env.e2e backend/.env`.
   2. `export VITE_API_URL=""` then `pnpm build`. Confirm `grep -o localhost:8000 apps/web/dist/assets/*.js` prints nothing.
   3. `docker compose up -d postgres redis minio minio-init mailhog backend worker caddy`,
      then `docker compose exec -T backend python -m app.core.migrations` and
      `docker compose exec -T backend python -m app.core.seed`.
   4. Run the Playwright image whose tag matches `@playwright/test` (e.g.
      `mcr.microsoft.com/playwright:v<version>-noble`) on network `saas-template_forge-net`
      (check the real name with `docker network ls`) with `-e E2E_BASE_URL=https://caddy`.
      Mount **only** `apps/web/e2e` read-only and the root `tsconfig.json`, plus an empty
      host folder as `/out`. Inside: copy them to a scratch dir, `npm install -D
      @playwright/test@<ver> @axe-core/playwright@<ver>`, then run
      `npx playwright test visual.spec.ts --update-snapshots=all` (the bare
      `--update-snapshots` flag silently does nothing), copy the new PNGs to `/out`.
      Never mount the host `node_modules`. In bash on Windows prefix `MSYS_NO_PATHCONV=1`.
   5. Copy the PNGs from `/out` into `apps/web/e2e/__screenshots__/`, then run the visual
      spec again **without** the update flag and confirm it passes.
   6. Restore `backend/.env`. Stop the stack: `docker compose down`.

   Open every new PNG and look at it: sidebar without border, raised panel, Iris accent,
   Instrument Sans, `ar` mirrored. A wrong-looking baseline is a bug, not a pass.

3. **Accessibility gate:** run `apps/web/e2e/a11y.spec.ts`. It registers a user through
   email verification, which the container recipe doesn't support. If you can't run it
   locally, run the in-process check instead (`pnpm --filter web exec vitest run src/features/auth/a11y.test.tsx`)
   and the Storybook a11y pass (`pnpm storybook:test` after `storybook:build`), and write
   in the Notes that the full Playwright a11y spec needs CI.
   Also run `touch-targets.spec.ts` and `mobile-layout.spec.ts` with the same container
   recipe if they don't need email (check their setup), otherwise leave to CI.

4. **Module matrix spot check:** run the web tests with file storage and billing off, if
   the repo has a documented way (search `module_config` or `MODULE_SURFACE` in `apps/web`
   and the memory notes in `docs/`); at minimum the per-module test files from ticket 10 cover it.

5. **Final review:** `git diff main --stat`. Skim for stray debug code, `console.log`,
   hardcoded colours (`grep -rnE '#[0-9a-fA-F]{6}' apps/web/src packages/ui/src --include=*.tsx`),
   raw English in JSX (lint catches it), and leftover `Card` usage on converted pages.

6. Update `docs/adr/0008-design-language-v2.md` only if something decided there changed
   during the build (write it in its Consequences section). Mark this ticket done.

## Acceptance

- [x] `pnpm lint && pnpm type-check && pnpm test` green; Storybook builds.
- [x] 16 visual baselines regenerated, reviewed by eye, and passing without the update flag.
- [x] Accessibility checks pass (or the CI-only remainder is written in Notes).
- [x] No hardcoded colours or raw strings; diff reviewed.

## Don't

- Don't push or open a PR unless the user asks. Report what's done and what needs CI.

## Notes

Completed 2026-10-09; see `PROGRESS.md`. The full Playwright a11y spec *was* run locally (the
container joins Caddy's network namespace so the emailed `https://localhost` link works), in
both themes and `ar`, on chromium and webkit. Still for CI: Lighthouse, and the module-matrix
legs.
