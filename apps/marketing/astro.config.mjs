// @ts-check
import mdx from "@astrojs/mdx";
import react from "@astrojs/react";
import sitemap from "@astrojs/sitemap";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "astro/config";

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

  // T-304 makes robots.txt and the sitemap depend on this, including the
  // "staging must not be indexed" rule. The fallback keeps `astro build`
  // deterministic without an env file -- it is not a production value, and
  // T-304's check is what stops it reaching production.
  site: process.env["PUBLIC_SITE_URL"] ?? "http://localhost:4321",

  i18n: {
    defaultLocale,
    locales,
    // Left at Astro's default (the default locale is not prefixed) *for now*.
    // T-305 owns this: its job is to match apps/web's T-233 prefix policy
    // exactly, so a user crossing from marketing to the app keeps their
    // locale. Do not settle it here by guessing.
    routing: { prefixDefaultLocale: false },
  },

  integrations: [
    // Constraint 1, and the reason Astro won: pages are static HTML and a
    // React component is hydrated only where it is asked for, so @forge/ui --
    // React 18 + Radix, every component client-only by construction, zero
    // "use client" directives -- is consumable exactly as it ships.
    react(),
    // Constraint 3. T-301 adds the content collections that use it.
    mdx(),
    // T-304 replaces the defaults here with the filter that drops noindex
    // pages, drafts, and routes belonging to a disabled module.
    sitemap(),
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
