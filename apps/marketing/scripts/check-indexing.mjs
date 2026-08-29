/**
 * The sitemap and `robots.txt`, and the gate that keeps them telling the same
 * story (T-304).
 *
 * Two things ship here.
 *
 * **A filter for `@astrojs/sitemap`.** The plan asks it to drop `noindex`
 * pages, drafts and the routes of a disabled module. Two of those three are
 * already impossible: a draft and a disabled module's page are never *built*,
 * and this integration only ever sees pages that were -- `isPublished()` in
 * `src/lib/content.ts` drops the draft from its collection, and a
 * module-gated page returns no paths from its own `getStaticPaths`. The
 * one that is not impossible is `noindex`, so that is what the filter reads --
 * and it reads it from the built HTML rather than from a list kept beside it,
 * because a list is a second place to remember and the markup is what the
 * crawler will actually see.
 *
 * **A check that fails the build**, in the shape `check-seo.mjs` established:
 * an `astro:build:done` hook that walks `dist/` and throws. It is the half
 * that catches what a filter cannot, because a filter can only ever remove
 * things -- a sitemap URL that resolves to no page, a page silently missing
 * from the sitemap, a `robots.txt` that disallows a page the sitemap is busy
 * recommending. "Every URL in it resolves" is one of this task's acceptance
 * criteria, and this is it as a build gate rather than as a thing somebody
 * checks once by hand.
 *
 * Also runnable over any directory of built output:
 *
 *     node scripts/check-indexing.mjs [dir] [origin]
 */
