/**
 * The three budget killers, refused at build time rather than found by a
 * Lighthouse run (T-306).
 *
 * T-306's job measures four pages, three times each, on a CI runner. That is
 * a couple of minutes, and it is the *last* place you want to discover that
 * someone added a webfont with no `font-display`, an image with no
 * dimensions, or a marketing tag. Each of those has a known, specific effect
 * on a known, specific metric, and each is a two-line check over `dist/`:
 *
 *   1. **A webfont without `font-display: swap`** blocks text from painting
 *      for up to three seconds while the font downloads. That is Largest
 *      Contentful Paint, directly.
 *   2. **An `<img>` without width and height** has no aspect ratio until the
 *      bytes arrive, so the browser lays the page out twice. That is
 *      Cumulative Layout Shift, directly, and it is the single most common
 *      cause of it.
 *   3. **A third-party script** is a request to a host nobody here controls,
 *      on the critical path, with no budget of its own. That is Total
 *      Blocking Time, and it is also a privacy decision (T-308) and a CSP
 *      entry (T-307).
 *
 * All three checks are *vacuously* true today, and that is the intended
 * state: this site has no webfonts (the token layer uses the system stack, so
 * text paints in the first frame and costs zero bytes), no raster images
 * outside the Open Graph card nothing renders, and no third-party anything.
 * The value of the checks is that the first person to change one of those
 * finds out here, in seconds, with the rule and its reason attached -- rather
 * than in a red Lighthouse job that says "LCP 3.1 s" and leaves them to work
 * out why.
 *
 * Runs as an Astro integration, like the other three gates, so every build in
 * this repo gets it -- the tests' builds included. Also runnable by hand:
 *
 *     node scripts/check-budget.mjs [dir]     # defaults to ./dist
 */
import { readFile, readdir } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { createJiti } from "jiti";

import { attribute, htmlFiles, tagsNamed } from "./html.mjs";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * The analytics provider's hosts, read from the TypeScript config.
 *
 * jiti for the same reason `scripts/product-config.mjs` uses it: this file
 * runs as plain Node, both as an Astro integration and from the command line,
 * and a regex over the source could not tell a host in the active
 * configuration from one in the two inactive worked examples that file also
 * exports.
 */
async function allowedAnalyticsHosts() {
  const jiti = createJiti(import.meta.url, { interopDefault: true });
  const config = await jiti.import(join(APP_ROOT, "src", "config", "analytics.ts"));
  return [...config.allowedHosts()];
}

/**
 * Hosts a subresource may be fetched from.
 *
 * **Read from `src/config/analytics.ts`, not written here (T-308).** Adding a
 * third-party host is the ADR conversation, not a diff: ADR-0004's fifth
 * decision asks for a written justification for any third party this site
 * pulls in at runtime,
 * and a third-party script is the strongest form of that -- code from someone
 * else's server, executing on this origin, on the critical path, changeable
 * without a deploy. "Marketing asked for it" is not the justification.
 *
 * Deriving the list from that file rather than maintaining a second copy is
 * what makes the three places that have to agree agree: this gate, the CSP
 * `scripts/caddy-site.mjs` writes, and the consent rules the banner enforces.
 * A provider added to the config is allowed here automatically *and* gets a
 * CSP entry automatically; a host that appears in `dist/` without being in
 * that file fails the build, which is the case this check exists for.
 *
 * Empty in the committed configuration, because the committed configuration
 * has no analytics at all.
 *
 * Note what this does *not* cover: `<a href>` to another origin is a link,
 * not a subresource, and the app itself is on another origin by design
 * (T-307). Only the elements that make the browser fetch something are
 * checked below.
 */
const ALLOWED_SUBRESOURCE_HOSTS = await allowedAnalyticsHosts();

/** Elements whose URL attribute makes the browser fetch a subresource. */
const SUBRESOURCE_ATTRIBUTES = [
  ["script", "src"],
  ["link", "href"],
  ["img", "src"],
  ["source", "srcset"],
  ["iframe", "src"],
];

