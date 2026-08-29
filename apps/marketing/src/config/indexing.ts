/**
 * What this site tells a crawler, and the one line a product has to edit
 * before it is allowed to say "index me" (T-304).
 *
 * Two facts live here, because `robots.txt` and the sitemap are the two
 * places they both have to be applied:
 *
 * 1. **Which origin is production.** Everything else is a staging copy, and a
 *    staging copy that outranks production is a genuinely expensive mistake
 *    to undo -- weeks of "why does google send our customers to
 *    staging.acme.com". So indexing here is *opt-in*: a build is offered to
 *    crawlers only when `PUBLIC_SITE_URL` is exactly `PRODUCTION_ORIGIN`,
 *    and every other build -- a fresh checkout on `http://localhost:4321`, a
 *    preview deploy, a staging host -- serves `Disallow: /`.
 *
 * 2. **Which paths are never crawlable**, wherever they are served from.
 *
 * `src/pages/robots.txt.ts` renders these, and `scripts/check-indexing.mjs`
 * holds the built output to them: no page listed in the sitemap may be
 * disallowed here, and no page that opted out of indexing may be listed.
 */

/**
 * The one origin this product's marketing site is indexable on.
 *
 * **A fork edits this line.** It ships as the placeholder domain the rest of
 * the template uses (`supportEmail: "support@example.com"`, and T-307 puts
 * marketing at the apex with the app on `app.`), which means a fresh clone
 * indexes nothing until somebody says where production is. That is the safe
 * direction to be wrong in: an un-indexed launch is a line in a deploy log
 * away from being fixed, an indexed staging site is a reconsideration
 * request and a month of waiting.
 *
 * Committed rather than read from the environment on purpose. The whole
 * check is "is the origin this build was given the real one?" -- comparing an
 * environment variable against another environment variable set by the same
 * deploy proves nothing. The build prints which way it decided (see
 * `robots.txt.ts`), so a deploy log always says.
 */
export const PRODUCTION_ORIGIN = "https://example.com";

/** Is this build the production site, and therefore allowed to be indexed? */
export function isProductionSite(site: URL | undefined): boolean {
  return site !== undefined && site.origin === new URL(PRODUCTION_ORIGIN).origin;
}

/**
 * Where the sitemap a crawler should read actually is.
 *
 * `@astrojs/sitemap` always writes an *index* plus numbered chunks, even for
 * a site of fifty pages -- so the file to point at is `sitemap-index.xml`,
 * not the `sitemap.xml` habit expects, and there is no `sitemap.xml` here to
 * point at. A sitemap index is a first-class thing to submit; what is not
 * first-class is a `Sitemap:` line naming a file that 404s, which is why
 * `check-indexing.mjs` fails the build when this does not resolve.
 */
export const SITEMAP_PATH = "/sitemap-index.xml";

/**
 * Paths that must never be crawled, as robots.txt path prefixes.
 *
 * **None of these are served from this host**, and that is not an oversight.
 * T-307 puts marketing on its own origin and the app on `app.<domain>`, so a
 * crawler reading this file will never see an `/api/` here. They are listed
 * because `robots.txt` is per-origin and per-*deployment*: a fork that ever
 * serves the app under this domain -- the path split T-307 rejects, but which
 * somebody will eventually want -- gets the right rules without having to
 * remember this file existed. The cost of a rule for a path nobody serves is
 * zero; the cost of discovering a password-reset URL in a search result is
 * not.
 *
 * `check-indexing.mjs` is what keeps that free: it fails the build if any of
 * these ever matches a marketing page this site actually built.
 */
const NEVER_CRAWLED = [
  // Backend-owned prefixes, exactly the ones the repo-root Caddyfile routes
  // to it. /docs is the API's Swagger UI, which is also why this site's own
  // documentation lives at /guides (T-301).
  "/api",
  "/docs",
  "/redoc",
  "/openapi.json",
  "/health",
  // apps/web's routes, from apps/web/src/routes/. Everything behind the
  // session and everything in the auth flow: an indexed /reset-password is a
  // support ticket, an indexed /admin is worse.
  //
  // Read off that directory rather than from the plan, which asks for
  // `/_admin`: T-263 specified the admin area under that prefix and it landed
  // at `/admin`, because TanStack Router reads a leading underscore as a
  // pathless layout. A rule for a path the product does not have protects
  // nothing.
  //
  // Every one of them carries a locale segment (T-233 prefixes them all), so
  // a bare "/admin" would match nothing a visitor can reach. The "/*/" is
  // what makes these real rules rather than decoration.
  "/*/admin",
  "/*/settings",
  "/*/files",
  "/*/reports",
  "/*/login",
  "/*/register",
  "/*/magic-link",
  "/*/forgot-password",
  "/*/reset-password",
  "/*/verify-email",
  "/*/confirm-email-change",
  "/*/cancel-email-change",
  "/*/accept-invitation",
];

/**
 * The `Disallow:` lines, as patterns.
 *
 * Each prefix becomes two rules, and the pair is the point. `Disallow: /api`
 * on its own is a *prefix* match: it also blocks `/api-changes-in-v2`, a
 * blog post this site would very much like indexed. `/api/` plus `/api$`
 * blocks the endpoint and everything under it and nothing else.
 *
 * `$` is a Google/Bing extension. A crawler that does not implement it reads
 * `/api$` as a literal prefix that matches no real URL -- so the failure mode
 * is one rule doing nothing, never a page blocked by accident.
 */
export function disallowRules(): string[] {
  return NEVER_CRAWLED.flatMap((prefix) =>
    // A prefix whose last segment carries an extension is a file, and
    // `Disallow: /openapi.json/` would be a rule for a directory that cannot
    // exist -- noise in a file people read by hand.
    prefix.slice(prefix.lastIndexOf("/")).includes(".")
      ? [`${prefix}$`]
      : [`${prefix}$`, `${prefix}/`],
  );
}
