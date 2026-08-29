/**
 * T-305: the locale annotations on every built page, and the gate behind them.
 *
 * The claims split the same way T-304's do. What the *site* says is asserted
 * over a real `dist/` -- which locales a page announces, that each one
 * resolves, that a locale a product turned off leaves no trace. What the
 * *gate* catches is asserted over a fixture directory with one thing broken
 * in it at a time, because a build that is right cannot demonstrate that a
 * checker would notice a build that is wrong.
 *
 * The origin matters here in a way it does not elsewhere: an `hreflang` href
 * is absolute, so the build in `beforeAll` runs with a real PUBLIC_SITE_URL
 * rather than the localhost fallback, and "on the configured origin" is
 * observable.
 */
import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { findHreflangProblems } from "../scripts/check-hreflang.mjs";
import {
  enabledLocales,
  loadProductConfig,
} from "../scripts/product-config.mjs";
import {
  APP_ROOT,
  DIST,
  build,
  builtPages,
  read,
  readPath,
} from "./helpers";

/** The origin this build is configured with, rather than the localhost fallback. */
const SITE_URL = "https://forge.example";

const CHECK_HREFLANG = join(APP_ROOT, "scripts", "check-hreflang.mjs");

/** Every enabled locale, in product.config.ts's order. */
const LOCALES = ["en", "fi", "de", "es", "fr", "ar"];

/** A locale the registry ships and this product enables -- so its pages exist. */
const DEFAULT_LOCALE = "en";

interface Alternate {
  hreflang: string;
  href: string;
}

/** Every `link rel="alternate"` with an `hreflang`, in document order. */
function alternatesOf(html: string): Alternate[] {
  const head = html.match(/<head[^>]*>([\s\S]*?)<\/head>/i)?.[1] ?? "";
  return [
    ...head.matchAll(
      /<link\b[^>]*\brel="alternate"[^>]*\bhreflang="([^"]*)"[^>]*\bhref="([^"]*)"[^>]*>/gi,
    ),
  ].map((match) => ({ hreflang: match[1] ?? "", href: match[2] ?? "" }));
}

/** The `hreflang` values a page announces, in document order. */
function localesAnnouncedBy(html: string): string[] {
  return alternatesOf(html).map((alternate) => alternate.hreflang);
}

beforeAll(() => {
  build({ env: { PUBLIC_SITE_URL: SITE_URL } });
});

afterAll(() => {
  // Back to the default origin, whatever order vitest runs these files in.
  build();
});

describe("the alternates on a page", () => {
  it("are exactly the enabled locales plus x-default", () => {
    for (const locale of LOCALES) {
      for (const route of ["", "features", "pricing", "blog", "guides"]) {
        const path = `/${[locale, route].filter(Boolean).join("/")}/`;
        expect(localesAnnouncedBy(readPath(path)), path).toEqual([
          ...LOCALES,
          "x-default",
        ]);
      }
    }
  });

  it("include the page's own locale, which is what makes the cluster valid", () => {
    // Google confirms a cluster only when every member points at every
    // member, itself included. A page that lists the other five and not
    // itself has its annotations discarded whole -- and looks perfect while
    // it happens.
    for (const locale of LOCALES) {
      expect(localesAnnouncedBy(readPath(`/${locale}/`))).toContain(locale);
    }
  });

  it("point at absolute URLs on the configured origin", () => {
    // A crawler has no base to resolve "/fr/pricing/" against. This is also
    // what would catch an annotation still carrying the localhost fallback
    // after a deploy that forgot PUBLIC_SITE_URL.
    for (const { hreflang, href } of alternatesOf(readPath("/fr/pricing/"))) {
      expect(href, hreflang).toMatch(new RegExp(`^${SITE_URL}/`));
    }
  });

  it("each resolve to a built page, written in the language announced", () => {
    // The two halves of T-305's second acceptance criterion, over every page
    // in the build rather than a sample: nothing points at a URL that 404s,
    // and nothing points at a URL serving another language.
    for (const page of builtPages()) {
      for (const { hreflang, href } of alternatesOf(read(page))) {
        const path = decodeURIComponent(new URL(href).pathname);

        expect(() => readPath(path), `${page}: ${href}`).not.toThrow();
        if (hreflang !== "x-default") {
          expect(readPath(path), `${page}: ${href}`).toContain(
            `<html lang="${hreflang}"`,
          );
        }
      }
    }
  });

  it("send x-default to the default locale", () => {
    const alternates = alternatesOf(readPath("/ar/guides/"));
    const xDefault = alternates.find(
      (alternate) => alternate.hreflang === "x-default",
    );

    expect(xDefault?.href).toBe(`${SITE_URL}/${DEFAULT_LOCALE}/guides/`);
  });
});

