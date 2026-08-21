/// <reference types="astro/client" />

// Only PUBLIC_-prefixed variables reach the built output. Both are optional:
// astro.config.mjs and src/lib/product.ts each carry a documented local
// default so `astro build` works in a fresh checkout with no env file.
interface ImportMetaEnv {
  /** Absolute origin this site is served from. T-304 keys robots/sitemap off it. */
  readonly PUBLIC_SITE_URL?: string;
  /** Absolute origin of apps/web, for sign-in and sign-up links. */
  readonly PUBLIC_APP_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
