/**
 * The one route that only exists while T-306's regression fixture is running.
 *
 * `scripts/lighthouse.mjs --fixture` sets FORGE_LIGHTHOUSE_FIXTURE=1 and
 * rebuilds; this integration then injects a page that hydrates an island for
 * no reason, and lighthouserc.fixture.cjs asserts the ordinary budget against
 * it and is expected to fail. Every other build -- `pnpm build`, every build
 * the test suite runs, CI's -- gets an empty array here and is byte-identical
 * to what it was before this file existed.
 *
 * ── Why an injected route ──────────────────────────────────────────────────
 *
 * Two other shapes were tried first, and both shipped something:
 *
 *   - A page under `src/pages/` with `getStaticPaths()` returning `[]`. No
 *     HTML, as intended -- and `dist/_astro/UnnecessaryIsland.<hash>.js`
 *     anyway, because Vite follows the imports of a page module rather than
 *     the pages it produced. A fixture that ships a chunk to production is a
 *     worse bug than the one it exists to catch.
 *   - Copying the fixture into `src/pages/` for the run and deleting it
 *     after. Correct when it completes; a Ctrl-C between the two steps leaves
 *     a fixture page in the source tree waiting to be committed.
 *
 * An injected route has neither failure mode: the entrypoint lives under
 * `tests/fixtures/`, where nothing imports it, and the injection is a
 * function of an environment variable rather than of the state of the working
 * tree.
 */
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * The route the fixture is served at.
 *
 * Shared with lighthouserc.fixture.cjs by being written in both places rather
 * than imported -- that file is CommonJS and this one is ESM, and a build-time
 * bridge between the two costs more than one duplicated string. The test in
 * tests/lighthouse.test.ts asserts they still agree.
 */
export const FIXTURE_ROUTE = "/lighthouse-fixture/unnecessary-island";

/** The `.astro` file behind it, relative to this app's root. */
export const FIXTURE_ENTRYPOINT = "./tests/fixtures/lighthouse/unnecessary-island.astro";

/** Is this build the fixture build? */
export function fixtureRequested(env = process.env) {
  return env["FORGE_LIGHTHOUSE_FIXTURE"] === "1";
}

/**
 * Spread into `integrations` in astro.config.mjs. Empty unless asked for.
 */
export function lighthouseFixture() {
  if (!fixtureRequested()) {
    return [];
  }

  return [
    {
      name: "forge:lighthouse-fixture",
      hooks: {
        "astro:config:setup": ({ injectRoute, logger }) => {
          // A missing entrypoint would otherwise surface as a confusing
          // build error about a route that resolves to nothing -- and the
          // only way to get here is to have deleted the fixture, which is a
          // thing to say plainly.
          if (!existsSync(resolve(APP_ROOT, FIXTURE_ENTRYPOINT))) {
            throw new Error(
              `FORGE_LIGHTHOUSE_FIXTURE=1 but ${FIXTURE_ENTRYPOINT} does not exist — ` +
                "T-306's regression fixture has been deleted, and `pnpm lighthouse:fixture` " +
                "can no longer prove the budget is being asserted",
            );
          }
          injectRoute({ pattern: FIXTURE_ROUTE, entrypoint: FIXTURE_ENTRYPOINT });
          logger.warn(
            `injected ${FIXTURE_ROUTE} — this build is T-306's regression fixture and must not be deployed`,
          );
        },
      },
    },
  ];
}
