/**
 * No production page may link somewhere only the developer can reach.
 *
 * `src/lib/product.ts` builds every link into the app from `PUBLIC_APP_URL`,
 * falling back to `https://localhost` because that is this repo's local Caddy
 * host. The fallback is right for a dev build and silently wrong for a real
 * one, and it is not a small blast radius: `appLink()` is the header's Sign
 * in, the hero's Register and Log in, and the pricing CTA. Forget the variable
 * and every conversion path on the marketing site points at the visitor's own
 * machine -- a site that looks completely fine until somebody clicks.
 *
 * That happened on the first production deploy of this template. Nothing
 * caught it, because nothing was looking.
 *
 * **Read from the built markup, not from the environment.** Checking that
 * `PUBLIC_APP_URL` was set would only catch the one cause this time; the
 * markup is what the visitor actually clicks, so it catches a hard-coded
 * localhost in a component, a stale absolute URL pasted into MDX, or whatever
 * the next version of this mistake looks like. That is the same argument
 * `check-indexing.mjs` makes for reading `noindex` out of `dist/`.
 *
 * **Attributes only, never text.** A guide legitimately shows
 * `http://localhost:8000` in a code block, and that is documentation rather
 * than a broken link. Only `href` and `src` are destinations.
 *
 * Dev and staging builds are left alone: localhost is correct there, and the
 * check is keyed off the same `isProductionSite()` that decides whether the
 * site tells crawlers about itself at all.
 *
 * Also runnable over any directory of built output:
 *
 *     node scripts/check-app-links.mjs [dir]
 */
import { dirname, join, resolve } from "node:path";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { createJiti } from "jiti";

import { attribute, htmlFiles, tagsNamed } from "./html.mjs";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// Hosts that mean "this machine". `.localhost` is reserved for exactly this
// (RFC 6761) and is what docker-compose.yml serves marketing on locally, so
// `marketing.localhost` and friends have to be caught too.
const LOCAL_HOST = /^(https?:)?\/\/([^/]*\.)?(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(:\d+)?(\/|$)/i;

function destinationsIn(html) {
  const found = [];
  for (const name of ["a", "link", "script", "img", "iframe", "form"]) {
    for (const tag of tagsNamed(html, name)) {
      for (const attr of ["href", "src", "action"]) {
        const value = attribute(tag, attr);
        if (value) found.push({ tag: name, attr, value });
      }
    }
  }
  return found;
}

export async function findLocalLinks(distDir) {
  const problems = [];
  for (const file of await htmlFiles(distDir)) {
    // htmlFiles yields paths relative to the directory, already
    // forward-slashed so messages read identically on Windows and Linux --
    // join it back to read, keep it relative to report.
    const html = await readFile(join(distDir, file), "utf8");
    const page = `/${file}`;
    for (const { tag, attr, value } of destinationsIn(html)) {
      if (LOCAL_HOST.test(value)) {
        problems.push(`${page}: <${tag} ${attr}="${value}">`);
      }
    }
  }
  return problems;
}

export function checkAppLinks() {
  return {
    name: "forge:check-app-links",
    hooks: {
      "astro:build:done": async ({ dir, logger }) => {
        // The same test `robots.txt` uses to decide whether this build is the
        // real site. Importing the source of truth rather than re-deriving it
        // keeps the two from drifting into disagreeing about what production
        // is -- through jiti, like every other script here, because Node
        // cannot load a .ts file and a bare dynamic import would crash the
        // build rather than guard it.
        const jiti = createJiti(import.meta.url, { interopDefault: true });
        const { isProductionSite } = await jiti.import(
          join(APP_ROOT, "src", "config", "indexing.ts"),
        );
        const site = process.env.PUBLIC_SITE_URL
          ? new URL(process.env.PUBLIC_SITE_URL)
          : undefined;

        if (!isProductionSite(site)) {
          logger.info("not a production build — localhost links are expected, skipping");
          return;
        }

        const problems = await findLocalLinks(fileURLToPath(dir));
        if (problems.length > 0) {
          throw new Error(
            `check-app-links: ${problems.length} link(s) in a production build point at localhost.\n` +
              "PUBLIC_APP_URL is the usual cause — set it to the app's public origin at build time.\n" +
              problems.map((problem) => `  ${problem}`).join("\n"),
          );
        }
        logger.info("no page links to localhost");
      },
    },
  };
}

async function main() {
  const dir = resolve(process.argv[2] ?? "dist");
  const problems = await findLocalLinks(dir);
  if (problems.length > 0) {
    console.error(`check-app-links: ${problems.length} link(s) point at localhost:`);
    for (const problem of problems) console.error(`  ${problem}`);
    process.exit(1);
  }
  console.log("check-app-links: no page links to localhost");
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await main();
}