describe("a page that was not translated", () => {
  it("has no page and no alternate in the locales it is missing from", () => {
    // This post exists in en and de and nowhere else, so it is one URL per
    // locale that has it and nothing at all in the other four. Announcing a
    // French translation of a post nobody translated is the metadata form of
    // the dead link T-220 fixed once.
    const path = "/de/blog/module-toggles-in-practice/";

    expect(localesAnnouncedBy(readPath(path))).toEqual([
      "en",
      "de",
      "x-default",
    ]);
    expect(() => readPath("/fr/blog/module-toggles-in-practice/")).toThrow();

    // ...and the pages that *do* exist point at each other, both ways.
    expect(localesAnnouncedBy(readPath("/en/blog/module-toggles-in-practice/"))).toEqual(
      ["en", "de", "x-default"],
    );
  });

  it("announces only itself when it was written once", () => {
    expect(
      localesAnnouncedBy(readPath("/en/blog/why-a-template/")),
    ).toEqual(["en", "x-default"]);
  });

  it("keeps a legal fallback out of the cluster and out of the index", () => {
    // The one collection that falls back rather than 404ing (T-301). The page
    // exists in every locale and is the same English prose in five of them,
    // so it is noindex, carries no hreflang, and the document in the locale
    // it was actually written in carries the whole cluster -- which here is
    // one member.
    for (const document of ["privacy", "terms"]) {
      for (const locale of LOCALES.filter((code) => code !== DEFAULT_LOCALE)) {
        const path = `/${locale}/${document}/`;
        const fallback = readPath(path);

        expect(fallback, path).toContain('data-testid="legal-untranslated"');
        expect(fallback, path).toContain('content="noindex, nofollow"');
        expect(alternatesOf(fallback), path).toEqual([]);
      }
      // ...and the original carries the cluster, which here has one member.
      expect(localesAnnouncedBy(readPath(`/${DEFAULT_LOCALE}/${document}/`))).toEqual([
        DEFAULT_LOCALE,
        "x-default",
      ]);
    }
  });
});

describe("the language switcher", () => {
  it("offers a visitor exactly what hreflang offers a crawler", () => {
    // The head and the footer read the same list, so a page cannot advertise
    // a translation to a search engine that a reader is not offered, or the
    // other way round. Every link in it is a page that exists -- asserted for
    // every internal link by tests/routes.test.ts.
    const html = readPath("/de/blog/module-toggles-in-practice/");
    const announced = localesAnnouncedBy(html).filter(
      (code) => code !== "x-default",
    );

    for (const code of announced) {
      expect(html, `no switcher link for ${code}`).toContain(
        `href="/${code}/blog/module-toggles-in-practice/"`,
      );
    }
    // In their own names, from @forge/i18n's registry.
    expect(html).toContain("Deutsch");
    expect(html).toContain("English");
  });

  it("is absent where there is nothing to switch to", () => {
    // A post written once exists at one address. A switcher listing one
    // language is a control that does nothing.
    expect(readPath("/en/blog/why-a-template/")).not.toContain(
      'aria-current="true"',
    );
  });
});

describe("right-to-left locales", () => {
  it("declare their direction on every page, not just the home page", () => {
    // T-305 rules out a visual regression test here on purpose -- T-262's
    // `ar` RTL screenshot is known unpassable, and a second instance of a
    // broken check helps nobody. The attribute is what is asserted, on every
    // built page, by scripts/check-hreflang.mjs; these are the two spot
    // checks that make the rule readable.
    for (const page of builtPages().filter((file) => file.startsWith("ar/"))) {
      expect(read(page), page).toContain('<html lang="ar" dir="rtl"');
    }
  });

  it("lay out with logical properties, so one stylesheet serves both directions", () => {
    // `start`/`end` rather than `left`/`right`: the skip link sits at the top
    // corner the reading direction begins in, and there is no `[dir="rtl"]`
    // override anywhere to keep in step with the LTR rules.
    const html = readPath("/ar/");

    expect(html).toContain("focus:start-4");
    expect(html).not.toContain("focus:left-4");
  });
});