/**
 * The `rel` values that make a `<link>` contact a server.
 *
 * Most `<link>` tags on this site do not: `canonical`, and the `alternate`
 * cluster T-305 emits, are statements *about* a URL and are read by crawlers,
 * never fetched by a browser -- and both of them legitimately name the
 * production origin while a Lighthouse run serves the site from localhost. A
 * checker that did not know the difference would report every page of a
 * correct build as loading eleven third-party resources, which is what the
 * first version of this file did.
 *
 * `preconnect` and `dns-prefetch` fetch nothing, but they open a connection
 * to a host, which for a third party is the same disclosure a request is.
 */
const FETCHING_LINK_RELS = [
  "stylesheet",
  "preload",
  "modulepreload",
  "prefetch",
  "preconnect",
  "dns-prefetch",
  "icon",
  "shortcut icon",
  "apple-touch-icon",
  "mask-icon",
  "manifest",
];

/**
 * Is this URL fetched from somewhere this site does not control?
 *
 * A relative path, a root-relative path and a `data:` URI are all this
 * origin (or no origin at all). Anything with a host is judged against the
 * allowlist. Unparseable values are left alone: they are somebody's templating
 * mistake, and this gate is not the place to report it.
 */
function isThirdParty(url) {
  const trimmed = url.trim();
  if (trimmed === "" || trimmed.startsWith("data:") || trimmed.startsWith("#")) {
    return false;
  }
  let parsed;
  try {
    parsed = new URL(trimmed, "https://self.invalid/");
  } catch {
    return false;
  }
  return parsed.hostname !== "self.invalid" && !ALLOWED_SUBRESOURCE_HOSTS.includes(parsed.hostname);
}

/** Every `@font-face { ... }` block in a stylesheet, as its declarations. */
function fontFaceBlocks(css) {
  const blocks = [];
  let cursor = 0;
  for (;;) {
    const at = css.indexOf("@font-face", cursor);
    if (at === -1) {
      return blocks;
    }
    const open = css.indexOf("{", at);
    const close = css.indexOf("}", open);
    if (open === -1 || close === -1) {
      return blocks;
    }
    blocks.push(css.slice(open + 1, close));
    cursor = close + 1;
  }
}

/** The value of one declaration inside a block, lowercased, or "". */
function declaration(block, property) {
  for (const line of block.split(";")) {
    const colon = line.indexOf(":");
    if (colon !== -1 && line.slice(0, colon).trim().toLowerCase() === property) {
      return line.slice(colon + 1).trim().toLowerCase();
    }
  }
  return "";
}

/** Every `.css` file under `dir`, as paths relative to it, in a stable order. */
async function cssFiles(dir) {
  const entries = await readdir(dir, { recursive: true, withFileTypes: true });
  return entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".css"))
    .map((entry) => relative(dir, join(entry.parentPath, entry.name)).split(sep).join("/"))
    .sort();
}

/**
 * Everything wrong with a built site's budget, as messages.
 *
 * Returns an empty array for a clean build, which is what this repo's
 * committed state produces.
 */
