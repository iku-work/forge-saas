/**
 * The guarantee behind <Seo> (T-302).
 *
 * <Seo> makes it *easy* for every page to carry a title, a description and a
 * canonical. This makes it impossible for one not to: it walks the built
 * `dist/` after every `astro build` and fails the build on any page missing
 * one of the three, carrying two of one, or carrying an empty one. That
 * covers the cases the component cannot -- a page written without the
 * layout, a page that grows a second head block, a description that is an
 * empty string in a locale nobody read.
 *
 * It runs as an Astro integration (see astro.config.mjs) rather than as a
 * second command in package.json, because every build in this repo goes
 * through `scripts/guard-enabled.mjs astro build` -- the tests' builds
 * included -- and a check bolted onto the package script would be absent
 * from all of them.
 *
 * Also runnable by hand, over any directory of HTML:
 *
 *     node scripts/check-seo.mjs [dir]     # defaults to ./dist
 */
import { readdir, readFile } from "node:fs/promises";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Only the head is scanned.
 *
 * A `<title>` inside an inline SVG icon, or `<link rel="canonical">` quoted
 * inside a guide's fenced code block, is not a second canonical -- and a
 * checker that fails on prose about SEO in an article about SEO would be
 * turned off within a week. The head is where the tags mean what they say.
 */
const HEAD = /<head[^>]*>([\s\S]*?)<\/head>/i;

/**
 * The value of an attribute on a single tag, or "" when it has none.
 *
 * Every attribute is read and compared by name rather than a regex built
 * around the name: a pattern assembled from a string is one escaping mistake
 * away from matching nothing, and a checker that silently matches nothing
 * reports every page as broken (or, worse, none).
 */
const ATTRIBUTE = /([a-z][a-z0-9:-]*)\s*=\s*"([^"]*)"/gi;

function attribute(tag, name) {
  for (const [, key, value] of tag.matchAll(ATTRIBUTE)) {
    if (key.toLowerCase() === name) {
      return value ?? "";
    }
  }
  return "";
}

/**
 * Every start tag in a fragment, as its full text.
 *
 * The alternation is what makes this quote-aware, and it is not decoration.
 * Inside a double-quoted attribute value only `&` and `"` have to be
 * escaped -- `<` and `>` are ordinary characters there, and Astro
 * (correctly) leaves them alone. So a page whose description reads "faster
 * than the alternative -> by a lot" emits a perfectly valid
 * `<meta name="description" content="... -> by a lot">`, and the obvious
 * `<meta[^>]*>` ends the tag at that `>`: the `content` attribute then
 * looks unterminated, reads as empty, and this checker fails the build over a
 * description that is fine. T-303's JSON-LD found it -- a post about ending a
 * script block has exactly that shape.
 */
