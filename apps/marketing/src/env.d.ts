/// <reference types="astro/client" />

// Only PUBLIC_-prefixed variables reach the built output. Both are optional:
// astro.config.mjs and src/lib/product.ts each carry a documented local
// default so `astro build` works in a fresh checkout with no env file.
interface ImportMetaEnv {
  /**
   * Absolute origin this site is served from.
   *
   * Everything a crawler is told keys off it (T-304): the sitemap's URLs, the
   * `Sitemap:` line, and whether robots.txt allows anything at all -- a build
   * whose origin is not the `PRODUCTION_ORIGIN` in src/config/indexing.ts
   * serves `Disallow: /`.
   */
  readonly PUBLIC_SITE_URL?: string;
  /** Absolute origin of apps/web, for sign-in and sign-up links. */
  readonly PUBLIC_APP_URL?: string;
  /**
   * Absolute origin of the backend, read once at *build* time to fetch the
   * plan catalogue (T-301). Unset means "use the committed catalogue", which
   * is what a fresh checkout and every CI frontend build do.
   */
  readonly PUBLIC_API_URL?: string;
  /**
   * The registrable domain the consent cookie is scoped to, e.g.
   * `.example.com` (T-308).
   *
   * Unset means host-only, which is correct and slightly repetitive: consent
   * given on this site is then invisible to the app on `app.<domain>`, so a
   * visitor who signs in is asked once more inside the product. Setting it is
   * what makes `src/lib/consent.ts`'s reconciliation at login actually
   * reconcile. A deployment fact, not a product decision -- staging and
   * production differ -- which is why it is here rather than in
   * product.config.ts.
   */
  readonly PUBLIC_CONSENT_COOKIE_DOMAIN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