export async function findBudgetProblems(dir) {
  const problems = [];

  // ── 1. Webfonts ──────────────────────────────────────────────────────────
  let fontFaceCount = 0;
  for (const file of await cssFiles(dir)) {
    const css = await readFile(join(dir, file), "utf-8");
    for (const block of fontFaceBlocks(css)) {
      fontFaceCount += 1;
      const family = declaration(block, "font-family") || "(unnamed)";
      if (declaration(block, "font-display") !== "swap") {
        problems.push(
          `${file}: @font-face ${family} has no \`font-display: swap\` — text stays invisible ` +
            `while the font downloads, which is a Largest Contentful Paint regression on every page that uses it`,
        );
      }
      const source = declaration(block, "src");
      if (isThirdParty(source.match(/url\(\s*['"]?([^'")]+)/)?.[1] ?? "")) {
        problems.push(
          `${file}: @font-face ${family} is loaded from another origin — self-host it in public/ ` +
            `so the font is not a third-party request on the critical path`,
        );
      }
    }
  }

  const pages = await htmlFiles(dir);
  const documents = await Promise.all(
    pages.map(async (file) => ({ file, html: await readFile(join(dir, file), "utf-8") })),
  );

  // A webfont that is not preloaded is discovered only after the stylesheet
  // that references it has been parsed -- two round trips before the first
  // byte of the font is requested. Which face is above the fold is not
  // something a build can know, so the rule is the checkable half: if this
  // site has webfonts at all, at least one of them must be preloaded.
  if (fontFaceCount > 0) {
    const preloads = documents.some(({ html }) =>
      tagsNamed(html, "link").some(
        (tag) => attribute(tag, "rel") === "preload" && attribute(tag, "as") === "font",
      ),
    );
    if (!preloads) {
      problems.push(
        `dist/: ${fontFaceCount} @font-face rule(s) and no \`<link rel="preload" as="font">\` on any page — ` +
          `the above-the-fold face is discovered two round trips late`,
      );
    }
  }

  for (const { file, html } of documents) {
    // ── 2. Unsized images ──────────────────────────────────────────────────
    for (const tag of tagsNamed(html, "img")) {
      const missing = ["width", "height"].filter((name) => attribute(tag, name).trim() === "");
      if (missing.length > 0) {
        problems.push(
          `${file}: <img src="${attribute(tag, "src")}"> has no ${missing.join(" and no ")} — ` +
            `the browser cannot reserve space for it and the page reflows when it loads (CLS). ` +
            `Use Astro's <Image> from astro:assets, which writes both from the file on disk`,
        );
      }
    }

    // ── 3. Third-party subresources ────────────────────────────────────────
    for (const [name, urlAttribute] of SUBRESOURCE_ATTRIBUTES) {
      for (const tag of tagsNamed(html, name)) {
        if (name === "link" && !FETCHING_LINK_RELS.includes(attribute(tag, "rel").toLowerCase())) {
          continue;
        }
        // A srcset is a comma-separated list of "url descriptor" pairs.
        const urls = attribute(tag, urlAttribute)
          .split(",")
          .map((candidate) => candidate.trim().split(/\s+/)[0] ?? "");
        for (const url of urls) {
          if (isThirdParty(url)) {
            problems.push(
              `${file}: <${name} ${urlAttribute}="${url}"> loads from another origin — ` +
                `a third-party subresource needs a justification (ADR-0004, decision 5), ` +
                `a CSP entry (T-307) and, if it is a tracker, consent (T-308). Declare it ` +
                `in src/config/analytics.ts, which is where all three read it from`,
            );
          }
        }
      }
    }
  }

  return problems;
}

/**
 * The Astro integration. Ordered before the SEO and hreflang gates in
 * astro.config.mjs only because its failures are the cheapest to act on --
 * all four run on the same `astro:build:done` and all four fail the build.
 */
export function checkBudget() {
  return {
    name: "forge:check-budget",
    hooks: {
      "astro:build:done": async ({ dir, logger }) => {
        const problems = await findBudgetProblems(fileURLToPath(dir));
        if (problems.length > 0) {
          throw new Error(
            `check-budget: ${problems.length} thing(s) in dist/ will cost T-306's performance budget:\n` +
              problems.map((problem) => `  ${problem}`).join("\n"),
          );
        }
        logger.info("no unswapped webfonts, no unsized images, no third-party subresources");
      },
    },
  };
}

async function main() {
  const dir = resolve(process.argv[2] ?? "dist");
  const problems = await findBudgetProblems(dir);
  if (problems.length > 0) {
    console.error(`check-budget: ${problems.length} problem(s) in ${dir}`);
    for (const problem of problems) {
      console.error(`  ${problem}`);
    }
    process.exit(1);
  }
  console.log(`check-budget: ${dir} is clean`);
}

// Importable by astro.config.mjs and by the tests without running the CLI.
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await main();
}
