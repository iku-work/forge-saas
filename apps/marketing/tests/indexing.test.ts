/**
 * T-304: what this site offers a crawler, and what it refuses to.
 *
 * Three claims, and they need three different kinds of test.
 *
 * The *shape* of the output — who is allowed where, which URLs are listed —
 * is asserted over a real `dist/`, like everything else here. The
 * production/staging switch needs two builds, because `PUBLIC_SITE_URL` is
 * the input and there is no way to observe a decision that was never taken.
 *
 * The *gate* is asserted over fixture directories instead. `check-indexing`
 * exists to catch a `dist/` that is wrong, and a build that is right cannot
 * demonstrate that: the only page here that opts out of indexing is the 404,
 * which `@astrojs/sitemap` excludes by its own status-code rule anyway. So
 * the sitemap filter gets a fixture page of its own, and the checker gets a
 * clean fixture directory with one thing broken in it at a time.
 */
import { cpSync, existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  blocksEverything,
  findIndexingProblems,
  isAllowedByRobots,
  parseRobots,
} from "../scripts/check-indexing.mjs";
import { PRODUCTION_ORIGIN, SITEMAP_PATH } from "../src/config/indexing";
import {
  APP_ROOT,
  DIST,
  build,
  builtPages,
  read,
  sitemapPaths,
} from "./helpers";

/** A host that is emphatically not `PRODUCTION_ORIGIN`, in the way a staging deploy is not. */
const STAGING_URL = "https://staging.forge.example";

/**
 * A page that asks not to be indexed, added for one build.
 *
 * The 404 cannot stand in for it: `@astrojs/sitemap` drops `404` and `500` on
 * its own, before any filter of ours runs, so a test built on that page would
 * pass with the filter deleted. This one is an ordinary route in every
 * respect except the prop, which is exactly the case the filter is for.
 *
 * Not underscore-prefixed — Astro reads a leading `_` in `src/pages/` as "not
 * a route" and would build nothing at all, which is a very convincing way for
 * a test like this to pass (T-302 found that one the hard way).
 */
const FIXTURE_PAGE = join(APP_ROOT, "src", "pages", "indexing-fixture-page.astro");
const FIXTURE_PATH = "/indexing-fixture-page/";

const FIXTURE_PAGE_SOURCE = `---
// Written by tests/indexing.test.ts, and removed again when it finishes.
import BaseLayout from "../layouts/BaseLayout.astro";
---

<BaseLayout
  title="Indexing fixture"
  description="A page that asks not to be indexed, so the sitemap filter has something to drop."
  noindex={true}
>
  <p>This page exists for the length of one test run.</p>
</BaseLayout>
`;

/** The clean fixture, and a copy of it with individual files rewritten or removed. */
const FIXTURES = join(APP_ROOT, "tests", "fixtures", "indexing");
const FIXTURE_ORIGIN = new URL("https://example.com");

/**
 * The clean fixture with some files replaced, and `undefined` meaning delete.
 *
 * One committed directory plus a visible mutation beats eight committed
 * directories that differ from each other in a way a reader has to diff for.
 */
function withMutations(mutations: Record<string, string | undefined>): string {
  const dir = mkdtempSync(join(tmpdir(), "forge-indexing-"));
  cpSync(join(FIXTURES, "clean"), dir, { recursive: true });
  for (const [file, contents] of Object.entries(mutations)) {
    if (contents === undefined) {
      rmSync(join(dir, file));
    } else {
      writeFileSync(join(dir, file), contents, "utf-8");
    }
  }
  return dir;
}