import sitemap from "@astrojs/sitemap";
import { existsSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { attribute, headOf, htmlFiles, tagsNamed } from "./html.mjs";

// ── Reading a built page ────────────────────────────────────────────────────

/**
 * Does this page tell a crawler not to index it?
 *
 * `googlebot` as well as `robots`: a page can address one crawler by name,
 * and a sitemap entry for a page Google is told to drop is the same
 * contradiction as one for a page every crawler is told to drop.
 */
export function isNoindex(html) {
  const head = headOf(html);
  if (head === undefined) {
    return false;
  }
  return tagsNamed(head, "meta")
    .filter((tag) => ["robots", "googlebot"].includes(attribute(tag, "name").toLowerCase()))
    .some((tag) =>
      attribute(tag, "content")
        .toLowerCase()
        .split(",")
        .map((directive) => directive.trim())
        .includes("noindex"),
    );
}

/**
 * A URL path as the filesystem spells it.
 *
 * A `<loc>` is percent-encoded and a filename is not, so a guide whose slug is
 * `erste-schritte/größe` is `/erste-schritte/gr%C3%B6%C3%9Fe/` in the sitemap
 * and `erste-schritte/größe/index.html` on disk. Comparing the two without
 * this would report a perfectly good page as built-but-unlisted, which is the
 * worst thing a build gate can do: fail on something correct.
 *
 * A malformed escape is returned unchanged rather than thrown over -- this is
 * a checker, and the problems it exists to report are further down.
 */
function decodePath(path) {
  try {
    return decodeURIComponent(path);
  } catch {
    return path;
  }
}

/**
 * The file a site path was built to, or `undefined` for a path nothing built.
 *
 * `build.format` is Astro's default `"directory"`, so almost every page here
 * is `<path>/index.html` and almost every URL ends in a slash. The other two
 * candidates are for the page that is not a directory: the 404, which is
 * written as `404.html` and reached at `/404.html`. Which of those two spellings
 * a caller has depends on whether it started from a URL or from a file, and
 * neither should have to know.
 */
export function pageFileFor(dir, pathname) {
  const rel = decodePath(pathname).replace(/^\/+/, "").replace(/\/+$/, "");
  const candidates =
    rel === ""
      ? ["index.html"]
      : [`${rel}/index.html`, `${rel}.html`, ...(rel.endsWith(".html") ? [rel] : [])];
  return candidates.find((candidate) => existsSync(join(dir, candidate)));
}

/** The site path a built file is served at: `fr/blog/index.html` -> `/fr/blog/`. */
function pathForFile(file) {
  return file === "index.html"
    ? "/"
    : file.endsWith("/index.html")
      ? `/${file.slice(0, -"index.html".length)}`
      : `/${file}`;
}

// ── Reading robots.txt ──────────────────────────────────────────────────────

/**
 * The `User-agent: *` group's rules, plus every `Sitemap:` line.
 *
 * Only the wildcard group is read. A product that adds a group for one
 * crawler by name is making a decision about that crawler; what this file
 * checks is the promise made to all of them, which is the group every crawler
 * without a name of its own obeys.
 *
 * Grouping follows the original spec: consecutive `User-agent` lines share
 * one group, and the first rule line closes the list of agents.
 */
export function parseRobots(text) {
  const rules = [];
  const sitemaps = [];
  let agents = [];
  let collectingAgents = true;

  for (const raw of text.split(/\r?\n/)) {
    const line = raw.replace(/#.*$/, "").trim();
    const separator = line.indexOf(":");
    if (separator === -1) {
      continue;
    }
    const field = line.slice(0, separator).trim().toLowerCase();
    // sliced rather than split, because the value of a Sitemap line is a URL
    // and contains the separator itself.
    const value = line.slice(separator + 1).trim();

    if (field === "user-agent") {
      if (!collectingAgents) {
        agents = [];
        collectingAgents = true;
      }
      agents.push(value.toLowerCase());
    } else if (field === "allow" || field === "disallow") {
      collectingAgents = false;
      if (agents.includes("*") && value !== "") {
        rules.push({ allow: field === "allow", pattern: value });
      }
    } else if (field === "sitemap") {
      sitemaps.push(value);
    }
  }

  return { rules, sitemaps };
}

/**
 * Does a rule's pattern match a path?
 *
 * `*` (any run of characters) and `$` (end of URL) are the two extensions
 * Google and Bing implement, and src/config/indexing.ts leans on both. A
 * crawler that implements neither reads the pattern literally, which can only
 * ever make a rule match *less* -- so the failure mode is a rule doing
 * nothing, never a page blocked by accident.
 */
function matchesRule(pattern, path) {
  const anchored = pattern.endsWith("$");
  const body = anchored ? pattern.slice(0, -1) : pattern;
  const source = body
    .split("*")
    .map((literal) => literal.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join(".*");
  return new RegExp(`^${source}${anchored ? "$" : ""}`).test(path);
}

/**
 * May a crawler fetch this path?
 *
 * Longest matching pattern wins, `Allow` breaking a tie -- the precedence
 * every major crawler settled on, and the reason `Allow: /` next to a list of
 * `Disallow:` prefixes means what it looks like it means rather than
 * cancelling them.
 */
export function isAllowedByRobots(path, robots) {
  let winner;
  for (const rule of robots.rules) {
    if (!matchesRule(rule.pattern, path)) {
      continue;
    }
    if (
      !winner ||
      rule.pattern.length > winner.pattern.length ||
      (rule.pattern.length === winner.pattern.length && rule.allow)
    ) {
      winner = rule;
    }
  }
  return winner ? winner.allow : true;
}

/** Is this the staging form -- every crawler turned away from the whole site? */
export function blocksEverything(robots) {
  return !isAllowedByRobots("/", robots);
}

// ── Reading the sitemap ─────────────────────────────────────────────────────

const LOC = /<loc>([^<]*)<\/loc>/g;

function locations(xml) {
  return [...xml.matchAll(LOC)].map((match) => match[1] ?? "");
}

// ── The check ───────────────────────────────────────────────────────────────

/**
 * Everything wrong with a directory of built output, as printable lines.
 *
 * Returns all of them rather than throwing on the first: a build that fails
 * once per page wastes one build per page.
 *
 * `origin` is the site the build was given (`Astro.site`). It is passed in
 * rather than read back out of the output, because "every URL in the sitemap
 * is on the origin this build was configured for" is one of the things worth
 * checking -- a sitemap full of `http://localhost:4321` URLs is exactly what
 * a deploy that forgot `PUBLIC_SITE_URL` produces.
 */
export async function findIndexingProblems(dir, origin) {
  const problems = [];
  const at = (file) => join(dir, file);

  if (!existsSync(at("robots.txt"))) {
    problems.push(
      "robots.txt: not built — src/pages/robots.txt.ts is what produces it, and a site " +
        "without one is crawled entirely at the crawler's discretion",
    );
    return problems;
  }
  const robots = parseRobots(await readFile(at("robots.txt"), "utf-8"));
  const staging = blocksEverything(robots);

  if (!existsSync(at("sitemap-index.xml"))) {
    problems.push(
      "sitemap-index.xml: not built — @astrojs/sitemap writes it from every page in " +
        "the build, so an absent one means it filtered all of them away",
    );
    return problems;
  }

  // The index names the chunks by absolute URL; each one has to be a file
  // beside it, or the index is a list of 404s.
  const chunks = [];
  for (const url of locations(await readFile(at("sitemap-index.xml"), "utf-8"))) {
    const file = url.slice(url.lastIndexOf("/") + 1);
    if (existsSync(at(file))) {
      chunks.push(file);
    } else {
      problems.push(`sitemap-index.xml: names ${url}, which was not written`);
    }
  }
  if (chunks.length === 0) {
    problems.push("sitemap-index.xml: names no sitemap at all");
  }

  const listed = new Set();
  for (const chunk of chunks) {
    const xml = await readFile(at(chunk), "utf-8");
    if (!xml.includes("http://www.sitemaps.org/schemas/sitemap/0.9")) {
      problems.push(`${chunk}: no sitemap namespace — a crawler will not read this as a sitemap`);
    }
    for (const url of locations(xml)) {
      let pathname;
      try {
        const parsed = new URL(url);
        pathname = parsed.pathname;
        if (parsed.origin !== origin.origin) {
          problems.push(
            `${chunk}: ${url} is not on ${origin.origin} — this build's ` +
              "PUBLIC_SITE_URL is what every URL in a sitemap has to be relative to",
          );
        }
      } catch {
        problems.push(`${chunk}: ${url} is not an absolute URL`);
        continue;
      }
      listed.add(decodePath(pathname));

      const file = pageFileFor(dir, pathname);
      if (file === undefined) {
        problems.push(`${chunk}: ${pathname} is in the sitemap and was never built`);
        continue;
      }
      if (isNoindex(await readFile(at(file), "utf-8"))) {
        problems.push(
          `${chunk}: ${pathname} is in the sitemap and asks not to be indexed ` +
            "— one of the two is wrong",
        );
      }
      if (!staging && !isAllowedByRobots(pathname, robots)) {
        problems.push(
          `${chunk}: ${pathname} is in the sitemap and disallowed by robots.txt — a ` +
            "rule in src/config/indexing.ts is matching a page this site actually serves",
        );
      }
    }
  }

  // The converse, and the half a filter can never catch: a page that was
  // built, wants to be indexed, and is in no sitemap is invisible.
  for (const file of await htmlFiles(dir)) {
    const path = pathForFile(file);
    if (listed.has(path) || isNoindex(await readFile(at(file), "utf-8"))) {
      continue;
    }
    problems.push(
      `${file}: built and indexable, and in no sitemap — either list it or give the page noindex`,
    );
  }

  if (staging && robots.sitemaps.length > 0) {
    problems.push(
      `robots.txt: disallows every crawler and still offers ${robots.sitemaps[0]} — a ` +
        "sitemap is an invitation, and this build is not inviting anybody",
    );
  }
  if (!staging) {
    if (robots.sitemaps.length === 0) {
      problems.push("robots.txt: allows crawling and names no Sitemap:");
    }
    for (const url of robots.sitemaps) {
      let pathname;
      try {
        pathname = new URL(url).pathname;
      } catch {
        problems.push(
          `robots.txt: Sitemap: ${url} is not an absolute URL, so a crawler ignores it`,
        );
        continue;
      }
      if (!existsSync(at(pathname.replace(/^\/+/, "")))) {
        problems.push(`robots.txt: Sitemap: ${url} was not built`);
      }
    }
  }

  return problems;
}

// ── The integrations ────────────────────────────────────────────────────────

/**
 * `@astrojs/sitemap` with this site's filter, followed by the check that
 * holds its output to `robots.txt` and to `dist/`.
 *
 * Returned as a pair, and spread into `integrations` as a pair, because they
 * are one decision: the filter can only remove URLs, and removing the wrong
 * one is silent until the check says so. Keeping them separable would mean
 * keeping a way to have the filter without the gate.
 *
 * Order matters and is the order they are returned in. Astro runs
 * `astro:build:done` hooks in integration order, so the sitemap is written
 * before anything reads it back.
 *
 * **Per-locale `<xhtml:link rel="alternate">` entries are T-305's**, along
 * with the `hreflang` tags in the head they have to agree with. Configuring
 * this integration's `i18n` option here would settle half of that question in
 * the wrong task.
 */
export function indexing() {
  // Set from astro:config:done, which runs for every integration before any
  // build hook does, and read by the filter during the sitemap's
  // astro:build:done. The two are in one closure so the ordering is a
  // property of this function rather than of astro.config.mjs.
  let distDir;
  let site;

  const filter = (url) => {
    if (distDir === undefined) {
      throw new Error(
        "check-indexing: the sitemap filter ran before astro:config:done. Both halves " +
          "of indexing() must be in astro.config.mjs's integrations, in the order it " +
          "returns them.",
      );
    }
    const { pathname } = new URL(url);
    const file = pageFileFor(distDir, pathname);
    // A URL with no page behind it is kept deliberately, so the check below
    // can fail the build naming it. Dropping it here would fix the symptom
    // and hide a route that generates nothing.
    return file === undefined ? true : !isNoindex(readFileSync(join(distDir, file), "utf-8"));
  };

  return [
    sitemap({ filter }),
    {
      name: "forge:check-indexing",
      hooks: {
        "astro:config:done": ({ config }) => {
          distDir = fileURLToPath(config.outDir);
          // `config.site` is the string astro.config.mjs was given, not the
          // URL a page sees as `Astro.site` -- so it is parsed once here
          // rather than at both of the places below that want an origin.
          site = config.site === undefined ? undefined : new URL(config.site);
        },
        "astro:build:done": async ({ dir, logger }) => {
          if (site === undefined) {
            // The same invariant src/lib/site-url.ts states for pages, from
            // the one side of the build that can see the whole output: with
            // no `site`, @astrojs/sitemap writes nothing and every URL a
            // crawler is given would have been relative to nowhere.
            throw new Error(
              "check-indexing: astro.config.mjs must set `site`. Without it there is no " +
                "sitemap and no absolute URL to put in robots.txt.",
            );
          }

          const built = fileURLToPath(dir);
          const problems = await findIndexingProblems(built, site);
          if (problems.length > 0) {
            throw new Error(
              `check-indexing: ${problems.length} problem(s) between dist/, robots.txt ` +
                "and the sitemap:\n" +
                problems.map((problem) => `  ${problem}`).join("\n"),
            );
          }

          const robots = parseRobots(await readFile(join(built, "robots.txt"), "utf-8"));
          if (blocksEverything(robots)) {
            // Loud, every build, because this is the state a deploy is least
            // likely to notice: the site is perfect and nobody is allowed to
            // look at it.
            logger.warn(
              `robots.txt disallows every crawler — ${site.origin} is not the ` +
                "PRODUCTION_ORIGIN declared in src/config/indexing.ts",
            );
          } else {
            logger.info("robots.txt and the sitemap agree, and every URL in it resolves");
          }
        },
      },
    },
  ];
}

async function main() {
  const dir = resolve(process.argv[2] ?? "dist");
  const origin = new URL(process.argv[3] ?? "http://localhost:4321");
  const problems = await findIndexingProblems(dir, origin);
  if (problems.length > 0) {
    console.error(`check-indexing: ${problems.length} problem(s) in ${dir}`);
    for (const problem of problems) {
      console.error(`  ${problem}`);
    }
    process.exit(1);
  }
  console.log(`check-indexing: ${dir} is consistent`);
}

// Importable by astro.config.mjs and by the tests without running the CLI.
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await main();
}
