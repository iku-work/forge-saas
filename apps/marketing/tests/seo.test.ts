/**
 * T-302's meta block: what every built page carries, and what fails a build.
 *
 * Like every other test here, the claims are about `dist/` -- rendering
 * <Seo> in isolation would prove it can emit a canonical, not that all fifty
 * pages have one. The build in `beforeAll` runs with a real PUBLIC_SITE_URL,
 * so "absolute, from the configured origin" is observable rather than
 * indistinguishable from the localhost fallback.
 */
import { execFileSync } from "node:child_process";
import { readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { decodePng } from "../scripts/make-og-image.mjs";
import { loadProductConfig } from "../scripts/product-config.mjs";
import {
  APP_ROOT,
  DIST,
  build,
  builtPages,
  buildExpectingFailure,
  read,
  readPath,
  sitemapPaths,
} from "./helpers";

/** The origin this build is configured with, rather than the localhost fallback. */
const SITE_URL = "https://forge.example";

const CHECK_SEO = join(APP_ROOT, "scripts", "check-seo.mjs");
const FIXTURES = join(APP_ROOT, "tests", "fixtures", "seo");

/** A post that overrides its own card, so the `ogImage` field is exercised end to end. */
const FIXTURE_POST = join(APP_ROOT, "src", "content", "blog", "en", "__seo-fixture.mdx");
const FIXTURE_POST_IMAGE = "/og-fixture.png";

/**
 * A page that passes an empty description to <Seo>, to prove the build stops.
 *
 * Not `__`-prefixed like the post fixture above: Astro treats an underscore
 * at the start of a name in `src/pages/` as "not a route" and would build
 * nothing at all, which is a very convincing way for this test to pass.
 */
const FIXTURE_PAGE = join(APP_ROOT, "src", "pages", "seo-fixture-page.astro");

function headOf(html: string): string {
  return html.match(/<head[^>]*>([\s\S]*?)<\/head>/i)?.[1] ?? "";
}

/** Every `content` of a `<meta>` in the head carrying this `name` or `property`. */
function metaContents(html: string, key: string): string[] {
  const pattern = new RegExp(`<meta\\b[^>]*\\b(?:name|property)="${key}"[^>]*>`, "gi");
  return [...headOf(html).matchAll(pattern)].map(
    (match) => match[0].match(/\bcontent="([^"]*)"/)?.[1] ?? "",
  );
}

function metaContent(html: string, key: string): string | undefined {
  return metaContents(html, key)[0];
}

function countOf(html: string, pattern: RegExp): number {
  return [...headOf(html).matchAll(pattern)].length;
}

/** Every built page, as a path inside dist/ with forward slashes. */
/** The check-seo CLI over a directory: what it printed, and whether it passed. */
function runCheckSeo(dir: string): { passed: boolean; output: string } {
  try {
    const stdout = execFileSync(process.execPath, [CHECK_SEO, dir], {
      cwd: APP_ROOT,
      encoding: "utf-8",
      stdio: "pipe",
    });
    return { passed: true, output: stdout };
  } catch (error) {
    const failure = error as { stdout?: string; stderr?: string };
    return { passed: false, output: `${failure.stdout ?? ""}${failure.stderr ?? ""}` };
  }
}

beforeAll(() => {
  writeFileSync(
    FIXTURE_POST,
    [
      "---",
      "title: A post that brings its own card",
      "description: Exercises the per-entry ogImage override in the collection schema.",
      "publishedAt: 2026-08-20",
      "author: The fixture",
      `ogImage: ${FIXTURE_POST_IMAGE}`,
      "---",
      "",
      "Body.",
      "",
    ].join("\n"),
    "utf-8",
  );
  build({ env: { PUBLIC_SITE_URL: SITE_URL } });
});

afterAll(() => {
  rmSync(FIXTURE_POST, { force: true });
  rmSync(FIXTURE_PAGE, { force: true });
  // Back to the committed content and the default origin, whatever order
  // vitest runs these files in.
  build();
});

