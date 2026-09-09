import type { APIRoute } from "astro";

import {
  PRODUCTION_ORIGIN,
  SITEMAP_PATH,
  disallowRules,
  isProductionSite,
} from "../config/indexing";
import { site } from "../config/site";
import { siteOrigin } from "../lib/site-url";

/**
 * `robots.txt`, rendered at build time rather than committed to `public/`
 * (T-304).
 *
 * A static file cannot do the one thing this file exists to do: decide
 * whether *this* build is the production site. That answer comes from
 * `PUBLIC_SITE_URL`, which is set by whichever environment ran the build, and
 * it changes both halves of the output -- a staging copy disallows everything,
 * and the `Sitemap:` line has to be an absolute URL on the origin the build
 * was actually given. A committed `public/robots.txt` would also *win* over
 * this route and then quietly rot, which is why there is not one.
 *
 * The rules themselves live in src/config/indexing.ts, next to the origin
 * they are conditional on. `scripts/check-indexing.mjs` reads this file back
 * out of `dist/` after the build and fails on any disagreement between it and
 * the sitemap.
 */
export const GET: APIRoute = ({ site: siteUrl }) => {
  const origin = siteOrigin(siteUrl);
  const body = isProductionSite(origin) ? production(origin) : notProduction(origin);

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      // Short, and shorter than the assets around it. This file is how a
      // mistake in the paragraph above gets *undone* -- a fork that discovers
      // it disallowed everything wants the fix live at the next deploy, not
      // at the end of a day-long cache.
      "Cache-Control": "public, max-age=3600",
    },
  });
};

/** The site is the real one: crawl the marketing pages, nothing else. */
function production(origin: URL): string {
  return [
    `# ${site.name} — ${origin.origin}`,
    "#",
    "# Generated at build time by src/pages/robots.txt.ts.",
    "",
    "User-agent: *",
    "Allow: /",
    // Not one of these paths is served from this host (T-307 gives the app
    // its own), and src/config/indexing.ts says why they are here anyway.
    ...disallowRules().map((rule) => `Disallow: ${rule}`),
    "",
    // Absolute, because a Sitemap: line is the one place in this file a
    // relative path is not merely discouraged but ignored.
    `Sitemap: ${new URL(SITEMAP_PATH, origin).href}`,
    "",
  ].join("\n");
}

/**
 * The site is a staging copy, a preview deploy or a developer's laptop:
 * offer a crawler nothing at all.
 *
 * No `Sitemap:` line either. A sitemap is an invitation, and this build is
 * not inviting anybody -- the file is still written, so promoting a build to
 * production is a change of environment rather than a different build.
 */
function notProduction(origin: URL): string {
  return [
    `# ${site.name} — this build is not the production site.`,
    "#",
    `# It was built for ${origin.origin}, and the production origin declared in`,
    `# src/config/indexing.ts is ${PRODUCTION_ORIGIN}. So nothing here is offered to a`,
    "# crawler: a staging copy that outranks production is expensive to undo, and",
    "# robots.txt is the half of that fix which needs no server configuration.",
    "# T-307 adds the other half, an X-Robots-Tag: noindex header on the same",
    "# condition.",
    "#",
    "# If this *is* production, PRODUCTION_ORIGIN in src/config/indexing.ts is the",
    "# line to change — not this file, and not PUBLIC_SITE_URL.",
    "",
    "User-agent: *",
    "Disallow: /",
    "",
  ].join("\n");
}
