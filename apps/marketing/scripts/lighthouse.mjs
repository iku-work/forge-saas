/**
 * The performance budget, in one command (T-306).
 *
 *     pnpm --filter marketing lighthouse             # the four real pages
 *     pnpm --filter marketing lighthouse:fixture     # prove the gate has teeth
 *
 * CI runs exactly these two, in that order, and nothing else -- so a budget
 * that passes locally passes there, and a local run needs no knowledge of
 * which environment variables to set. That is the whole reason this is a
 * script rather than three lines of YAML: the build below has to be a
 * *particular* build, and getting it wrong produces a gate that fails on a
 * site that is fine.
 *
 * ── The build this measures ────────────────────────────────────────────────
 *
 * `PUBLIC_SITE_URL` is set to `PRODUCTION_ORIGIN`, and it has to be. T-304
 * made indexing opt-in: any other origin builds a robots.txt that reads
 * `Disallow: /`, Lighthouse's `is-crawlable` audit reads robots.txt, and the
 * SEO category then scores 0.66 against a 0.95 assertion. Measured here, on
 * the committed config, before this was written down. The alternative --
 * lowering the SEO threshold until a staging build passes -- would be
 * asserting nothing about the site that actually ships.
 *
 * The site is still *served* from localhost, so the canonical, hreflang and
 * Open Graph URLs in the built HTML name an origin the run is not on. That is
 * fine, and was checked rather than assumed: Lighthouse's `canonical` audit
 * does not require the canonical to be the page's own URL, and the SEO
 * category scores 1.0 on all four pages this way. Nothing else in the built
 * output differs between the two origins except robots.txt and the sitemap,
 * neither of which is a performance input -- and both of which have their own
 * gate in scripts/check-indexing.mjs.
 *
 * ── Running this on Windows ────────────────────────────────────────────────
 *
 * chrome-launcher deletes Chrome's temporary profile directory the moment it
 * kills the browser, and against a full Chrome install on Windows that
 * `rmSync` loses a race with something still holding the directory --
 * `EPERM ... AppData\Local\Temp\lighthouse.NNNNNNNN`, on roughly one launch
 * in three, which over twelve launches means never finishing. Point CHROME_PATH
 * at a headless shell instead and it does not happen (12/12 here):
 *
 *     CHROME_PATH=".../ms-playwright/chromium_headless_shell-*\
 *       /chrome-headless-shell-win64/chrome-headless-shell.exe"
 *
 * CI is Linux and unaffected, which is why this is a note rather than a
 * default -- pinning a browser path in the repo would make the gate measure
 * whatever browser happened to be vendored rather than the one Lighthouse
 * chose.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createJiti } from "jiti";

import { readMarketingEnabled } from "./guard-enabled.mjs";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * The origin the site must be built for, read from the file that owns it
 * rather than repeated here. A fork that points `PRODUCTION_ORIGIN` at its
 * real domain changes one line and this keeps working.
 */
async function productionOrigin() {
  const jiti = createJiti(import.meta.url, { interopDefault: true });
  const { PRODUCTION_ORIGIN } = await jiti.import(join(APP_ROOT, "src", "config", "indexing.ts"));
  if (typeof PRODUCTION_ORIGIN !== "string") {
    throw new Error("src/config/indexing.ts: expected a string export named PRODUCTION_ORIGIN");
  }
  return PRODUCTION_ORIGIN;
}

/**
 * `astro build`, through the guard like every other build in this repo.
 *
 * A production build refuses links into an app on localhost
 * (check-app-links.mjs), so the app needs some public origin. Which one does
 * not matter to Lighthouse: app links are never followed or measured.
 */
function build(extraEnv) {
  const env = { PUBLIC_APP_URL: "https://app.example.com", ...process.env, ...extraEnv };
  const result = spawnSync(
    process.execPath,
    [join(APP_ROOT, "scripts", "guard-enabled.mjs"), "astro", "build"],
    { cwd: APP_ROOT, stdio: "inherit", env },
  );
  if (result.status !== 0) {
    throw new Error(`astro build exited ${result.status}`);
  }
}

/**
 * `lhci autorun` against a config, returning its exit status.
 *
 * Resolved through its package.json and spawned with `node`, for the reason
 * guard-enabled.mjs spells out for astro: `node_modules/.bin/lhci` is a .CMD
 * shim on Windows, and reaching it needs `shell: true`, which hands every
 * argument to a shell to reparse.
 */
