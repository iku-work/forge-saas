/**
 * T-301's routing surface: every route in every enabled locale, and the three
 * places a disabled module has to disappear from at once.
 *
 * T-305 added the prefix policy itself -- every URL under a locale segment,
 * the default locale's included -- and the locale-less root that resolves one.
 * tests/hreflang.test.ts is where the annotations that policy makes possible
 * are checked; what is here is the shape of the URLs.
 */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { runInNewContext } from "node:vm";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  enabledLocales,
  loadProductConfig,
} from "../scripts/product-config.mjs";
import {
  APP_ROOT,
  BILLING_DISABLED_FIXTURE,
  DIST,
  TEST_APP_ORIGIN,
  build,
  hrefsOf,
  read,
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

/**
 * The path a route has under a locale.
 *
 * Written out rather than imported from src/lib/routing.ts on purpose: a test
 * that asks the implementation where a page should be passes whatever the
 * implementation does. T-305's policy is that this expression is the URL, and
 * `/en/pricing/` is what it says.
 */
function pathFor(locale: string, route: string): string {
  return `/${[locale, route].filter(Boolean).join("/")}/`;
}

/**
 * Runs a built page's redirect script against a stubbed browser, and returns
 * where it would send the visitor -- `undefined` for "it left them alone".
 *
 * The script is the one piece of T-305 that is behaviour rather than markup,
 * and reading it out of `dist/` is the only way to test what actually ships:
 * the source is TypeScript that Astro compiles and minifies, and a copy of the
 * logic here would be a test of the copy. `node:vm` is enough of a browser for
 * it -- the whole script touches `document.querySelector`, `window.location`
 * and `navigator.languages`, and the data it reads comes off the real element
 * the real build emitted.
 */
function runRedirect(
  page: string,
  pathname: string,
  languages: string[],
): string | undefined {
  const html = read(page);
  const script = html.match(
    /<script type="module">([\s\S]*?)<\/script>/,
  )?.[1];
  const marker = html.match(/<span[^>]*\bdata-locale-redirect\b[^>]*>/)?.[0];
  expect(script, `${page}: no redirect script`).toBeTruthy();
  expect(marker, `${page}: no redirect marker`).toBeTruthy();

  const dataset = {
    locales: marker?.match(/data-locales="([^"]*)"/)?.[1],
    fallback: marker?.match(/data-fallback="([^"]*)"/)?.[1],
  };
  let target: string | undefined;
  const location = {
    pathname,
    search: "",
    hash: "",
    replace: (href: string) => {
      target = href;
    },
  };

  runInNewContext(String(script), {
    document: { querySelector: () => ({ dataset }) },
    window: { location },
    navigator: { languages, language: languages[0] },
  });
  return target;
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

  it("prefixes the default locale too, exactly as astro.config.mjs says", () => {
    // The prefix policy is one decision expressed in two files
    // (src/lib/routing.ts and astro.config.mjs's `routing` block), and T-305
    // moved both to match apps/web's T-233. This is what fails if only one of
    // them ever moves back.
    const config = readFileSync(join(APP_ROOT, "astro.config.mjs"), "utf-8");
    expect(config).toMatch(/prefixDefaultLocale:\s*true/);
    expect(
      readFileSync(join(APP_ROOT, "src", "lib", "routing.ts"), "utf-8"),
    ).toMatch(/DEFAULT_LOCALE_IS_PREFIXED = true/);

    expect(existsSync(join(DIST, "en", "index.html"))).toBe(true);
    expect(existsSync(join(DIST, "fr", "index.html"))).toBe(true);
    // The English landing page is /en/, and nothing else is: the file at the
    // root is the locale-less door in front of all six (see below).
    expect(read("index.html")).not.toContain(
      "Ship the product, not the plumbing",
    );
  });

  it("applies the policy apps/web applies (T-233)", () => {
    // The app prefixes every route with a locale segment and treats a
    // locale-less URL as one to resolve and redirect. Both halves are read off
    // apps/web itself rather than restated here, because "identical to T-233"
    // is a claim about that code, and a copy of it would go stale the moment
    // somebody changed the original.
    const webRoutes = join(APP_ROOT, "..", "web", "src", "routes");
    expect(existsSync(join(webRoutes, "$locale.tsx"))).toBe(true);
    expect(readFileSync(join(webRoutes, "index.tsx"), "utf-8")).toContain(
      "localeRedirect(",
    );

    // ...and here the same two things: a locale segment on every page, and a
    // root that resolves one instead of being a page.
    for (const locale of locales) {
      expect(existsSync(join(DIST, locale, "index.html"))).toBe(true);
    }
    const root = read("index.html");
    expect(root).toContain("data-locale-redirect");
    expect(root).toContain('content="noindex, nofollow"');
  });

  it("offers every locale by name on the root, for a visitor without JavaScript", () => {
    // The redirect is a script, and a page that is only a script is a dead end
    // without one. These links are what a text browser, a script blocker and a
    // crawler get -- and, being ordinary links, they fall under the dead-link
    // assertion below like any other.
    const html = read("index.html");
    const hrefs = hrefsOf(html);

    for (const locale of locales) {
      expect(hrefs, `no link to ${locale}`).toContain(`/${locale}/`);
      expect(() => readPath(`/${locale}/`)).not.toThrow();
    }
    // Each in its own language, from @forge/i18n's registry: "suomi" is what
    // a Finnish speaker scans for, "Finnish" is not.
    expect(html).toContain("suomi");
    expect(html).toContain("Deutsch");
  });

  it("sends a locale-less deep link to the same page under a locale", () => {
    // A static host has one file to serve for an unmatched path, so /pricing
    // arrives at the 404 and the same component rewrites it -- the counterpart
    // of apps/web's $.tsx. T-307 should make this a 301 at the edge instead.
    expect(runRedirect("404.html", "/pricing", ["fi"])).toBe("/fi/pricing/");
    expect(runRedirect("404.html", "/blog/why-a-template", ["de-AT"])).toBe(
      "/de/blog/why-a-template/",
    );
  });

  it("leaves a URL that already carries a locale exactly where it is", () => {
    // The rule that stops the 404 bouncing: /en/nope is a genuine 404 and
    // stays one, as it is in the app rather than being re-prefixed forever.
    expect(runRedirect("404.html", "/en/nope", ["fi"])).toBeUndefined();
    expect(runRedirect("404.html", "/fi/nope", ["fi"])).toBeUndefined();
  });

  it("resolves the root from the browser's languages, then the default", () => {
    // The same order apps/web's resolvePreferredLocale() applies, minus the
    // two tiers a session-less origin does not have. `de-AT` is German: the
    // language subtag is what is matched, because this site has one German.
    expect(runRedirect("index.html", "/", ["ar"])).toBe("/ar/");
    expect(runRedirect("index.html", "/", ["de-AT", "en"])).toBe("/de/");
    // A language nothing here speaks falls through to the product's default
    // rather than to the first thing in the list.
    expect(runRedirect("index.html", "/", ["ja", "ko"])).toBe("/en/");
  });

  it("declares the right direction for each locale", () => {
    // Asserted on *every* built page, not just these two, by
    // scripts/check-hreflang.mjs -- see tests/hreflang.test.ts. T-305 rules
    // out a visual regression test for `ar` (T-262's is known unpassable), so
    // the attribute and the logical-property CSS are what is checked instead.
    expect(readPath("/ar/")).toContain('<html lang="ar" dir="rtl"');
    expect(readPath("/de/")).toContain('<html lang="de" dir="ltr"');
  });

  it("links only routes it actually built, in the locale of the page", () => {
    for (const locale of locales) {
      const internal = hrefsOf(readPath(pathFor(locale, ""))).filter(
        (href) => href.startsWith("/") && href !== "/favicon.svg",
      );

      expect(internal.length).toBeGreaterThan(0);
      for (const href of internal) {
        // Every internal link is a page that exists -- the dead-link bug
        // T-220 fixed once already.
        expect(
          () => readPath(href),
          `${locale}: dead link ${href}`,
        ).not.toThrow();

        // ...and stays in this page's locale, unless it is this same page in
        // another one, which is the language switcher and nothing else. Put
        // as "the switcher or nothing" rather than as an exempted region of
        // the markup, so a link that quietly loses the locale still fails
        // even when it is added inside the footer.
        const switchesLanguage = locales.some(
          (other) => href === pathFor(other, ""),
        );
        expect(
          href.startsWith(`/${locale}/`) || switchesLanguage,
          `${locale}: ${href} leaves the locale`,
        ).toBe(true);
      }
    }
  });

  it("keeps the visitor's locale on the way into the app", () => {
    // T-305's own sentence: a user in `fi` clicking "Sign in" must land in
    // `fi`. apps/web prefixes every route (T-233), so a locale-less
    // https://localhost/login would have handed that decision back to the app,
    // which resolves it from inputs this site cannot see and may well answer
    // differently.
    for (const locale of ["fi", "ar"]) {
      const external = hrefsOf(readPath(pathFor(locale, ""))).filter((href) =>
        href.startsWith("http"),
      );

      // The origin is TEST_APP_ORIGIN, not the localhost fallback: every
      // test build sets PUBLIC_APP_URL now, because check-app-links fails a
      // build whose links point at localhost. What is under test here is the
      // locale in the path, not the host in front of it.
      expect(external).toContain(`${TEST_APP_ORIGIN}/${locale}/login`);
      expect(external).toContain(`${TEST_APP_ORIGIN}/${locale}/register`);
      expect(
        external.some((href) =>
          new RegExp(`^${TEST_APP_ORIGIN}/(login|register)`).test(href),
        ),
        `${locale}: an app link with no locale in it`,
      ).toBe(false);
    }
  });

  it("puts every built route in the sitemap", () => {
    const paths = sitemapPaths();
    for (const locale of locales) {
      for (const route of STATIC_ROUTES) {
        const path = pathFor(locale, route);
        // A legal document that has not been translated is the same prose at
        // a second address, so T-305 gives it `noindex` and T-304's filter
        // drops it -- the route still exists and is still linked from every
        // footer, it just is not offered to a crawler six times. It is listed
        // in the locale it was written in, which is checked below.
        const isUntranslatedLegal =
          ["privacy", "terms"].includes(route) && locale !== DEFAULT_LOCALE;

        expect(paths.includes(path), `${path} in sitemap`).toBe(
          !isUntranslatedLegal,
        );
      }
    }
    for (const route of CONTENT_ROUTES) {
      expect(paths).toContain(pathFor(DEFAULT_LOCALE, route));
    }
  });

  it("keeps the locale-resolving root out of the sitemap", () => {
    // `/` is noindex and T-304's filter drops it: it is not the English home
    // page or the French one, and a door that competes in a search result with
    // the rooms behind it wins on brand queries and shows nobody the product.
    // What the sitemap lists is the six real home pages.
    expect(sitemapPaths()).not.toContain("/");
    for (const locale of locales) {
      expect(sitemapPaths()).toContain(pathFor(locale, ""));
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
    expect(existsSync(join(DIST, "en", "pricing"))).toBe(false);
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