describe("a locale a product turned off", () => {
  // Every enabled locale has to be *enabled*, so the fixture turns one off
  // rather than adding one: the registry is what limits the set, and a locale
  // outside it has no translations to be found.
  const FIXTURE = join(APP_ROOT, "tests", "fixtures", "no-french.config.ts");

  beforeAll(async () => {
    const config = await loadProductConfig();
    expect(enabledLocales(config).locales).toContain("fr");
    build({ productConfig: FIXTURE, env: { PUBLIC_SITE_URL: SITE_URL } });
  });

  afterAll(() => {
    build({ env: { PUBLIC_SITE_URL: SITE_URL } });
  });

  it("leaves no page, no alternate and no sitemap entry anywhere in the build", () => {
    // The three places a half-applied locale toggle survives in, and the
    // reason this is one test: the pages are the easy half.
    expect(() => readPath("/fr/")).toThrow();
    expect(builtPages().some((page) => page.startsWith("fr/"))).toBe(false);

    for (const page of builtPages()) {
      expect(localesAnnouncedBy(read(page)), page).not.toContain("fr");
    }
    expect(read("sitemap-0.xml")).not.toContain(`${SITE_URL}/fr/`);
  });

  it("still annotates the locales that are left", () => {
    expect(localesAnnouncedBy(readPath("/de/"))).toEqual([
      "en",
      "fi",
      "de",
      "es",
      "ar",
      "x-default",
    ]);
  });
});

