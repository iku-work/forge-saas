/**
 * The Caddy site block that serves this app, written by the build (T-307).
 *
 * Everything else in `scripts/` reads `dist/` and complains. This one writes:
 * after every `astro build` it renders `caddy/dev/marketing.caddy` and
 * `caddy/prod/marketing.caddy`, which the repo-root `Caddyfile` and
 * `Caddyfile.prod` pick up with
 *
 *     import /etc/caddy/conf.d/*.caddy
 *
 * and which docker-compose bind-mounts from this package. Three reasons the
 * block is generated rather than committed next to the app's:
 *
 *  1. **The module toggle.** `modules.marketing: false` means there is no
 *     marketing site, and the requirement is that the block is *absent* --
 *     not that it exists and 404s. A committed block would have to be deleted
 *     by hand, which is a dirty working tree and a thing to forget;
 *     `guard-enabled.mjs` deletes these two files on the same run in which it
 *     deletes `dist/`. A glob `import` that matches nothing is a warning in
 *     Caddy's log, not an error, so the app host comes up exactly as before.
 *  2. **The CSP hashes.** The site ships three inline `<script>` blocks --
 *     Astro's island runtime, its hydration loader, and T-305's locale
 *     redirect -- and a strict `script-src` names each by SHA-256 digest.
 *     Those digests change when the code does, so the only version of this
 *     file that can be trusted is one produced from the `dist/` being served.
 *     A hand-maintained copy would be wrong within a week, and its failure
 *     mode is a site with no JavaScript and no error anybody sees.
 *  3. **The locale list.** T-305 puts every URL under a locale segment, so
 *     the edge's "which locale is this visitor" rules follow
 *     `product.config.ts` -- one list, not two.
 *
 * Run by hand with `node scripts/caddy-site.mjs [dist]`, and by
 * `astro.config.mjs` on every build. The output is `caddy fmt`-clean.
 */
import { existsSync } from "node:fs";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { createJiti } from "jiti";

import { attribute, htmlFiles, inlineBlocks } from "./html.mjs";
import { enabledLocales, loadProductConfig } from "./product-config.mjs";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** Where the generated blocks land; `.gitkeep`ed so the bind mounts resolve. */
export const CADDY_DIR = join(APP_ROOT, "caddy");

/** The path Caddy serves the site from, inside the container. */
export const SITE_ROOT = "/srv/marketing";

/**
 * The two deployments, and the whole of the difference between them.
 *
 * Same headers, same cache rules, same redirects: what changes is the name
 * the site answers to, how it gets a certificate, and whether "this is not
 * production, do not index it" is unconditional.
 *
 * `{$MARKETING_DOMAIN}` is substituted by Caddy at startup from the
 * environment (`backend/.env`, which the prod compose file passes to the
 * caddy service). Its default is deliberately a `.localhost` name: an
 * operator who deploys the prod stack without setting it gets a marketing
 * host nobody can reach and an app host that is completely unaffected, rather
 * than an edge that refuses to start because a site address was empty. Caddy
 * issues `.localhost` names an internal certificate on its own, so the
 * default also cannot set an ACME order in motion for a domain this product
 * does not own.
 */
export const TARGETS = {
  dev: {
    file: "dev/marketing.caddy",
    caddyfile: "Caddyfile",
    address: "marketing.localhost",
    // The app keeps `localhost`. That is the entire reason this split is
    // host-based rather than path-based: every Playwright spec, every ZAP
    // target and every k6 script goes on addressing https://localhost and
    // does not know this file exists.
    tls: "tls internal",
    hsts: "max-age=31536000; includeSubDomains",
    // No local stack is production, so the crawler directive is not a
    // question here -- it is always on.
    production: false,
  },
  prod: {
    file: "prod/marketing.caddy",
    caddyfile: "Caddyfile.prod",
    address: "{$MARKETING_DOMAIN:marketing.localhost}",
    // Nothing: Caddy obtains and renews a certificate for the configured
    // domain by itself, exactly as the app's block in Caddyfile.prod does.
    tls: "",
    // `preload` matches Caddyfile.prod's app block. Marketing is at the apex
    // in this topology, so its includeSubDomains covers `app.` too -- which
    // is the direction you want to be wrong in.
    hsts: "max-age=31536000; includeSubDomains; preload",
    // Here it depends on which host answered, which is a question only a
    // request can settle -- see robotsHeader().
    production: true,
  },
};

