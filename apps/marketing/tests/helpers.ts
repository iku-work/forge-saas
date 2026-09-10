/**
 * Shared machinery for the build-output tests.
 *
 * Everything here runs a real `astro build` and reads `dist/`, because every
 * claim these tests make is a claim about what ships. A component rendered in
 * isolation cannot tell you that a page costs no JavaScript, that a draft was
 * left out, or that a disabled module took its route out of the sitemap.
 *
 * `vitest.config.ts` sets `fileParallelism: false` for the same reason it
 * always has: every file here writes the same `dist/`.
 */
import { execFile, execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

export const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const DIST = join(APP_ROOT, "dist");
export const GUARD = join(APP_ROOT, "scripts", "guard-enabled.mjs");

/** The module-matrix fixture with `marketing` (and everything else) off. */
export const MINIMAL_FIXTURE = resolve(
  APP_ROOT,
  "..",
  "..",
  "backend",
  "tests",
  "fixtures",
  "module_configs",
  "minimal.config.ts",
);

/** `marketing` on, `billing` off -- the only config where /pricing's absence is observable. */
export const BILLING_DISABLED_FIXTURE = join(
  APP_ROOT,
  "tests",
  "fixtures",
  "billing-disabled.config.ts",
);

export interface BuildOptions {
  /** Absolute path to a product.config.ts to build against. Omit for the committed one. */
  productConfig?: string;
  /** Extra environment for the build, e.g. PUBLIC_API_URL. */
  env?: Record<string, string>;
}

/** The committed config is the *absence* of the override, not an override pointing back at it. */
/**
 * The app origin every test build is given, exported so a test asserting on
 * app links states that dependency rather than hardcoding a host it happens
 * to observe.
 */
export const TEST_APP_ORIGIN = "https://app.example.com";

function envFor(options: BuildOptions): NodeJS.ProcessEnv {
  const env = { ...process.env, ...options.env };
  // Every build here goes through check-app-links, which fails the build when
  // a link points at localhost -- the gate that exists because exactly that
  // shipped once. A test build inherits no PUBLIC_APP_URL, so appLink() falls
  // back to the local Caddy host and the gate fires, failing suites that have
  // nothing to do with app links (tests/indexing.test.ts among them). Giving
  // it the value a real build would have is not a bypass: the gate is still
  // live for anything that sets one badly, and app-links.test.ts drives
  // findLocalLinks against fixtures rather than a build, so it is unaffected.
  env["PUBLIC_APP_URL"] ??= TEST_APP_ORIGIN;
  if (options.productConfig) {
    env["FORGE_PRODUCT_CONFIG"] = options.productConfig;
  } else {
    delete env["FORGE_PRODUCT_CONFIG"];
  }
  return env;
}

/** Runs the build the way package.json does -- through the guard, never around it. */
export function build(options: BuildOptions = {}): string {
  return execFileSync(process.execPath, [GUARD, "astro", "build"], {
    cwd: APP_ROOT,
    encoding: "utf-8",
    env: envFor(options),
  });
}

/**
 * The same build, without blocking the event loop.
 *
 * `execFileSync` stops this process dead until the build finishes, which is
 * fine everywhere except the one test that has to *answer* the build: the
 * pricing fetch talks to a stub HTTP server running in the test process, and
 * a blocked event loop cannot serve it.
 */
export async function buildAsync(options: BuildOptions = {}): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(
      process.execPath,
      [GUARD, "astro", "build"],
      { cwd: APP_ROOT, encoding: "utf-8", env: envFor(options) },
      (error, stdout, stderr) => {
        if (error) {
          reject(new Error(`astro build failed:${stdout}${stderr}`));
          return;
        }
        resolve(`${stdout}${stderr}`);
      },
    );
  });
}

/**
 * Runs a build that is expected to fail, and returns everything it printed.
 *
 * `execFileSync` throws on a non-zero exit and puts the streams on the error,
 * so a test that wants the *message* has to reach in there. A build that
 * unexpectedly succeeds is itself a failure -- that is what the throw at the
 * end is for.
 */
export function buildExpectingFailure(options: BuildOptions = {}): string {
  try {
    execFileSync(process.execPath, [GUARD, "astro", "build"], {
      cwd: APP_ROOT,
      encoding: "utf-8",
      env: envFor(options),
      stdio: "pipe",
    });
  } catch (error) {
    const failure = error as { stdout?: string; stderr?: string };
    return `${failure.stdout ?? ""}${failure.stderr ?? ""}`;
  }
  throw new Error("expected `astro build` to fail, but it succeeded");
}

/** A built page, by its path inside dist/ ("index.html", "fr/pricing/index.html"). */
export function read(page: string): string {
  return readFileSync(join(DIST, page), "utf-8");
}

/**
 * Every HTML file in the build, as paths inside dist/, in a stable order.
 *
 * Read off disk rather than listed here, so a claim made about "every page"
 * covers a page added tomorrow without anyone editing the test that makes it.
 */
export function builtPages(): string[] {
  return readdirSync(DIST, { recursive: true })
    .map((entry) => String(entry).split(sep).join("/"))
    .filter((entry) => entry.endsWith(".html"))
    .sort();
}

/** The built page for a site path: "/" -> index.html, "/fr/blog/" -> fr/blog/index.html. */
export function readPath(sitePath: string): string {
  const trimmed = sitePath.replace(/^\/+|\/+$/g, "");
  return read(trimmed === "" ? "index.html" : join(trimmed, "index.html"));
}