function autorun(configFile) {
  const require = createRequire(import.meta.url);
  const cli = join(dirname(require.resolve("@lhci/cli/package.json")), "src", "cli.js");
  const result = spawnSync(
    process.execPath,
    [cli, "autorun", `--config=${join(APP_ROOT, configFile)}`],
    { cwd: APP_ROOT, stdio: "inherit", env: process.env },
  );
  if (result.error) {
    throw result.error;
  }
  return result.status ?? 1;
}

/** The real run: build for production, assert the budget on four pages. */
async function runBudget() {
  build({ PUBLIC_SITE_URL: await productionOrigin() });
  return autorun("lighthouserc.cjs");
}

/** Where `lhci assert` records every assertion it checked, passed or failed. */
const ASSERTION_RESULTS = join(APP_ROOT, ".lighthouseci", "assertion-results.json");

/**
 * The assertions the fixture page must be caught by, as the `auditId` and
 * (for a category score) `auditProperty` that file records.
 *
 * Naming them is the point, and "LHCI exited non-zero" is not enough on its
 * own: the fixture page is `noindex` -- it has to be, since T-305's gate wants
 * an hreflang cluster from every indexable page and a one-locale fixture has
 * none -- so `categories.seo` fails on it whatever else is true. A check that
 * only looked at the exit code would therefore stay green with the entire
 * performance budget deleted, which is exactly the failure this fixture
 * exists to catch.
 */
const REQUIRED_FIXTURE_FAILURES = [
  { auditId: "total-blocking-time" },
  { auditId: "categories", auditProperty: "performance" },
];

function describeAssertion({ auditId, auditProperty }) {
  return auditProperty === undefined ? auditId : `${auditId}.${auditProperty}`;
}

/**
 * The regression fixture: the same budget, against a page built to fail it.
 *
 * Inverted on purpose. If LHCI finds nothing wrong with a page that hydrates
 * an island for no reason and blocks the main thread for over a second, the
 * assertions are not being applied -- a preset that overrode them, an
 * `upload` step that swallowed the failure, a threshold quietly raised. That
 * is a broken gate, and a broken gate should be red.
 */
async function runFixture() {
  // A stale file from an earlier run would let this pass without LHCI having
  // asserted anything at all.
  rmSync(ASSERTION_RESULTS, { force: true });

  build({
    PUBLIC_SITE_URL: await productionOrigin(),
    FORGE_LIGHTHOUSE_FIXTURE: "1",
  });

  const status = autorun("lighthouserc.fixture.cjs");

  if (!existsSync(ASSERTION_RESULTS)) {
    console.error(
      [
        "",
        `lighthouse --fixture: lhci exited ${status} without writing ${ASSERTION_RESULTS}.`,
        "Nothing was asserted, so nothing was proved — the run failed before the assert step.",
      ].join("\n"),
    );
    return 1;
  }

  const failures = JSON.parse(readFileSync(ASSERTION_RESULTS, "utf-8")).filter(
    (result) => result.passed === false,
  );
  const missed = REQUIRED_FIXTURE_FAILURES.filter(
    (required) =>
      !failures.some(
        (failure) =>
          failure.auditId === required.auditId &&
          (required.auditProperty === undefined ||
            failure.auditProperty === required.auditProperty),
      ),
  );

  if (missed.length > 0) {
    console.error(
      [
        "",
        "lighthouse --fixture: tests/fixtures/lighthouse/unnecessary-island.astro hydrates an",
        "island that blocks the main thread for over a second, and the budget did not catch it on:",
        ...missed.map((required) => `  ${describeAssertion(required)}`),
        "",
        "The assertions in lighthouserc.cjs are not being applied. Check that",
        "`assert.assertions` still holds total-blocking-time and the category scores, and that",
        "lighthouserc.fixture.cjs still reuses `base.ci.assert` by reference.",
      ].join("\n"),
    );
    return 1;
  }

  console.log(
    `\nlighthouse --fixture: the fixture page was caught by ` +
      `${REQUIRED_FIXTURE_FAILURES.map(describeAssertion).join(" and ")} — the gate has teeth.`,
  );
  return 0;
}

async function main() {
  const fixture = process.argv.slice(2).includes("--fixture");

  // The same toggle `astro build` honours, checked before anything is spawned
  // -- a product with `modules.marketing: false` has no site to measure, and
  // the honest outcome is a green no-op rather than a Lighthouse run against
  // an empty directory. CI skips this job outright for the same reason; this
  // is what makes a local `pnpm lighthouse` agree with it.
  if (!(await readMarketingEnabled())) {
    console.log("marketing is disabled in product.config.ts — nothing to measure");
    return 0;
  }

  return fixture ? runFixture() : runBudget();
}

process.exit(await main());
