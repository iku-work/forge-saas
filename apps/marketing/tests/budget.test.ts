/**
 * T-306's build-time half: the three budget killers, and the shape of the
 * Lighthouse job that measures what they cost.
 *
 * The budget itself is asserted by the job, not from here -- a metric needs a
 * browser, three runs and a couple of minutes, which is a CI job and not a
 * unit test. What *is* testable in a second is everything around it: that the
 * cheap gate catches each of the three things it exists to catch, that the
 * pages the job measures are pages that exist, and that the regression
 * fixture is nowhere near a normal build.
 *
 * The gate's failure cases go through fixture directories rather than through
 * a real build, for the reason tests/indexing.test.ts gives for the same
 * shape: a correct build cannot demonstrate a checker that catches incorrect
 * ones. The committed fixture is clean, and each case mutates one file.
 */
import { cpSync, existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import { findBudgetProblems } from "../scripts/check-budget.mjs";
import {
  FIXTURE_ENTRYPOINT,
  FIXTURE_ROUTE,
  fixtureRequested,
} from "../scripts/lighthouse-fixture.mjs";
import { APP_ROOT, DIST, build, builtPages, read, readPath } from "./helpers";

const FIXTURES = join(APP_ROOT, "tests", "fixtures", "budget");

/** LHCI's configs are CommonJS; this file is not. */
const require = createRequire(import.meta.url);
const lighthouserc = require(join(APP_ROOT, "lighthouserc.cjs"));
const fixturerc = require(join(APP_ROOT, "lighthouserc.fixture.cjs"));

/**
 * Every problem the gate reports about the clean fixture with some files
 * replaced, as one string. `undefined` means delete the file.
 */
async function problemsWith(
  mutations: Record<string, string | undefined>,
): Promise<string> {
  const dir = mkdtempSync(join(tmpdir(), "forge-budget-"));
  cpSync(join(FIXTURES, "clean"), dir, { recursive: true });
  for (const [file, contents] of Object.entries(mutations)) {
    if (contents === undefined) {
      rmSync(join(dir, file));
    } else {
      writeFileSync(join(dir, file), contents, "utf-8");
    }
  }
  try {
    return (await findBudgetProblems(dir)).join("\n");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/**
 * The fixture's font preload, which most mutations have to keep.
 *
 * The clean fixture has one HTML file, so a case that rewrites index.html
 * also deletes the only preload on the site -- and every one of those cases
 * would then fail on the webfont rule instead of the rule it is about. Cases
 * that *are* about the preload leave it out explicitly.
 */
const PRELOAD = '<link rel="preload" as="font" href="/fonts/inter.woff2" crossorigin>';

/** A one-page document, for the mutations that rewrite one. */
function page(head: string, body = "<p>Hello.</p>"): string {
  return (
    `<!DOCTYPE html><html lang="en"><head><title>T</title>${PRELOAD}${head}</head>` +
    `<body>${body}</body></html>`
  );
}

describe("check-budget over the real build", () => {
  beforeAll(() => {
    build();
  });

  it("passes, which is the state this site is meant to be in", async () => {
    await expect(findBudgetProblems(DIST)).resolves.toEqual([]);
  });

  it("finds no webfont at all, so there is no face to swap in or preload", () => {
    // The strongest form of "handled up front": the token layer uses the
    // system font stack, so text paints in the first frame, costs zero bytes,
    // and cannot be the thing that delays Largest Contentful Paint. The rules
    // above are for whoever changes that.
    const stylesheets = readdirSync(join(DIST, "_astro")).filter((file) => file.endsWith(".css"));
    expect(stylesheets.length).toBeGreaterThan(0);
    for (const file of stylesheets) {
      expect(readFileSync(join(DIST, "_astro", file), "utf-8")).not.toContain("@font-face");
    }
  });

  it("finds no <img> on any page, so nothing can shift the layout as it loads", () => {
    for (const file of builtPages()) {
      expect(read(file)).not.toMatch(/<img\b/);
    }
  });
});

describe("check-budget's webfont rule", () => {
  it("accepts a self-hosted face with font-display: swap and a preload", async () => {
    await expect(problemsWith({})).resolves.toBe("");
  });

  it("fails a face with no font-display", async () => {
    const problems = await problemsWith({
      "styles.css": "@font-face { font-family: Inter; src: url(/fonts/inter.woff2); }",
    });

    expect(problems).toContain("font-display: swap");
    expect(problems).toContain("Largest Contentful Paint");
  });

  it("fails a face served from another origin", async () => {
    const problems = await problemsWith({
      "styles.css":
        "@font-face { font-family: Inter; font-display: swap; " +
        "src: url(https://fonts.gstatic.com/s/inter.woff2); }",
    });

    expect(problems).toContain("loaded from another origin");
  });

  it("fails a site with webfonts and no preload on any page", async () => {
    const problems = await problemsWith({
      "index.html": "<!DOCTYPE html><html lang=\"en\"><head><title>T</title></head><body></body></html>",
    });

    expect(problems).toContain('no `<link rel="preload" as="font">`');
  });
});

describe("check-budget's image rule", () => {
  it("fails an <img> with no dimensions", async () => {
    const problems = await problemsWith({
      "index.html": page("", '<img src="/hero.png" alt="A hero">'),
    });

    expect(problems).toContain("has no width and no height");
    expect(problems).toContain("astro:assets");
  });

  it("fails an <img> carrying only one of the two", async () => {
    // Half the rule is not half the protection: a width with no height leaves
    // the height unknown, which is the whole of the reflow.
    const problems = await problemsWith({
      "index.html": page("", '<img src="/hero.png" width="800" alt="A hero">'),
    });

    expect(problems).toContain("has no height");
    expect(problems).not.toContain("has no width");
  });

  it("accepts an <img> with both", async () => {
    await expect(
      problemsWith({
        "index.html": page("", '<img src="/hero.png" width="800" height="600" alt="A hero">'),
      }),
    ).resolves.toBe("");
  });
});

describe("check-budget's third-party rule", () => {
  it("fails a script from another origin", async () => {
    const problems = await problemsWith({
      "index.html": page('<script src="https://plausible.io/script.js"></script>'),
    });

    expect(problems).toContain("loads from another origin");
    expect(problems).toContain("ADR-0004, decision 5");
  });

  it("fails a remote stylesheet, a preconnect and an iframe", async () => {
    for (const head of [
      '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">',
      '<link rel="preconnect" href="https://fonts.gstatic.com">',
    ]) {
      await expect(problemsWith({ "index.html": page(head) })).resolves.toContain(
        "loads from another origin",
      );
    }
    await expect(
      problemsWith({
        "index.html": page("", '<iframe src="https://youtube.com/embed/x"></iframe>'),
      }),
    ).resolves.toContain("loads from another origin");
  });

  it("leaves rel=canonical and the hreflang cluster alone", async () => {
    // These name the production origin on every page of a build that
    // Lighthouse serves from localhost, and they are statements rather than
    // fetches. Reporting them would be a gate failing on the whole site,
    // correctly built — which is what the first version of the rule did.
    const problems = await problemsWith({
      "index.html": page(
        '<link rel="canonical" href="https://example.com/en/">' +
          '<link rel="alternate" hreflang="de" href="https://example.com/de/">' +
          '<link rel="alternate" hreflang="x-default" href="https://example.com/en/">',
      ),
    });

    expect(problems).toBe("");
  });

  it("leaves an <a> to another origin alone", async () => {
    // The app is on another origin by design (T-307) and every page links to
    // it. A link is not a subresource.
    await expect(
      problemsWith({
        "index.html": page("", '<a href="https://app.example.com/en/register">Start</a>'),
      }),
    ).resolves.toBe("");
  });
});

/**
 * Heading levels in document order, as numbers.
 *
 * Read off the built HTML rather than the components, because the shape a
 * screen reader gets is the shape of the whole assembled page -- a layout, a
 * page, an island and a footer, each of which only knows its own headings.
 */
function headingLevels(html: string): number[] {
  return [...html.matchAll(/<h([1-6])[\s>]/gi)].map((match) => Number(match[1]));
}

describe("the accessibility score the budget demands", () => {
  beforeAll(() => {
    build();
  });

  // The Lighthouse job asserts `categories:accessibility >= 1`, and this is
  // the audit it failed on when the budget was first run: three plan cards
  // under an <h1> whose titles were <div>s, each containing an <h3>. That is
  // a skipped level, and it cost the pricing page 0.99 -- one hundredth,
  // enough to fail. Checked here as well as there because two seconds over
  // fifty-one pages beats two minutes over four, and because the four the job
  // measures are not the only pages that can break this.
  it("never skips a heading level, on any page", () => {
    for (const file of builtPages()) {
      const levels = headingLevels(read(file));
      let previous = 0;
      for (const level of levels) {
        const skipped = previous === 0 ? level !== 1 : level > previous + 1;
        expect(
          skipped,
          `${file}: heading levels ${levels.join(", ")} — a page starts at <h1> and descends one level at a time`,
        ).toBe(false);
        previous = level;
      }
    }
  });

  it("gives each pricing plan a heading of its own", () => {
    // The fix, stated as the thing it is rather than as "not a skipped
    // level": a plan is a section of the pricing page, so its name is a
    // heading, and the "Includes" list below it is the level under that.
    const html = read("en/pricing/index.html");

    expect(html).toMatch(/<h2[^>]*>Free<\/h2>/);
    expect(html).toMatch(/<h2[^>]*>Pro<\/h2>/);
    expect(html).toMatch(/<h3[^>]*>Includes<\/h3>/);
  });
});

describe("the pages the Lighthouse job measures", () => {
  beforeAll(() => {
    build();
  });

  it("are the four kinds of page this site builds", () => {
    expect(lighthouserc.PAGES).toEqual([
      "/en/",
      "/en/pricing/",
      "/en/blog/why-a-template/",
      "/en/guides/getting-started/installation/",
    ]);
  });

  it("all resolve to a page in dist/", () => {
    // A renamed post would otherwise turn the job red with a 404's scores,
    // which reads like a performance regression and is not one.
    for (const path of lighthouserc.PAGES as string[]) {
      expect(() => readPath(path)).not.toThrow();
    }
  });

  it("are measured three times each, on mobile, and asserted at the median", () => {
    expect(lighthouserc.ci.collect.numberOfRuns).toBe(3);
    expect(lighthouserc.ci.collect.settings.formFactor).toBe("mobile");
    expect(lighthouserc.ci.assert.aggregationMethod).toBe("median");
    expect(lighthouserc.ci.collect.startServerCommand).toContain("http-server");
  });

  it("hold exactly the thresholds T-306 specifies", () => {
    const { assertions } = lighthouserc.ci.assert;

    expect(assertions["categories:performance"][1].minScore).toBe(0.9);
    expect(assertions["categories:accessibility"][1].minScore).toBe(1);
    expect(assertions["categories:best-practices"][1].minScore).toBe(0.9);
    expect(assertions["categories:seo"][1].minScore).toBe(0.95);
    expect(assertions["largest-contentful-paint"][1].maxNumericValue).toBe(2500);
    expect(assertions["cumulative-layout-shift"][1].maxNumericValue).toBe(0.1);
    expect(assertions["total-blocking-time"][1].maxNumericValue).toBe(200);
  });
});

describe("the regression fixture", () => {
  beforeAll(() => {
    build();
  });

  it("is injected only when it is asked for", () => {
    expect(fixtureRequested({})).toBe(false);
    expect(fixtureRequested({ FORGE_LIGHTHOUSE_FIXTURE: "1" })).toBe(true);
  });

  it("contributes no page to dist/", () => {
    expect(existsSync(join(DIST, "lighthouse-fixture"))).toBe(false);
    expect(builtPages().some((file) => file.includes("lighthouse-fixture"))).toBe(false);
  });

  it("contributes no JavaScript to dist/ either", () => {
    // The failure this arrangement replaced: as a route under src/pages/ with
    // an empty getStaticPaths(), it produced no HTML and shipped
    // _astro/UnnecessaryIsland.<hash>.js anyway, because Vite follows a page
    // module's imports rather than the pages it produced.
    const chunks = readdirSync(join(DIST, "_astro"));
    expect(chunks.some((file) => file.includes("UnnecessaryIsland"))).toBe(false);
  });

  it("still exists, and the two configs agree on where it is served", () => {
    expect(existsSync(join(APP_ROOT, FIXTURE_ENTRYPOINT))).toBe(true);
    // The route is written once in ESM and once in CommonJS, because a
    // build-time bridge between the two costs more than one duplicated
    // string. This is what keeps the copies honest.
    expect(fixturerc.FIXTURE_PATH).toBe(`${FIXTURE_ROUTE}/`);
  });

  it("asserts exactly the same budget as the real run", () => {
    // By reference, not by copy: a threshold loosened in lighthouserc.cjs has
    // to reach the fixture too, or the fixture quietly stops failing and the
    // check that it must fail is what turns red.
    expect(fixturerc.ci.assert).toBe(lighthouserc.ci.assert);
  });
});
