/**
 * The performance budget, enforced by CI rather than by a screenshot (T-306).
 *
 * `scripts/lighthouse.mjs` is what runs this -- it builds the site the way a
 * production deploy would and then hands `lhci autorun` this file. Read that
 * script before changing anything here; the two halves that matter (which
 * origin the site is built for, and which origin it is served from) live
 * there, and getting them out of step is how this gate starts failing on a
 * site that is fine.
 *
 * CommonJS, and `.cjs` rather than `.js`, because package.json says
 * `"type": "module"` for this app: LHCI `require()`s its config, and a `.js`
 * file here would be ESM. The script passes `--config` explicitly, so the
 * extension never has to be one LHCI auto-discovers.
 */

/**
 * The port the built site is served on for the duration of a run.
 *
 * Not 4321: that is `astro dev`, and a developer running the dev server and
 * this gate at the same time should get two servers, not one confusing one.
 */
const PORT = 4322;

const ORIGIN = `http://localhost:${PORT}`;

/**
 * The four pages, one of each kind this site builds.
 *
 * Real URLs, not representative ones -- a budget asserted against a fixture
 * page is a budget nobody ships. `/en/pricing/` is here because it is the one
 * page that hydrates an island (T-301's billing-period toggle), and it is
 * therefore the page most likely to be the first to fail; the blog post and
 * the guide are here because MDX, the typography plugin and Shiki's syntax
 * highlighting all reach a visitor through them and through nothing else.
 *
 * Every path carries its locale segment, because since T-305 every URL does.
 * `/` is a locale chooser that redirects, and measuring it would measure the
 * redirect rather than the landing page.
 *
 * `tests/lighthouse.test.ts` fails the build if one of these stops resolving
 * to a built page -- a renamed post would otherwise turn this job red with a
 * 404's scores, which reads like a performance regression and is not one.
 */
const PAGES = [
  "/en/",
  "/en/pricing/",
  "/en/blog/why-a-template/",
  "/en/guides/getting-started/installation/",
];

module.exports = {
  ci: {
    collect: {
      // `http-server` over the built directory: no Docker, no backend, no
      // database. The site is a directory of static files, which is the whole
      // argument ADR-0004 made for Astro, and it is why this job is a couple
      // of minutes rather than the ten the E2E stack takes.
      //
      // `--silent` is deliberately *not* passed: LHCI waits for the server to
      // announce itself on stdout, and a silent server never does.
      startServerCommand: `pnpm exec http-server dist --port ${PORT}`,
      startServerReadyPattern: "Available on",
      url: PAGES.map((page) => `${ORIGIN}${page}`),

      // Three runs per page. Lighthouse's metrics are timings taken on a
      // shared CI runner, and a single run of anything timed on a shared
      // runner is a coin flip; the assertions below are checked against the
      // median of the three.
      numberOfRuns: 3,

      settings: {
        // Mobile, which is Lighthouse's default -- there is no
        // `--preset=mobile`, only `--preset=desktop`, so the way to be
        // explicit about wanting the harder of the two is to say so here and
        // leave the emulation and throttling profiles at their defaults.
        // Anyone who adds `preset: "desktop"` below is weakening every
        // threshold in this file at once, which is exactly the edit this line
        // is here to make visible in a diff.
        formFactor: "mobile",
      },
    },

    assert: {
      // The median of the three runs, per assertion. LHCI's default is
      // optimistic -- it takes the *best* run -- which for a budget means the
      // gate passes on the run that happened to get a quiet runner.
      aggregationMethod: "median",

      // Nothing is inherited from an LHCI preset: every threshold this job
      // enforces is written out below, so the budget can be read off one
      // screen and changing it is always a visible edit.
      assertions: {
        "categories:performance": ["error", { minScore: 0.9 }],

        // The same bar T-221 holds apps/web to, for the same reason: a
        // marketing site is where a customer meets this product first, and
        // "the app is accessible, the site it is advertised on is not" is not
        // a position anybody defends on purpose. 1.0 is reachable here --
        // this site is static markup -- and it is not a stretch goal: it is
        // where the four pages already are.
        "categories:accessibility": ["error", { minScore: 1 }],

        "categories:best-practices": ["error", { minScore: 0.9 }],

        // 0.95 rather than 1.0 because a handful of SEO audits are about
        // things a template cannot decide for a product (whether the copy on
        // a page deserves to rank), while the ones this repo *does* decide --
        // the title, the description, the canonical, the robots directive,
        // hreflang -- already have their own build gates in scripts/.
        "categories:seo": ["error", { minScore: 0.95 }],

        "largest-contentful-paint": ["error", { maxNumericValue: 2500 }],
        "cumulative-layout-shift": ["error", { maxNumericValue: 0.1 }],

        // ── INP is not asserted here, and cannot be ──────────────────────
        //
        // The plan asks for "INP < 200 ms". Interaction to Next Paint is a
        // *field* metric: it is the latency of interactions real people
        // actually performed, collected from real sessions. A Lighthouse lab
        // run performs no interactions, so it has no INP to report -- what it
        // reports instead is Total Blocking Time, the amount of main-thread
        // blocking after first paint, which is the lab proxy for "would an
        // interaction have been slow if someone had tried one".
        //
        // So the assertion below is TBT < 200 ms, and the number is the same
        // number for a reason: it is the threshold that keeps a page's long
        // tasks short enough that an interaction lands inside INP's own
        // 200 ms "good" bucket.
        //
        // **Do not "fix" this discrepancy by relaxing something.** If you came
        // here because the plan says INP and the config says TBT, the config
        // is right and the plan's metric cannot be measured in CI. Real INP
        // needs CrUX or a RUM script on the production site -- a post-launch
        // measurement, and (per T-308) a third-party script decision with its
        // own ADR. It is not a gate this or any lab job can hold.
        "total-blocking-time": ["error", { maxNumericValue: 200 }],
      },
    },

    upload: {
      // No LHCI server to talk to, so the reports land on disk and CI uploads
      // them as an artifact. A failed budget without the report that failed
      // it is a red job nobody can act on.
      target: "filesystem",
      outputDir: "./.lighthouseci/reports",
      reportFilenamePattern: "%%PATHNAME%%.report.%%EXTENSION%%",
    },
  },

  // Read by tests/lighthouse.test.ts and by lighthouserc.fixture.cjs. Not part
  // of the `ci` key, so LHCI ignores it.
  PAGES,
  PORT,
};
