/**
 * Reading built HTML from Node, in the one place that knows how (T-302, T-304).
 *
 * Several scripts walk `dist/` after every `astro build` and ask questions of
 * the markup: `check-seo.mjs` wants the title, the description and the
 * canonical; `check-indexing.mjs` wants the robots directive, so a page that
 * opted out of indexing cannot also be listed in the sitemap; `caddy-site.mjs`
 * (T-307) wants the text of every inline script, to hash it into a CSP. They
 * need the same few primitives — find the head, find the tags with a given
 * name, read an attribute off one, read an element's contents — and a second
 * copy of those would be a second set of the escaping mistakes below.
 *
 * This is deliberately not an HTML parser. It reads a document this app
 * generated, in a build that has already type-checked the components that
 * generated it, looking for a handful of well-known tags in the head. A real
 * parser would be a dependency, and every quirk it handles is a quirk Astro
 * does not emit.
 */
import { readdir } from "node:fs/promises";
import { join, relative, sep } from "node:path";

/**
 * Only the head is ever scanned.
 *
 * A `<title>` inside an inline SVG icon, or a `<meta name="robots">` quoted
 * inside a guide's fenced code block, is not a directive -- and a checker
 * that fails on prose about SEO in an article about SEO would be turned off
 * within a week. The head is where these tags mean what they say.
 */
const HEAD = /<head[^>]*>([\s\S]*?)<\/head>/i;

/** A document's head, or `undefined` for a file that has none. */
export function headOf(html) {
  return html.match(HEAD)?.[1];
}

/**
 * The value of an attribute on a single tag, or "" when it has none.
 *
 * Every attribute is read and compared by name rather than a regex built
 * around the name: a pattern assembled from a string is one escaping mistake
 * away from matching nothing, and a checker that silently matches nothing
 * reports every page as broken (or, worse, none).
 */
const ATTRIBUTE = /([a-z][a-z0-9:-]*)\s*=\s*"([^"]*)"/gi;

export function attribute(tag, name) {
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
 * looks unterminated, reads as empty, and the checker fails the build over a
 * description that is fine. T-303's JSON-LD found it -- a post about ending a
 * script block has exactly that shape.
 */
const TAG = /<([a-z][a-z0-9-]*)((?:[^>"]|"[^"]*")*)>/gi;

/** Every `<name ...>` start tag in a fragment. */
export function tagsNamed(fragment, name) {
  return [...fragment.matchAll(TAG)]
    .filter((match) => match[1]?.toLowerCase() === name)
    .map((match) => match[0]);
}

/**
 * Every `<name>...</name>` element in a document, as its start tag and the
 * exact text between the tags.
 *
 * The text is returned byte-for-byte, with nothing trimmed, because T-307
 * hashes it: a CSP `'sha256-...'` source covers the script's content as the
 * parser saw it, and a helper that tidied whitespace would produce digests
 * that match nothing a browser computes.
 *
 * Non-greedy to the first closing tag, which is what the HTML parser does
 * too: a `</script>` inside a payload really does end the block, and T-303
 * escapes the one place this site can emit one.
 */
export function inlineBlocks(html, name) {
  const pattern = new RegExp(`<${name}((?:[^>"]|"[^"]*")*)>([\\s\\S]*?)</${name}\\s*>`, "gi");
  return [...html.matchAll(pattern)].map((match) => ({
    tag: `<${name}${match[1] ?? ""}>`,
    content: match[2] ?? "",
  }));
}

/** Every `.html` file under `dir`, as paths relative to it, in a stable order. */
export async function htmlFiles(dir) {
  const entries = await readdir(dir, { recursive: true, withFileTypes: true });
  return (
    entries
      .filter((entry) => entry.isFile() && entry.name.endsWith(".html"))
      // parentPath is absolute; relative() plus a forward-slash join keeps the
      // messages identical on Windows and Linux, which matters because tests
      // assert on them.
      .map((entry) => relative(dir, join(entry.parentPath, entry.name)).split(sep).join("/"))
      .sort()
  );
}