/**
 * The JavaScript a page costs a visitor: every <script> that is script,
 * inline or external.
 *
 * `type="application/ld+json"` is excluded deliberately and in advance --
 * T-303 puts JSON-LD on every page, and it is metadata a parser reads, not
 * code a browser runs. Excluding it here is what stops that task from
 * arriving at this assertion and weakening it to make room.
 */
export function scriptTagsOf(html: string): string[] {
  return [
    ...html.matchAll(/<script\b[^>]*>[\s\S]*?<\/script>|<script\b[^>]*\/>/g),
  ]
    .map((match) => match[0])
    .filter((tag) => !/type\s*=\s*["']application\/ld\+json["']/i.test(tag));
}

const JSON_LD_OPEN = '<script type="application/ld+json">';
const JSON_LD_CLOSE = "</script>";

/** Each JSON-LD block in a page, as its raw text, in document order. */
function jsonLdBlocks(html: string): string[] {
  const blocks: string[] = [];
  let cursor = 0;
  for (;;) {
    const start = html.indexOf(JSON_LD_OPEN, cursor);
    if (start === -1) {
      return blocks;
    }
    const from = start + JSON_LD_OPEN.length;
    const end = html.indexOf(JSON_LD_CLOSE, from);
    if (end === -1) {
      throw new Error("a JSON-LD block in this page is never closed");
    }
    blocks.push(html.slice(from, end));
    cursor = end + JSON_LD_CLOSE.length;
  }
}

/**
 * Every JSON-LD node in a page, with the one-or-many distinction flattened
 * away -- a block holding an array contributes each of its entries.
 *
 * Scanned with `indexOf` rather than a regex on purpose: this is also the
 * test of T-303's escaping. An unescaped `</script>` inside a payload ends the
 * block here exactly as it would in a browser, and what this returns is then
 * truncated JSON that `JSON.parse` refuses -- so the assertion fails for the
 * real reason rather than because a lazy regex quietly matched less.
 */
export function jsonLdNodes(html: string): Record<string, unknown>[] {
  return jsonLdBlocks(html).flatMap((block) => {
    const parsed: unknown = JSON.parse(block);
    return (
      Array.isArray(parsed) ? parsed : [parsed]
    ) as Record<string, unknown>[];
  });
}

/** The nodes of one `@type` in a page. */
export function jsonLdOfType(
  html: string,
  type: string,
): Record<string, unknown>[] {
  return jsonLdNodes(html).filter((node) => node["@type"] === type);
}

/**
 * The page with its JSON-LD blocks removed.
 *
 * What the FAQ and breadcrumb assertions are actually about is that the
 * structured data describes something a *visitor* can see -- and every string
 * in those nodes appears twice in the file, once in the markup and once in
 * the payload. Searching the whole page for a question would therefore pass
 * on a page that had lost the visible half entirely, which is the exact
 * failure Google penalises.
 */
export function withoutJsonLd(html: string): string {
  let stripped = html;
  for (const block of jsonLdBlocks(html)) {
    stripped = stripped.replace(block, "");
  }
  return stripped;
}

/**
 * Every `href` on an `<a>` in a page's rendered HTML.
 *
 * Anchors only. A bare `href="..."` match also picks up `<link rel=stylesheet>`
 * and the favicon, which are assets rather than navigation -- and a test that
 * checks "every link goes somewhere" would then demand an index.html inside
 * `_astro/BaseLayout.<hash>.css`.
 */
export function hrefsOf(html: string): string[] {
  return [...html.matchAll(/<a\b[^>]*?\shref="([^"]*)"/g)].map(
    (match) => match[1] ?? "",
  );
}

/**
 * Rendered HTML with its character references turned back into characters.
 *
 * Astro escapes text nodes, so a page that reads "Rien n'a encore" in the
 * browser contains "Rien n&#39;a encore" in the file -- and an assertion
 * written the way the copy reads fails for a reason that has nothing to do
 * with what it is checking.
 */
export function decodeEntities(html: string): string {
  return html
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

/**
 * The same text with every exotic space flattened to U+0020.
 *
 * `Intl.NumberFormat` separates a number from its currency symbol with a
 * no-break space, and in `fr` a *narrow* no-break space (U+202F) -- so a
 * rendered "49 $US" does not contain the "49 $US" a test types. Normalising is
 * the honest fix: the assertion is about the digits and the symbol, not about
 * which space Unicode chose.
 */
export function normalizeSpaces(text: string): string {
  // Escapes rather than the characters themselves: three different invisible
  // spaces inside one character class is not something a reviewer can check.
  return text.replace(/[\u00a0\u202f\u2009]/g, " ");
}

/**
 * Every URL in the sitemap, as site-relative paths.
 *
 * @astrojs/sitemap emits an index plus numbered chunks, so this reads the
 * chunks rather than assuming a single `sitemap.xml` -- there is no file by
 * that name here, which is why T-304's robots.txt points at the index.
 * Whether a URL *should* be listed is checked by scripts/check-indexing.mjs,
 * on every build; what callers here want is "does this route appear at all".
 */
export function sitemapPaths(): string[] {
  const index = read("sitemap-index.xml");
  const chunks = [
    ...index.matchAll(/<loc>([^<]*sitemap-\d+\.xml)<\/loc>/g),
  ].map((match) => match[1] ?? "");
  return chunks.flatMap((chunk) => {
    const file = chunk.slice(chunk.lastIndexOf("/") + 1);
    return [...read(file).matchAll(/<loc>([^<]*)<\/loc>/g)].map(
      (match) => new URL(match[1] ?? "").pathname,
    );
  });
}
