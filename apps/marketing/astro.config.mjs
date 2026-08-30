// @ts-check
import mdx from "@astrojs/mdx";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "astro/config";

import { caddySite } from "./scripts/caddy-site.mjs";
import { checkBudget } from "./scripts/check-budget.mjs";
import { checkHreflang } from "./scripts/check-hreflang.mjs";
import { indexing } from "./scripts/check-indexing.mjs";
import { checkSeo } from "./scripts/check-seo.mjs";
import { lighthouseFixture } from "./scripts/lighthouse-fixture.mjs";
import {
  enabledLocales,
  loadProductConfig,
  PRODUCT_CONFIG_PATH,
} from "./scripts/product-config.mjs";

// Read once, at config load, rather than hardcoding a second locale list next
// to the one in product.config.ts -- a product that turns `de` off edits one
// file, not two (ADR-0004, constraint 2).
const { locales, defaultLocale } = enabledLocales(await loadProductConfig());

export default defineConfig({
  // Constraint 4: a directory of files Caddy serves, no Node runtime, no
  // second deployment topology. T-307 wires the host.
  output: "static",

  // robots.txt and the sitemap both depend on this (T-304), including the
  // "staging must not be indexed" rule: a build whose origin is not the
  // PRODUCTION_ORIGIN in src/config/indexing.ts serves `Disallow: /`. The
  // fallback keeps `astro build` deterministic without an env file, and is
  // deliberately not a production value -- a fresh checkout indexes nothing.
  site: process.env["PUBLIC_SITE_URL"] ?? "http://localhost:4321",

  i18n: {
    defaultLocale,
    locales,
    // T-305: every URL carries a locale segment, the default locale's
    // included -- `/en/pricing`, not `/pricing`. This is apps/web's T-233
    // policy, and it is the same decision as DEFAULT_LOCALE_IS_PREFIXED in
    // src/lib/routing.ts; tests/routes.test.ts fails if only one of the two
    // ever moves. A user who crosses from `/fi/pricing` to the app's
    // `/fi/register` must not watch their language disappear on the way.
    routing: { prefixDefaultLocale: true },
  },

  markdown: {
    shikiConfig: {
      // Pinned rather than left to Astro's default, so an upgrade cannot
      // silently restyle every fenced block in T-301's guides.
      //
      // A code theme is deliberately *not* part of the token layer that
      // @forge/ui owns. Token colours describe the product's surfaces --
      // background, foreground, primary, border -- and a syntax highlighter
      // needs a dozen semantic colours that have nothing to do with a brand.
      // Inline `code` inside prose does follow the tokens (see the .prose
      // block in src/styles/global.css); the fenced blocks carry this theme.
      theme: "github-dark",
    },
  },

  integrations: [
    // Constraint 1, and the reason Astro won: pages are static HTML and a
    // React component is hydrated only where it is asked for, so @forge/ui --
    // React 18 + Radix, every component client-only by construction, zero
    // "use client" directives -- is consumable exactly as it ships.
    react(),
    // Constraint 3. T-301 adds the content collections that use it.
    mdx(),
    // T-304: @astrojs/sitemap with the filter that drops noindex pages, and
    // the check that holds the sitemap, robots.txt and dist/ to each other.
    // Spread as a pair because they are one decision -- see indexing().
    //
    // The `i18n` block is T-305's half: it is what makes each entry carry an
    // `<xhtml:link rel="alternate">` for the same page in every other locale.
    // The map is a locale to the `hreflang` value announced for it, which
    // here is the locale code itself -- these are language codes with no
    // region, and inventing one (`en-US`) would tell a crawler this site is
    // for one country when it is not.
    ...indexing({ i18n: { defaultLocale, locales: Object.fromEntries(locales.map((code) => [code, code])) } }),
    // T-302, and after the sitemap on purpose: these read what every
    // integration before them wrote. `astro:build:done` throwing is what
    // turns "a page has no description" into a failed build rather than a
    // deploy nobody inspects.
    checkSeo(),
    // T-305, and last: hreflang is the only claim on a page that is about a
    // *different* page, so it is the only one that cannot be checked until
    // every page exists.
    checkHreflang(),
    // T-306. Not a metric -- a metric needs a browser, and that is the
    // Lighthouse job -- but the three things that reliably cost one: a
    // webfont that hides text while it loads, an image with no dimensions,
    // a script from somebody else's server. Cheap enough to run on every
    // build, which is the point: the alternative is finding out from a
    // twelve-run Lighthouse job in CI.
    checkBudget(),
    // T-306's regression fixture, and nothing at all in any other build --
    // see that file for why the page it injects lives under tests/fixtures/
    // rather than under src/pages/.
    ...lighthouseFixture(),
    // T-307, and last of all: this one *writes*. Every gate above can fail
    // the build, and a Caddy site block describing output that was rejected
    // is worse than no site block at all -- so it is produced only once
    // nothing else has objected.
    caddySite(),
  ],

  vite: {
    // The same Tailwind v4 plugin apps/web and @forge/ui's Storybook run.
    // Astro is Vite-based, so the marketing site reuses this repo's
    // toolchain instead of introducing a second build system.
    plugins: [tailwindcss()],
    resolve: {
      alias: {
        // One resolution path for the product config across the whole app:
        // this config, the build guard, and every page reach the same file.
        // Without it, FORGE_PRODUCT_CONFIG would move the locale list and the
        // build guard while pages carried on importing the committed config
        // -- a half-applied override, which is the failure this whole module
        // system exists to prevent. tsconfig.json maps the same specifier to
        // the default path so `astro check` resolves it.
        "#product-config": PRODUCT_CONFIG_PATH,
      },
    },
  },
});