/**
 * `<script>` types that are actually script.
 *
 * A `<script type="application/ld+json">` (T-303 puts one on every page) is a
 * *data block* in the HTML spec: the parser never prepares it and the browser
 * never executes it, so `script-src` does not gate it and it needs no hash.
 * That is load-bearing -- there are 51 pages with a unique payload each, and
 * hashing them would mean a 51-entry CSP that changes whenever anybody edits
 * a headline.
 */
const EXECUTABLE_TYPES = new Set(["", "module", "text/javascript", "application/javascript"]);

/** The CSP source expression for one inline script's exact text. */
function sha256Source(content) {
  return `'sha256-${createHash("sha256").update(content, "utf-8").digest("base64")}'`;
}

/**
 * Every distinct inline script in a built site, as `'sha256-...'` sources.
 *
 * Deduplicated and sorted, so two builds of the same source produce the same
 * file and a review diff means something changed. A script with a `src` is
 * covered by `'self'` and is not hashed.
 */
export async function inlineScriptHashes(dir) {
  const sources = new Set();
  for (const file of await htmlFiles(dir)) {
    const html = await readFile(join(dir, file), "utf-8");
    for (const { tag, content } of inlineBlocks(html, "script")) {
      if (attribute(tag, "src") !== "") {
        continue;
      }
      if (!EXECUTABLE_TYPES.has(attribute(tag, "type").trim().toLowerCase())) {
        continue;
      }
      sources.add(sha256Source(content));
    }
  }
  return [...sources].sort();
}

/**
 * The Content-Security-Policy this site is served with.
 *
 * Its own policy, not the app's `default-src 'self'` from
 * `backend/app/middleware/security_headers.py`: that one covers JSON API
 * responses, which have no scripts, no styles and no framing to worry about.
 * This is a document policy, and the two differences from the obvious one are
 * both deliberate:
 *
 * - **`script-src` names hashes, never `'unsafe-inline'`.** T-305's locale
 *   redirect is inlined on purpose (a separate request on the one page whose
 *   whole job is to leave would be a round trip for nothing), and a hash pays
 *   for exactly that block. `'unsafe-inline'` would pay for every future
 *   injected one as well.
 * - **`style-src` allows `'unsafe-inline'`, and cannot avoid it.** Shiki
 *   emits a `style="color:#..."` attribute per token in every fenced code
 *   block (T-301's guides), and a CSP hash cannot cover a style *attribute* --
 *   only `'unsafe-hashes'` or `'unsafe-inline'` can, and the first needs a
 *   hash per distinct attribute value, of which there are hundreds.
 *   `style-src-attr` would scope the exemption to attributes, but Firefox
 *   does not implement it and falls back to `style-src`, so a policy written
 *   that way would leave every code block unstyled for a third of visitors.
 *   Inline CSS on a site with no user input and no session is the cheapest
 *   thing on this page to give up.
 *
 * The four `'none'`s are the ones `default-src` does *not* cover -- they have
 * no fallback and are silently absent if not written.
 *
 * **Analytics, if any, widens exactly three directives (T-308.)** The hosts
 * come from `src/config/analytics.ts`, which is also what
 * `scripts/check-budget.mjs` reads as its subresource allowlist -- one
 * declaration, so the gate that refuses an undeclared third party and the
 * header that would block it cannot disagree. Why three:
 *
 * - `script-src`, so the tag can load at all;
 * - `connect-src`, because most providers beacon to a *different* host than
 *   they load from, and `default-src 'self'` would block that with no visible
 *   error -- analytics that silently reports nothing is the failure mode this
 *   line exists to prevent;
 * - `img-src`, because a pixel is still how several of them deliver an event
 *   when `sendBeacon` is unavailable.
 *
 * In the committed configuration all three are empty and the policy is
 * byte-for-byte what it was before this task.
 */
function contentSecurityPolicy(hashes, analyticsHosts) {
  const origins = analyticsHosts.map((host) => `https://${host}`);
  const widen = (directive, sources) => [directive, ...sources].join(" ");
  return [
    "default-src 'self'",
    widen(`script-src 'self' ${hashes.join(" ")}`.trim(), origins),
    "style-src 'self' 'unsafe-inline'",
    widen("connect-src 'self'", origins),
    widen("img-src 'self' data:", origins),
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "upgrade-insecure-requests",
  ].join("; ");
}

