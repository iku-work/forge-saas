/**
 * T-301's routing surface: every route in every enabled locale, and the three
 * places a disabled module has to disappear from at once.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  enabledLocales,
  loadProductConfig,
} from "../scripts/product-config.mjs";
import {
  APP_ROOT,
  BILLING_DISABLED_FIXTURE,
  DIST,
  build,
  hrefsOf,
  readPath,
  sitemapPaths,
} from "./helpers";

/** Every route that exists once per locale, as its path under the locale segment. */
const STATIC_ROUTES = [
  "",
  "features",
  "pricing",
  "blog",
  "guides",
  "privacy",
  "terms",
];

/** Routes that only exist where their content has been translated. */
const CONTENT_ROUTES = [
  "blog/why-a-template",
  "blog/module-toggles-in-practice",
  "guides/getting-started/installation",
  "guides/getting-started/configuration",
  "guides/deploying/production",
];

const DEFAULT_LOCALE = "en";

/** The path a route has under a locale, per the unprefixed-default policy. */
function pathFor(locale: string, route: string): string {
  const segments = [locale === DEFAULT_LOCALE ? "" : locale, route].filter(
    Boolean,
  );
  return `/${segments.join("/")}${segments.length > 0 ? "/" : ""}`;
}

describe("locale routing", () => {
  let locales: string[] = [];

  beforeAll(async () => {
    build();
    locales = enabledLocales(await loadProductConfig()).locales;
  });

  it("builds every enabled locale, and only those", () => {
    // The list astro.config.mjs feeds its `i18n` block and the list
    // src/lib/product.ts hands getStaticPaths are derived separately -- one in
    // plain Node before Vite exists, one in TypeScript. This is where they are
    // held to the same answer.
    expect(locales).toEqual(["en", "fi", "de", "es", "fr", "ar"]);

    for (const locale of locales) {
      expect(
        existsSync(join(DIST, pathFor(locale, "").slice(1), "index.html")),
      ).toBe(true);
    }
    expect(existsSync(join(DIST, "xx"))).toBe(false);
  });

  it("builds every static route in every enabled locale", () => {
    for (const locale of locales) {
      for (const route of STATIC_ROUTES) {
        const html = readPath(pathFor(locale, route));
        expect(html, `${pathFor(locale, route)} is empty`).toContain("<html");
      }
    }
  });

  it("leaves the default locale unprefixed, exactly as astro.config.mjs says", () => {
    // The prefix policy is one decision expressed in two files
    // (src/lib/routing.ts and astro.config.mjs's `routing` block). T-305 flips
    // both together to match apps/web's T-233; this is what fails if only one
    // of them moves.
    const config = readFileSync(join(APP_ROOT, "astro.config.mjs"), "utf-8");
    expect(config).toMatch(/prefixDefaultLocale:\s*false/);

    expect(existsSync(join(DIST, "index.html"))).toBe(true);
    expect(existsSync(join(DIST, "en"))).toBe(false);
    expect(existsSync(join(DIST, "fr", "index.html"))).toBe(true);
  });

  it("declares the right direction for each locale", () => {
    expect(readPath("/ar/")).toContain('<html lang="ar" dir="rtl"');
    expect(readPath("/de/")).toContain('<html lang="de" dir="ltr"');
  });

  it("links only routes it actually built, in the locale of the page", () => {
    for (const locale of locales) {
      const hrefs = hrefsOf(readPath(pathFor(locale, "")));
      const internal = hrefs.filter(
        (href) => href.startsWith("/") && href !== "/favicon.svg",
      );

      expect(internal.length).toBeGreaterThan(0);
      for (const href of internal) {
        // Every internal link is a page that exists, under this page's own
        // locale -- the dead-link bug T-220 fixed once already, and the
        // locale-loss T-305 is about.
        expect(
          () => readPath(href),
          `${locale}: dead link ${href}`,
        ).not.toThrow();
        expect(href, `${locale}: ${href} leaves the locale`).toMatch(
          locale === DEFAULT_LOCALE
            ? /^\/(?!(fi|de|es|fr|ar)\/)/
            : new RegExp(`^/${locale}/`),
        );
      }
    }
  });

  it("puts every built route in the sitemap", () => {
    const paths = sitemapPaths();
    for (const locale of locales) {
      for (const route of STATIC_ROUTES) {
        expect(
          paths,
          `${pathFor(locale, route)} missing from sitemap`,
        ).toContain(pathFor(locale, route));
      }
    }
    for (const route of CONTENT_ROUTES) {
      expect(paths).toContain(pathFor(DEFAULT_LOCALE, route));
    }
  });

  it("keeps the 404 page out of the sitemap", () => {
    expect(sitemapPaths()).not.toContain("/404/");
    expect(sitemapPaths().some((path) => path.includes("404"))).toBe(false);
  });
});

describe("a module that is switched off", () => {
  beforeAll(() => {
    build({ productConfig: BILLING_DISABLED_FIXTURE });
  });

  afterAll(() => {
    build();
  });

  it("has no page, no link and no sitemap entry", () => {
    // Three separate places a half-applied toggle can survive in, asserted
    // together because that is how they fail: the route goes, and the link
    // and the sitemap entry are what get forgotten.
    expect(existsSync(join(DIST, "pricing"))).toBe(false);
    expect(existsSync(join(DIST, "fr", "pricing"))).toBe(false);

    for (const locale of ["en", "fr", "ar"]) {
      const html = readPath(pathFor(locale, ""));
      expect(hrefsOf(html).some((href) => href.includes("pricing"))).toBe(
        false,
      );
    }

    expect(sitemapPaths().some((path) => path.includes("pricing"))).toBe(false);
  });

  it("still builds every other route in every locale", () => {
    for (const route of STATIC_ROUTES.filter((route) => route !== "pricing")) {
      expect(readPath(pathFor("fr", route))).toContain("<html");
    }
  });
});
