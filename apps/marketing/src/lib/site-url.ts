/**
 * Turning a site path into the absolute URL a crawler can actually follow.
 *
 * Every canonical, `og:image`, `@id` and `BreadcrumbList` item on this site is
 * absolute, and for the same reason: a crawler resolving `/pricing/` has no
 * base URL to resolve it against. A relative one does not fail loudly -- it
 * points at nothing, on a page nobody will look at again.
 *
 * The origin comes from `Astro.site`, which astro.config.mjs reads once from
 * `PUBLIC_SITE_URL`. Passing it in rather than importing it keeps this usable
 * from a `.astro` component, an endpoint and a test alike, and keeps
 * src/config/site.ts free of a second copy of the origin -- the drift that
 * file exists to prevent.
 */

/**
 * `Astro.site`, or a failure that says which knob is missing.
 *
 * `site` is optional in Astro's config, so `Astro.site` is `URL | undefined`
 * at every call site. Rather than each of them handling `undefined` its own
 * way (or reaching for a non-null assertion), they all come through here and
 * get one message.
 */
export function siteOrigin(site: URL | undefined): URL {
  if (!site) {
    throw new Error(
      "apps/marketing: astro.config.mjs must set `site`. Every canonical, og:url and og:image here is " +
        "absolute, and a relative one does not fail -- it just points a crawler at nothing.",
    );
  }
  return site;
}

/** An absolute URL for a site path (`"/fr/pricing/"`, `"#organization"`). */
export function absoluteUrl(path: string, site: URL | undefined): string {
  return new URL(path, siteOrigin(site)).href;
}