/**
 * The per-locale redirects for a URL that carries no locale segment.
 *
 * T-305's policy is that `/pricing` is nobody's URL: it resolves to a locale
 * and redirects, exactly as `$.tsx` does in the app. `LocaleRedirect.astro`
 * does that in the page because a directory of files has no other mechanism,
 * and left a note asking this task to do it at the edge instead -- which is
 * better in two ways: `/pricing` stops being a 404 that rewrites itself, and
 * the visitor never downloads a page whose only purpose is to leave.
 *
 * **Two passes, and a 302 rather than the note's 301.**
 *
 * The passes approximate `navigator.languages`. The first matches only a
 * header whose *first* tag is an enabled locale, which is the visitor's
 * actual first choice and covers essentially all real traffic. The second
 * matches the locale anywhere in the header, for a visitor whose first choice
 * this product does not speak but whose second it does. Neither reads the
 * `q=` values: Caddy's matchers cannot, and browsers emit tags in preference
 * order anyway. Where the approximation is wrong the visitor lands on a
 * language they listed, which is the acceptable direction.
 *
 * The status code is 302, not 301, because the target depends on a request
 * header. A 301 is cached by the browser per-URL and would pin the first
 * visitor's language onto every later one on that machine -- and a shared
 * cache would do the same for a whole network. `Vary: Accept-Language` (set
 * on the same block) is what makes the 302 cacheable correctly.
 *
 * `{uri}` carries the query string through, which for a marketing site means
 * the campaign parameters survive the hop. A path with no trailing slash then
 * costs one further redirect from `file_server`'s own canonicalisation, which
 * is the price of not writing a second regex to normalise it.
 */
const ACCEPT_LANGUAGE_PASSES = [
  { suffix: "first", anchor: "^\\s*", says: "the first tag the visitor sent -- their actual first choice" },
  { suffix: "listed", anchor: "[,\\s]", says: "any tag further down their list" },
];

function localeRedirects(locales, defaultLocale) {
  const lines = [];
  for (const { suffix, anchor, says } of ACCEPT_LANGUAGE_PASSES) {
    lines.push(`\t\t# Match ${says}.`);
    for (const locale of locales) {
      lines.push(
        `\t\t@${locale}_${suffix} header_regexp Accept-Language (?i)${anchor}${locale}\\b`,
        `\t\tredir @${locale}_${suffix} /${locale}{uri} 302`,
      );
    }
    lines.push("");
  }
  lines.push(
    "\t\t# Failing all of that, the product's default locale.",
    `\t\tredir * /${defaultLocale}{uri} 302`,
  );
  return lines.join("\n");
}

/** The `X-Robots-Tag` lines for a target, as Caddyfile text. */
function robotsHeader(target, productionHost) {
  if (!target.production) {
    return [
      "\t# T-304's other half. The robots.txt in dist/ already says `Disallow: /`",
      "\t# for any build whose PUBLIC_SITE_URL is not the production origin; this is",
      "\t# the header form, which a crawler that already has the URL obeys without",
      "\t# fetching robots.txt first. Unconditional here: no local stack is",
      "\t# production.",
      '\theader X-Robots-Tag "noindex, nofollow"',
    ].join("\n");
  }
  return [
    "\t# T-304's other half, as a rule about the host actually being served",
    "\t# rather than about the build. A staging deployment of a production",
    "\t# build -- the case a build-time decision gets wrong -- is still told not",
    "\t# to index itself. Change PRODUCTION_ORIGIN in src/config/indexing.ts and",
    "\t# rebuild to move this line; it is the same constant robots.txt reads.",
    `\t@not_production not host ${productionHost}`,
    '\theader @not_production X-Robots-Tag "noindex, nofollow"',
  ].join("\n");
}

/**
 * One target's whole site block.
 *
 * Written as text rather than assembled from a JSON config on purpose: this
 * is a file a person opens at three in the morning when the marketing site is
 * down, and the comments are most of its value.
 */