const TAG = /<([a-z][a-z0-9-]*)((?:[^>"]|"[^"]*")*)>/gi;

/** Every `<name ...>` start tag in a fragment. */
function tagsNamed(fragment, name) {
  return [...fragment.matchAll(TAG)]
    .filter((match) => match[1]?.toLowerCase() === name)
    .map((match) => match[0]);
}

/**
 * The three tags, and how to read the value each one must not be missing.
 *
 * `find` returns one entry per occurrence, already reduced to the value that
 * has to be non-empty -- the text of a title, the `content` of a description,
 * the `href` of a canonical. Rules that read an attribute go through
 * `tagsNamed` and `attribute` rather than a pattern assembled around the
 * attribute name: a regex built from a string is one escaping mistake away
 * from matching nothing, and a checker that silently matches nothing reports
 * every page as broken (or, worse, none).
 */
const RULES = [
  {
    label: "<title>",
    // Element content rather than an attribute, so this one is read by
    // hand rather than by a pattern. <title> carries no attributes on any
    // page here, and scanning for its delimiters directly keeps TAG above
    // the only regex in this file anybody has to check twice.
    find: (head) => {
      const values = [];
      let cursor = 0;
      for (;;) {
        const open = head.indexOf("<title", cursor);
        if (open === -1) {
          return values;
        }
        const from = head.indexOf(">", open) + 1;
        const to = head.indexOf("</title", from);
        if (to === -1) {
          return values;
        }
        values.push(head.slice(from, to));
        cursor = to + 1;
      }
    },
    // Not "add a title": a page here gets one by passing it to <Seo>, and
    // pointing at the mechanism is what stops the next person adding a
    // hand-written tag beside the component's.
    fix: "pass a `title` to <Seo> (every page does, via BaseLayout)",
  },
  {
    label: 'meta name="description"',
    find: (head) =>
      tagsNamed(head, "meta")
        .filter((tag) => attribute(tag, "name") === "description")
        .map((tag) => attribute(tag, "content")),
    fix: "pass a `description` to <Seo> — it is the sentence a search result shows",
  },
  {
    label: 'link rel="canonical"',
    find: (head) =>
      tagsNamed(head, "link")
        .filter((tag) => attribute(tag, "rel") === "canonical")
        .map((tag) => attribute(tag, "href")),
    fix: "<Seo> emits one from Astro.site; a page that renders its own head does not get it",
  },
];

/** Every `.html` file under `dir`, as paths relative to it, in a stable order. */
async function htmlFiles(dir) {
  const entries = await readdir(dir, { recursive: true, withFileTypes: true });
  return entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".html"))
    // parentPath is absolute; relative() plus a forward-slash join keeps the
    // messages identical on Windows and Linux, which matters because tests
    // assert on them.
    .map((entry) => relative(dir, join(entry.parentPath, entry.name)).split(sep).join("/"))
    .sort();
}

/**
 * Every SEO problem in a directory of built HTML, as printable lines.
 *
 * Returns them all rather than throwing on the first: a build that fails
 * three times in a row, once per page, wastes three builds.
 */
export async function findSeoProblems(dir) {
  const problems = [];

  for (const file of await htmlFiles(dir)) {
    const html = await readFile(join(dir, file), "utf-8");
    const head = html.match(HEAD)?.[1];
    if (head === undefined) {
      problems.push(`${file}: has no <head> — nothing about this page is described to a crawler`);
      continue;
    }

    for (const rule of RULES) {
      const found = rule.find(head);
      if (found.length === 0) {
        problems.push(`${file}: no ${rule.label} — ${rule.fix}`);
      } else if (found.length > 1) {
        problems.push(
          `${file}: ${found.length} × ${rule.label} — a crawler picks one of them, and not the one you meant`,
        );
      } else if ((found[0] ?? "").trim() === "") {
        problems.push(`${file}: empty ${rule.label} — ${rule.fix}`);
      }
    }
  }

  return problems;
}

/**
 * The Astro integration. `astro:build:done` is after the last page and every
 * `public/` asset is written, and an error thrown from it fails the build --
 * which is the whole point: a site with an undescribed page should not reach
 * a deploy step.
 */
export function checkSeo() {
  return {
    name: "forge:check-seo",
    hooks: {
      "astro:build:done": async ({ dir, logger }) => {
        const distDir = fileURLToPath(dir);
        const problems = await findSeoProblems(distDir);
        if (problems.length > 0) {
          throw new Error(
            `check-seo: ${problems.length} page(s) in dist/ are missing required meta:\n` +
              problems.map((problem) => `  ${problem}`).join("\n"),
          );
        }
        logger.info("every page carries a title, a description and a canonical");
      },
    },
  };
}

async function main() {
  const dir = resolve(process.argv[2] ?? "dist");
  const problems = await findSeoProblems(dir);
  if (problems.length > 0) {
    console.error(`check-seo: ${problems.length} problem(s) in ${dir}`);
    for (const problem of problems) {
      console.error(`  ${problem}`);
    }
    process.exit(1);
  }
  console.log(`check-seo: ${dir} is clean`);
}

// Importable by astro.config.mjs and by the tests without running the CLI.
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await main();
}