describe("check-hreflang.mjs", () => {
  /**
   * The clean fixture with one file rewritten, in a directory of its own.
   *
   * The same shape T-304's gate tests use: one committed clean directory plus
   * a visible mutation, rather than a dozen near-identical broken ones that a
   * reader has to diff to understand.
   */
  function withMutation(
    mutations: Record<string, string | undefined>,
  ): string {
    const dir = mkdtempSync(join(tmpdir(), "forge-hreflang-"));
    cpSync(join(APP_ROOT, "tests", "fixtures", "hreflang", "clean"), dir, {
      recursive: true,
    });
    for (const [file, contents] of Object.entries(mutations)) {
      if (contents === undefined) {
        rmSync(join(dir, file), { force: true });
      } else {
        writeFileSync(join(dir, file), contents, "utf-8");
      }
    }
    return dir;
  }

  const ORIGIN = new URL("https://example.com");
  const OPTIONS = { locales: ["en", "de"], defaultLocale: "en" };

  const temporary: string[] = [];

  function mutate(mutations: Record<string, string | undefined>): string {
    const dir = withMutation(mutations);
    temporary.push(dir);
    return dir;
  }

  afterAll(() => {
    for (const dir of temporary) {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("passes a directory whose pages agree with each other", async () => {
    await expect(
      findHreflangProblems(mutate({}), ORIGIN, OPTIONS),
    ).resolves.toEqual([]);
  });

  it("passes the site this build produced", async () => {
    // The same check the build itself ran, from outside the build -- so a
    // change that quietly unwires the integration is still caught.
    await expect(
      findHreflangProblems(DIST, new URL(SITE_URL), {
        locales: LOCALES,
        defaultLocale: DEFAULT_LOCALE,
      }),
    ).resolves.toEqual([]);
  });

  it("fails an annotation pointing at a page that was never built", async () => {
    const problems = await findHreflangProblems(
      mutate({ "de/index.html": undefined }),
      ORIGIN,
      OPTIONS,
    );

    expect(problems.join("\n")).toContain("/de/, which was never built");
  });

  it("fails an annotation pointing at another language", async () => {
    // The failure T-305 is named after: a page served under /de/ that is
    // actually English, announced as the German version.
    const problems = await findHreflangProblems(
      mutate({
        "de/index.html": page({
          lang: "en",
          canonical: "https://example.com/de/",
          alternates: [
            ["en", "https://example.com/en/"],
            ["de", "https://example.com/de/"],
            ["x-default", "https://example.com/en/"],
          ],
        }),
      }),
      ORIGIN,
      OPTIONS,
    );

    expect(problems.join("\n")).toContain('is written in "en"');
  });

  it("fails a cluster that is not confirmed both ways", async () => {
    const problems = await findHreflangProblems(
      mutate({
        "de/index.html": page({
          lang: "de",
          canonical: "https://example.com/de/",
          alternates: [["de", "https://example.com/de/"]],
        }),
      }),
      ORIGIN,
      OPTIONS,
    );

    expect(problems.join("\n")).toContain("does not point back");
  });

  it("fails a page that annotates every locale but its own", async () => {
    const problems = await findHreflangProblems(
      mutate({
        "de/index.html": page({
          lang: "de",
          canonical: "https://example.com/de/",
          alternates: [
            ["en", "https://example.com/en/"],
            ["x-default", "https://example.com/en/"],
          ],
        }),
      }),
      ORIGIN,
      OPTIONS,
    );

    expect(problems.join("\n")).toContain("annotates other locales and not its own");
  });

  it("fails a page whose direction contradicts the locale registry", async () => {
    const problems = await findHreflangProblems(
      mutate({
        "de/index.html": page({
          lang: "de",
          dir: "rtl",
          canonical: "https://example.com/de/",
          alternates: [
            ["en", "https://example.com/en/"],
            ["de", "https://example.com/de/"],
            ["x-default", "https://example.com/en/"],
          ],
        }),
      }),
      ORIGIN,
      OPTIONS,
    );

    expect(problems.join("\n")).toContain("registry says ltr");
  });

  it("fails a page whose language contradicts the locale in its URL", async () => {
    const problems = await findHreflangProblems(
      mutate({
        "de/index.html": page({
          lang: "en",
          canonical: "https://example.com/de/",
          alternates: [
            ["en", "https://example.com/en/"],
            ["de", "https://example.com/de/"],
            ["x-default", "https://example.com/en/"],
          ],
        }),
      }),
      ORIGIN,
      OPTIONS,
    );

    expect(problems.join("\n")).toContain('served at /de/ and written in "en"');
  });

  it("fails an indexable page that annotates nothing at all", async () => {
    const problems = await findHreflangProblems(
      mutate({
        "de/index.html": page({
          lang: "de",
          canonical: "https://example.com/de/",
          alternates: [],
        }),
      }),
      ORIGIN,
      OPTIONS,
    );

    expect(problems.join("\n")).toContain("carries no hreflang");
  });

  it("fails a noindex page that is still in a cluster", async () => {
    const problems = await findHreflangProblems(
      mutate({
        "de/index.html": page({
          lang: "de",
          canonical: "https://example.com/de/",
          noindex: true,
          alternates: [
            ["en", "https://example.com/en/"],
            ["de", "https://example.com/de/"],
            ["x-default", "https://example.com/en/"],
          ],
        }),
      }),
      ORIGIN,
      OPTIONS,
    );

    expect(problems.join("\n")).toContain("is noindex and still carries");
  });

  it("fails an annotation for a locale the product does not enable", async () => {
    const problems = await findHreflangProblems(
      mutate({
        "en/index.html": page({
          lang: "en",
          canonical: "https://example.com/en/",
          alternates: [
            ["en", "https://example.com/en/"],
            ["de", "https://example.com/de/"],
            ["fr", "https://example.com/en/"],
            ["x-default", "https://example.com/en/"],
          ],
        }),
      }),
      ORIGIN,
      OPTIONS,
    );

    expect(problems.join("\n")).toContain('hreflang="fr" is not an enabled locale');
  });

  it("runs as a CLI over any directory of built output", () => {
    // The importable form is what the integration uses; this is the form a
    // person uses when a deploy looks wrong and the build log has scrolled.
    const output = execFileSync(
      process.execPath,
      [CHECK_HREFLANG, DIST, SITE_URL],
      { cwd: APP_ROOT, encoding: "utf-8" },
    );

    expect(output).toContain("annotates its locales consistently");
  });
});

/** A minimal built page, in the shape <Seo> emits one. */
function page(options: {
  lang: string;
  dir?: string;
  canonical: string;
  noindex?: boolean;
  alternates: [string, string][];
}): string {
  const { lang, dir = "ltr", canonical, noindex = false, alternates } = options;
  return [
    "<!DOCTYPE html>",
    `<html lang="${lang}" dir="${dir}">`,
    "<head>",
    "<title>A page</title>",
    '<meta name="description" content="A page in a fixture directory.">',
    `<link rel="canonical" href="${canonical}">`,
    ...(noindex ? ['<meta name="robots" content="noindex, nofollow">'] : []),
    ...alternates.map(
      ([hreflang, href]) =>
        `<link rel="alternate" hreflang="${hreflang}" href="${href}">`,
    ),
    "</head>",
    "<body><p>Body.</p></body>",
    "</html>",
    "",
  ].join("\n");
}