/**
 * @param {(typeof TARGETS)[keyof typeof TARGETS]} target
 * @param {{
 *   hashes: string[],
 *   locales: string[],
 *   defaultLocale: string,
 *   productionHost: string,
 *   analyticsHosts?: string[],
 * }} options
 *
 * Annotated because the callers are TypeScript: without it, `analyticsHosts
 * = []` infers as `never[]` and `astro check` rejects every caller that
 * passes a real list.
 */
export function renderSite(
  target,
  { hashes, locales, defaultLocale, productionHost, analyticsHosts = [] },
) {
  // Both spellings per locale, and the bare one is not decoration: Caddy's
  // `path /en/*` does not match `/en`, so without it the bare locale falls
  // through to the redirect below and is answered with `/en/en/` -- a 404 for
  // the one URL a language switcher is most likely to produce. With it,
  // file_server canonicalises `/en` to `/en/` itself.
  const localeGlobs = locales.map((locale) => `/${locale} /${locale}/*`).join(" ");
  return `# ---------------------------------------------------------------------------
# GENERATED by apps/marketing/scripts/caddy-site.mjs -- do not edit.
#
# Rewritten by every \`astro build\`, and deleted by scripts/guard-enabled.mjs
# when \`modules.marketing\` is false, so this file existing is the same claim
# as apps/marketing/dist/ existing. Edit the generator, not this.
#
# Imported by the repo-root ${target.caddyfile} through
# \`import /etc/caddy/conf.d/*.caddy\`; docker-compose bind-mounts the directory
# this file is in, alongside apps/marketing/dist at ${SITE_ROOT}.
# ---------------------------------------------------------------------------

${target.address} {
${target.tls === "" ? "" : `\t${target.tls}\n\n`}\troot * ${SITE_ROOT}
\tencode zstd gzip

\t# Marketing's own headers, not the app's. The app's come from
\t# backend/app/middleware/security_headers.py and describe JSON API
\t# responses; these describe a document. See contentSecurityPolicy() in the
\t# generator for why script-src carries hashes and style-src does not.
\theader {
\t\tStrict-Transport-Security "${target.hsts}"
\t\tX-Content-Type-Options "nosniff"
\t\tX-Frame-Options "DENY"
\t\tReferrer-Policy "strict-origin-when-cross-origin"
\t\tPermissions-Policy "camera=(), microphone=(), geolocation=()"
\t\tContent-Security-Policy "${contentSecurityPolicy(hashes, analyticsHosts)}"
\t\t-Server
\t}

${robotsHeader(target, productionHost)}

\t# Astro writes content-addressed filenames under /_astro/, so those bytes
\t# can never change under a URL and are cached for a year. Everything else --
\t# the HTML that names them, robots.txt, the sitemap, the OG card -- is
\t# mutable at a stable URL and gets five minutes, which is long enough to
\t# absorb a burst and short enough that a fix is live before anybody asks.
\t@immutable path /_astro/*
\t@revalidated not path /_astro/*
\theader @immutable Cache-Control "public, max-age=31536000, immutable"
\theader @revalidated Cache-Control "public, max-age=300"

\t# RFC 8615's registry of well-known URIs -- security.txt, an app association
\t# file, an ACME challenge. None of them carry a locale and none of them may
\t# be redirected into one. Caddy answers its own HTTP-01 challenge ahead of
\t# every route in this block, so this is not what keeps a certificate
\t# renewing; it is what stops a file dropped into public/.well-known/ from
\t# being sent into a locale instead of being served.
\thandle /.well-known/* {
\t\tfile_server
\t}

\t# Everything the build actually produced: hashed assets, and every page,
\t# each of which lives under a locale segment (T-305).
\t@built path /_astro/* ${localeGlobs}
\thandle @built {
\t\tfile_server
\t}

\t# Root-level files -- robots.txt, sitemap-*.xml, favicon.svg, the OG card.
\t# Matched by "has an extension" rather than by name so a new file dropped
\t# into public/ is served rather than redirected into a locale.
\t@file path_regexp \\.[A-Za-z0-9]+$
\thandle @file {
\t\tfile_server
\t}

\t# Anything left carries no locale: the apex itself, and locale-less deep
\t# links like /pricing. Resolve one and redirect. See localeRedirects() in
\t# the generator for why this is a 302 and why Vary is not optional.
\thandle {
\t\theader Vary Accept-Language

${localeRedirects(locales, defaultLocale)}
\t}

\t# A static host has exactly one file it can answer an unmatched path with,
\t# and 404.html is styled, translated and linked back into the site. With a
\t# real 404 status: a soft 404 (200 on a page that says "not found") is a
\t# page Google indexes.
\thandle_errors 404 {
\t\trewrite * /404.html
\t\tfile_server {
\t\t\tstatus 404
\t\t}
\t}
}
`;
}