describe("the meta block on every built page", () => {
  it("carries exactly one title, one description and one canonical", () => {
    const pages = builtPages();
    // Not a hardcoded count: the point is that *every* page passes, and a
    // page added tomorrow is included without editing this test.
    expect(pages.length).toBeGreaterThan(40);

    for (const page of pages) {
      const html = read(page);
      expect(countOf(html, /<title[^>]*>/gi), `${page}: <title>`).toBe(1);
      expect(
        countOf(html, /<meta\b[^>]*\bname="description"[^>]*>/gi),
        `${page}: description`,
      ).toBe(1);
      expect(
        countOf(html, /<link\b[^>]*\brel="canonical"[^>]*>/gi),
        `${page}: canonical`,
      ).toBe(1);
      expect(metaContent(html, "description")?.trim(), `${page}: description`).not.toBe("");
    }
  });

  it("points every card at an absolute image on the configured origin", () => {
    for (const page of builtPages()) {
      const html = read(page);
      const image = metaContent(html, "og:image");

      // Absolute, and absolute *here*: a crawler has no base URL to resolve
      // "/og-default.png" against, and a card that 404s is a card that does
      // not render.
      expect(image, `${page}: og:image`).toBeTruthy();
      expect(image, `${page}: og:image`).toMatch(new RegExp(`^${SITE_URL}/`));
      expect(metaContent(html, "twitter:card"), page).toBe("summary_large_image");
    }
  });

  it("canonicalises each page to its own URL, from the configured origin", () => {
    // The origin comes from PUBLIC_SITE_URL through astro.config.mjs's `site`
    // and reaches the page as Astro.site -- there is no second copy of it in
    // TypeScript, and this is what would catch one appearing.
    for (const [page, path] of [
      ["index.html", "/"],
      ["en/index.html", "/en/"],
      ["fr/pricing/index.html", "/fr/pricing/"],
      ["ar/blog/index.html", "/ar/blog/"],
      ["en/guides/deploying/production/index.html", "/en/guides/deploying/production/"],
    ] as const) {
      const html = read(page);
      const canonical = headOf(html).match(
        /<link\b[^>]*\brel="canonical"[^>]*\bhref="([^"]*)"/i,
      )?.[1];

      expect(canonical, page).toBe(`${SITE_URL}${path}`);
      expect(metaContent(html, "og:url"), page).toBe(canonical);
    }
  });

  it("names the product once in the title, and not twice on the landing page", () => {
    // The landing page's own title *is* the product name, and
    // "ForgeSaaS — ForgeSaaS" is the classic way a title template goes wrong.
    expect(readPath("/en/")).toContain("<title>ForgeSaaS</title>");
    expect(readPath("/en/features/")).toContain("<title>Features — ForgeSaaS</title>");
  });

  it("declares the page's own locale, and the others as alternates", () => {
    const french = readPath("/fr/features/");

    expect(metaContent(french, "og:locale")).toBe("fr_FR");
    // en_US, not en_EN: og:locale is territory-qualified, and English is the
    // one locale here whose territory is not its language code uppercased.
    expect(metaContents(french, "og:alternate_locale")).toEqual([
      "en_US",
      "fi_FI",
      "de_DE",
      "es_ES",
      "ar_AR",
    ]);
  });

  it("claims a translation only where one was actually written", () => {
    // This post exists in en and de and nowhere else. Announcing five more
    // translations is the metadata form of a dead link.
    expect(metaContents(readPath("/en/blog/module-toggles-in-practice/"), "og:alternate_locale")).toEqual([
      "de_DE",
    ]);

    // ...while a post only ever written in English claims none at all.
    expect(metaContents(readPath("/en/blog/why-a-template/"), "og:alternate_locale")).toEqual([]);
  });

  it("marks a blog post as an article, with its publication date", () => {
    const post = readPath("/en/blog/why-a-template/");
    expect(metaContent(post, "og:type")).toBe("article");
    expect(metaContent(post, "article:published_time")).toBe("2026-07-02T00:00:00.000Z");

    // A landing page is not an article and has no publication date to give.
    const landing = readPath("/en/");
    expect(metaContent(landing, "og:type")).toBe("website");
    expect(metaContents(landing, "article:published_time")).toEqual([]);
  });

  it("lets an entry override its card without touching the component", () => {
    const post = readPath("/en/blog/__seo-fixture/");
    expect(metaContent(post, "og:image")).toBe(`${SITE_URL}${FIXTURE_POST_IMAGE}`);
    expect(metaContent(post, "twitter:image")).toBe(`${SITE_URL}${FIXTURE_POST_IMAGE}`);

    // ...and without dimensions, which are only known for the card this app
    // draws itself. A crawler told the wrong size drops the image.
    expect(metaContents(post, "og:image:width")).toEqual([]);

    // Everything else still comes from the committed default, with its size.
    const landing = readPath("/en/");
    expect(metaContent(landing, "og:image")).toBe(`${SITE_URL}/og-default.png`);
    expect(metaContent(landing, "og:image:width")).toBe("1200");
  });
});

describe("a page that must not be indexed", () => {
  it("says so in the markup and stays out of the sitemap", () => {
    expect(read("404.html")).toContain('content="noindex, nofollow"');
    // Both halves together: a noindex page still listed in the sitemap is an
    // invitation to crawl the page you asked not to have crawled.
    expect(sitemapPaths().some((path) => path.includes("404"))).toBe(false);
  });

  it("is one of the three kinds of page that may say it, and nothing else", () => {
    // Stated as a rule rather than as a list of files, because T-305 made the
    // set open-ended: every locale of every legal document that has not been
    // translated yet is a copy of the default locale's, and a copy is not
    // something to index. The rule is what a reviewer can check.
    //
    //   404.html    a page that does not exist
    //   index.html  the locale-less door in front of the six home pages
    //   <locale>/<legal>  the same English document at a second address
    //
    // Anything else with a robots directive is a page that quietly left the
    // index, which is the failure this assertion exists to catch.
    const legal = /^[a-z]{2}\/(privacy|terms)\/index\.html$/;
    const noindexed = builtPages().filter((page) =>
      read(page).includes('name="robots"'),
    );

    for (const page of noindexed) {
      if (page === "404.html" || page === "index.html") {
        continue;
      }
      const match = legal.exec(page);
      expect(match, `${page} is noindex and is not a legal document`).not.toBe(
        null,
      );
      // ...and only where it really is a copy: the document in the locale it
      // was written in stays indexable. The notice itself is translated, so
      // the marker is what is asserted rather than the English sentence.
      expect(read(page)).toContain('data-testid="legal-untranslated"');
    }

    expect(noindexed).toContain("404.html");
    expect(noindexed).toContain("index.html");
    expect(noindexed).toContain("fi/privacy/index.html");
    expect(noindexed).not.toContain("en/privacy/index.html");
  });
});

