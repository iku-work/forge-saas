/**
 * The same budget, pointed at a page built to fail it (T-306).
 *
 * "The job is the test" is true right up until the job goes green because
 * something quietly stopped asserting -- a preset that overrode the
 * thresholds, an `upload` misconfiguration that swallowed the exit code, a
 * `numberOfRuns` of one on a quiet runner. A gate with no demonstrated
 * failure is a gate nobody has checked.
 *
 * So `scripts/lighthouse.mjs --fixture` rebuilds the site with one extra page
 * -- `src/pages/lighthouse-fixture/[case].astro`, which hydrates an island
 * that has no business being one -- runs *this* config against it, and fails
 * if LHCI passes. See that page for why its cost is exaggerated rather than
 * merely realistic.
 *
 * Everything except the URL comes from lighthouserc.cjs by reference, which
 * is the point: a threshold loosened there is loosened here too, and the
 * fixture stops failing, and the check that the fixture must fail turns red.
 */
const base = require("./lighthouserc.cjs");

/** The route `[case].astro` builds when FORGE_LIGHTHOUSE_FIXTURE is set. */
const FIXTURE_PATH = "/lighthouse-fixture/unnecessary-island/";

module.exports = {
  ci: {
    ...base.ci,
    collect: {
      ...base.ci.collect,
      url: [`http://localhost:${base.PORT}${FIXTURE_PATH}`],
      // One run. Three would be three chances to be slow, and this page is
      // not slow by accident -- it is slow by construction, on any machine.
      numberOfRuns: 1,
    },
    // Identical assertions, by reference rather than by copy.
    assert: base.ci.assert,
    upload: {
      ...base.ci.upload,
      outputDir: "./.lighthouseci/fixture-reports",
    },
  },

  FIXTURE_PATH,
};