/**
 * Every host the configured analytics provider contacts (T-308).
 *
 * The same file `scripts/check-budget.mjs` reads for its allowlist. Empty in
 * the committed configuration.
 */
export async function analyticsHostnames() {
  const jiti = createJiti(import.meta.url, { interopDefault: true });
  const analytics = await jiti.import(join(APP_ROOT, "src", "config", "analytics.ts"));
  return [...analytics.allowedHosts()];
}

/** The production origin robots.txt and the sitemap are keyed off (T-304). */
async function productionHostname() {
  const jiti = createJiti(import.meta.url, { interopDefault: true });
  const indexing = await jiti.import(join(APP_ROOT, "src", "config", "indexing.ts"));
  return new URL(indexing.PRODUCTION_ORIGIN).host;
}

/**
 * Renders both targets from a built `dist/`, and returns what it wrote.
 *
 * Both, always: the dev block is what `make dev` and CI's E2E stack serve,
 * and the prod block is what docker-compose.prod.yml serves. Generating only
 * the one this machine happens to use would mean the other is missing exactly
 * when somebody deploys.
 */
export async function writeCaddySites(dir, configPath) {
  const [hashes, { locales, defaultLocale }, productionHost, analyticsHosts] = await Promise.all([
    inlineScriptHashes(dir),
    loadProductConfig(configPath).then(enabledLocales),
    productionHostname(),
    analyticsHostnames(),
  ]);

  const written = [];
  for (const target of Object.values(TARGETS)) {
    const path = join(CADDY_DIR, target.file);
    await mkdir(dirname(path), { recursive: true });
    await writeFile(
      path,
      renderSite(target, { hashes, locales, defaultLocale, productionHost, analyticsHosts }),
    );
    written.push(path);
  }
  return { written, hashes, analyticsHosts };
}

/**
 * Deletes both blocks, for a build that is not going to happen.
 *
 * Called by guard-enabled.mjs when `modules.marketing` is false, next to the
 * `dist/` it removes for the same reason: a stale site block would keep
 * serving a marketing host out of an empty directory long after the module
 * was turned off.
 */
export async function removeCaddySites() {
  const removed = [];
  for (const target of Object.values(TARGETS)) {
    const path = join(CADDY_DIR, target.file);
    if (existsSync(path)) {
      removed.push(path);
    }
    await rm(path, { force: true });
  }
  return removed;
}

/**
 * The Astro integration, last in astro.config.mjs.
 *
 * After the four gates, deliberately: a build that fails check-seo or
 * check-indexing must not leave a site block behind describing output that
 * was rejected. `astro:build:done` handlers run in order and a throw stops
 * the rest, so "last" is the whole of that guarantee.
 */
export function caddySite() {
  return {
    name: "forge:caddy-site",
    hooks: {
      "astro:build:done": async ({ dir, logger }) => {
        const { written, hashes, analyticsHosts } = await writeCaddySites(fileURLToPath(dir));
        logger.info(
          `wrote ${written.length} Caddy site block(s), script-src pinned to ${hashes.length} inline script hash(es)` +
            (analyticsHosts.length === 0
              ? ""
              : `, plus ${analyticsHosts.length} analytics host(s)`),
        );
      },
    },
  };
}

async function main() {
  const dir = resolve(process.argv[2] ?? "dist");
  const { written, hashes, analyticsHosts } = await writeCaddySites(dir);
  console.log(
    `caddy-site: ${hashes.length} inline script hash(es) from ${dir}` +
      (analyticsHosts.length === 0 ? "" : `, analytics hosts: ${analyticsHosts.join(", ")}`),
  );
  for (const path of written) {
    console.log(`  wrote ${path}`);
  }
}

// Importable by astro.config.mjs and by the tests without running the CLI.
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await main();
}