describe("check-seo.mjs", () => {
  it("passes a directory whose pages are complete", () => {
    const { passed, output } = runCheckSeo(join(FIXTURES, "clean"));

    expect(passed, output).toBe(true);
    expect(output).toContain("is clean");
  });

  it("passes the site this build produced", () => {
    // The same check the build itself ran, from outside the build -- so a
    // change that quietly stops wiring the integration is still caught.
    const { passed, output } = runCheckSeo(DIST);
    expect(passed, output).toBe(true);
  });

  it("does not mistake a `>` inside a description for the end of the tag", () => {
    // `<` and `>` need no escaping inside a double-quoted attribute value, so
    // a description containing one is valid markup that a naive `<meta[^>]*>`
    // truncates -- the attribute then reads as empty and the build fails over
    // a page that is fine. T-303's JSON-LD found this; tests/fixtures/seo/
    // clean/angle-brackets.html is the page that keeps it fixed.
    const { passed, output } = runCheckSeo(join(FIXTURES, "clean"));

    expect(passed, output).toBe(true);
    expect(readFileSync(join(FIXTURES, "clean", "angle-brackets.html"), "utf-8")).toContain(
      "-> by a lot",
    );
  });

  it("fails a page whose description is present but empty", () => {
    const { passed, output } = runCheckSeo(join(FIXTURES, "broken"));

    expect(passed).toBe(false);
    // Naming the file is the point, as it is for the frontmatter contract.
    expect(output).toContain('empty-description.html: empty meta name="description"');
  });

  it("fails a page with two titles, and one with no canonical", () => {
    const { output } = runCheckSeo(join(FIXTURES, "broken"));

    expect(output).toContain("two-titles.html: 2 × <title>");
    expect(output).toContain('no-canonical.html: no link rel="canonical"');
  });
});

describe("a page that ships without a description", () => {
  afterAll(() => {
    rmSync(FIXTURE_PAGE, { force: true });
    // A failed build leaves dist/ half-written -- Astro empties it before it
    // starts. Anything after this describe reads dist/, so it gets rebuilt
    // here rather than by whichever test notices first.
    build({ env: { PUBLIC_SITE_URL: SITE_URL } });
  });

  it("fails the build, naming the route", () => {
    writeFileSync(
      FIXTURE_PAGE,
      [
        "---",
        'import BaseLayout from "../layouts/BaseLayout.astro";',
        "---",
        "",
        '<BaseLayout title="A page with nothing to say" description="">',
        "  <p>Body.</p>",
        "</BaseLayout>",
        "",
      ].join("\n"),
      "utf-8",
    );

    const output = buildExpectingFailure({ env: { PUBLIC_SITE_URL: SITE_URL } });

    // <Seo> throws while the route is still known. check-seo.mjs is the net
    // under that, for anything that reaches dist/ without passing through it.
    expect(output).toContain("/seo-fixture-page");
    expect(output).toContain("empty description");
  });
});

describe("the committed Open Graph card", () => {
  it("is the size the markup promises a crawler", () => {
    const { width, height } = decodePng(readFileSync(join(DIST, "og-default.png")));

    // Asserted against the page's own og:image:width / og:image:height rather
    // than against literals: those two numbers live in src/config/site.ts and
    // the image is drawn by scripts/make-og-image.mjs, and this is the seam
    // where they could drift apart.
    const html = readPath("/en/");
    expect(String(width)).toBe(metaContent(html, "og:image:width"));
    expect(String(height)).toBe(metaContent(html, "og:image:height"));
    expect(width / height).toBeCloseTo(1.91, 1);
  });

  it("is drawn in the brand colour from product.config.ts", async () => {
    const { pixels } = decodePng(readFileSync(join(DIST, "og-default.png")));
    const config = await loadProductConfig();
    const brand = String(config.primaryColor).replace("#", "").toLowerCase();

    // The top-left pixel is the card's ground, which is the brand colour
    // itself -- so a product that changes primaryColor and forgets to run
    // `pnpm --filter marketing og-image` finds out here, rather than from a
    // link preview in someone else's chat window.
    expect(pixels.subarray(0, 3).toString("hex")).toBe(brand);
  });
});