/** Every problem the checker reports about a mutated fixture, as one string. */
async function problemsWith(
  mutations: Record<string, string | undefined>,
): Promise<string> {
  const dir = withMutations(mutations);
  try {
    return (await findIndexingProblems(dir, FIXTURE_ORIGIN)).join("\n");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9";

/** A sitemap chunk listing exactly these paths, for the mutations that rewrite one. */
function sitemapChunk(...paths: string[]): string {
  const urls = paths
    .map((path) => `<url><loc>${new URL(path, FIXTURE_ORIGIN).href}</loc></url>`)
    .join("");
  return (
    '<?xml version="1.0" encoding="UTF-8"?>' +
    `<urlset xmlns="${SITEMAP_NAMESPACE}">${urls}</urlset>`
  );
}

describe("the production site", () => {
  beforeAll(() => {
    writeFileSync(FIXTURE_PAGE, FIXTURE_PAGE_SOURCE, "utf-8");
    build({ env: { PUBLIC_SITE_URL: PRODUCTION_ORIGIN } });
  });

  afterAll(() => {
    rmSync(FIXTURE_PAGE, { force: true });
    build();
  });

  it("invites crawlers, and points them at a sitemap that exists", () => {
    const robots = parseRobots(read("robots.txt"));

    expect(blocksEverything(robots)).toBe(false);
    // Absolute, and on this build's own origin: a `Sitemap:` line is the one
    // place in robots.txt where a relative path is not merely discouraged but
    // ignored outright.
    expect(robots.sitemaps).toEqual([`${PRODUCTION_ORIGIN}${SITEMAP_PATH}`]);
    expect(existsSync(join(DIST, SITEMAP_PATH.slice(1)))).toBe(true);
  });

  it("keeps the API and the app's private paths out of reach", () => {
    const robots = parseRobots(read("robots.txt"));

    // The API's own surface, and the two apps/web paths whose appearance in a
    // search result would be an actual incident.
    expect(isAllowedByRobots("/api/v1/billing/plans", robots)).toBe(false);
    expect(isAllowedByRobots("/en/reset-password", robots)).toBe(false);
    expect(isAllowedByRobots("/fi/admin/users", robots)).toBe(false);

    // And the prefix that is a rule rather than a trap: /api is blocked,
    // a post about an API is not.
    expect(isAllowedByRobots("/blog/api-changes-in-v2/", robots)).toBe(true);
  });

  it("disallows nothing it actually built", () => {
    const robots = parseRobots(read("robots.txt"));

    // The rules above are written for paths this host does not serve, so the
    // risk they carry is the one this asserts away: a pattern quietly
    // matching a marketing page. The build gate checks the sitemap; this
    // covers every built page, listed or not.
    for (const page of builtPages()) {
      const path = page === "index.html" ? "/" : `/${page.replace(/index\.html$/, "")}`;
      expect(isAllowedByRobots(path, robots), `${path} is disallowed`).toBe(true);
    }
  });

  it("lists every page it built and nothing it did not", async () => {
    // The checker, run over dist/ from outside the build. It runs *inside*
    // every build as an integration, so this is what catches somebody
    // unwiring it from astro.config.mjs.
    await expect(findIndexingProblems(DIST, new URL(PRODUCTION_ORIGIN))).resolves.toEqual([]);
  });

  it("leaves out a page that asked not to be indexed", () => {
    // Built, and absent: the two halves together. A page missing from the
    // sitemap because it was never built would prove nothing about a filter.
    expect(existsSync(join(DIST, "indexing-fixture-page", "index.html"))).toBe(true);
    expect(sitemapPaths()).not.toContain(FIXTURE_PATH);
  });

  it("leaves out drafts, and the 404", () => {
    // Both for the same reason as the fixture page above, by two different
    // mechanisms: a draft is never built (src/lib/content.ts), and the 404 is
    // dropped by @astrojs/sitemap's own status-code rule.
    expect(sitemapPaths()).not.toContain("/en/blog/the-next-release/");
    expect(sitemapPaths().some((path) => path.includes("404"))).toBe(false);
  });
});

describe("a build that is not the production site", () => {
  beforeAll(() => {
    build({ env: { PUBLIC_SITE_URL: STAGING_URL } });
  });

  afterAll(() => {
    build();
  });

  it("turns every crawler away from everything", () => {
    const robots = parseRobots(read("robots.txt"));

    expect(blocksEverything(robots)).toBe(true);
    // Not "the landing page is disallowed" — every path is, including the
    // ones no rule mentions.
    for (const path of ["/", "/en/pricing/", "/fr/blog/why-a-template/", "/og-default.png"]) {
      expect(isAllowedByRobots(path, robots), path).toBe(false);
    }
  });

  it("names both origins, so the fix is one line rather than an investigation", () => {
    const robots = read("robots.txt");

    expect(robots).toContain(STAGING_URL);
    expect(robots).toContain(PRODUCTION_ORIGIN);
    expect(robots).toContain("src/config/indexing.ts");
  });

  it("offers no sitemap, and still writes one", () => {
    // A sitemap is an invitation, so a build that disallows everything must
    // not extend one. It is still *written*, because promoting a build to
    // production has to be a change of environment rather than a different
    // build.
    expect(parseRobots(read("robots.txt")).sitemaps).toEqual([]);
    expect(sitemapPaths().length).toBeGreaterThan(30);
  });

  it("is what a fresh checkout with no environment at all gets", () => {
    // The default in astro.config.mjs is http://localhost:4321, which is not
    // the production origin either — so the safe answer is also the one
    // nobody has to configure.
    build();
    expect(blocksEverything(parseRobots(read("robots.txt")))).toBe(true);
  });
});

describe("the check that gates the build", () => {
  it("passes the clean fixture", async () => {
    await expect(
      findIndexingProblems(join(FIXTURES, "clean"), FIXTURE_ORIGIN),
    ).resolves.toEqual([]);
  });

  it("catches a sitemap URL that resolves to nothing", async () => {
    const problems = await problemsWith({
      "sitemap-0.xml": sitemapChunk("/", "/blog/", "/features/"),
    });

    expect(problems).toContain("/features/ is in the sitemap and was never built");
  });

  it("catches a page listed in the sitemap that asks not to be indexed", async () => {
    const problems = await problemsWith({
      "sitemap-0.xml": sitemapChunk("/", "/blog/", "/404.html"),
    });

    expect(problems).toContain("/404.html is in the sitemap and asks not to be indexed");
  });

  it("catches a page the sitemap recommends and robots.txt forbids", async () => {
    const problems = await problemsWith({
      "robots.txt":
        "User-agent: *\nAllow: /\nDisallow: /blog/\n\n" +
        "Sitemap: https://example.com/sitemap-index.xml\n",
    });

    expect(problems).toContain("/blog/ is in the sitemap and disallowed by robots.txt");
  });

  it("catches a built page that is in no sitemap", async () => {
    const problems = await problemsWith({ "sitemap-0.xml": sitemapChunk("/") });

    expect(problems).toContain("blog/index.html: built and indexable, and in no sitemap");
  });

  it("catches a sitemap on the wrong origin", async () => {
    const problems = await problemsWith({
      "sitemap-0.xml":
        `<urlset xmlns="${SITEMAP_NAMESPACE}">` +
        "<url><loc>http://localhost:4321/</loc></url>" +
        "<url><loc>https://example.com/blog/</loc></url></urlset>",
    });

    // The shape of a deploy that forgot PUBLIC_SITE_URL, which otherwise
    // produces a perfectly well-formed sitemap full of URLs nobody can fetch.
    expect(problems).toContain("http://localhost:4321/ is not on https://example.com");
  });

  it("catches a Sitemap: line naming a file that was not written", async () => {
    const problems = await problemsWith({
      "robots.txt": "User-agent: *\nAllow: /\n\nSitemap: https://example.com/sitemap.xml\n",
    });

    // The `sitemap.xml` mistake specifically: @astrojs/sitemap writes an
    // index and numbered chunks, and never that name.
    expect(problems).toContain("Sitemap: https://example.com/sitemap.xml was not built");
  });

  it("catches a build that blocks every crawler and offers a sitemap anyway", async () => {
    const problems = await problemsWith({
      "robots.txt":
        "User-agent: *\nDisallow: /\n\n" +
        "Sitemap: https://example.com/sitemap-index.xml\n",
    });

    expect(problems).toContain("disallows every crawler and still offers");
  });

  it("catches output with no robots.txt at all", async () => {
    const problems = await problemsWith({ "robots.txt": undefined });

    expect(problems).toContain("robots.txt: not built");
  });
});

describe("reading robots.txt", () => {
  // The precedence rules are the part of this task that is real code rather
  // than configuration, and every assertion above rests on them.

  it("gives the longest matching rule the last word, and a tie to Allow", () => {
    const robots = parseRobots("User-agent: *\nDisallow: /blog/\nAllow: /blog/keep/\n");

    expect(isAllowedByRobots("/blog/drop/", robots)).toBe(false);
    expect(isAllowedByRobots("/blog/keep/", robots)).toBe(true);
  });

  it("reads $ as the end of the URL and * as anything", () => {
    const robots = parseRobots("User-agent: *\nDisallow: /api$\nDisallow: /*/admin/\n");

    expect(isAllowedByRobots("/api", robots)).toBe(false);
    expect(isAllowedByRobots("/api-changes/", robots)).toBe(true);
    expect(isAllowedByRobots("/de/admin/users", robots)).toBe(false);
  });

  it("ignores rules written for a named crawler", () => {
    // Only the wildcard group is the promise made to crawlers in general, and
    // it is the one the sitemap has to agree with.
    const robots = parseRobots(
      "User-agent: *\nAllow: /\n\nUser-agent: BadBot\nDisallow: /\n",
    );

    expect(blocksEverything(robots)).toBe(false);
  });

  it("reads an empty Disallow as no restriction, and comments as nothing", () => {
    const robots = parseRobots("# Disallow: /\nUser-agent: *\nDisallow:\n");

    expect(robots.rules).toEqual([]);
    expect(isAllowedByRobots("/", robots)).toBe(true);
  });
});
