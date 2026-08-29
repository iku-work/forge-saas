/**
 * The gate behind T-305's two acceptance criteria: the prefix policy is real,
 * and no `hreflang` anywhere points at a URL that 404s or serves another
 * language.
 *
 * `<Seo>` builds every annotation from one list -- the locales a page exists
 * in -- so the markup is right by construction. This is the half that does
 * not trust construction. hreflang is the only claim a page makes about a
 * *different* page, which means it is the only one that cannot be checked
 * while that page is being rendered: a post translated into German announces
 * a German URL, and whether that URL was built, is in German, and announces
 * this one back are three facts that exist only once the whole site does.
 * Google's own name for the last of those is bidirectional confirmation, and
 * an unconfirmed cluster is one it discards silently -- the worst kind of
 * broken, because the pages all look fine.
 *
 * The same walk checks `lang` and `dir` on every page, because they are the
 * same claim addressed to a person rather than a crawler. It is also T-305's
 * RTL requirement as a build gate: `ar` pages are asserted right-to-left here,
 * on every page, rather than by a screenshot test the task explicitly says not
 * to add (see T-262's note on the unpassable `ar` visual check).
 *
 * Runs as an Astro integration, last -- see astro.config.mjs. Also runnable
 * over any directory of built output:
 *
 *     node scripts/check-hreflang.mjs [dir] [origin]
 */
import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { localeMeta } from "@forge/i18n";

import { isNoindex, pageFileFor } from "./check-indexing.mjs";
import { attribute, headOf, htmlFiles, tagsNamed } from "./html.mjs";
import { enabledLocales, loadProductConfig } from "./product-config.mjs";

/** The value `x-default` carries, spelled once. */
const X_DEFAULT = "x-default";

/**
 * The document element's attributes.
 *
 * The *first* `html` start tag in the file, deliberately: a guide about HTML
 * may quote one inside a fenced code block, and that copy is always further
 * down the page than the real one. `headOf` cannot help here -- `lang` and
 * `dir` live on the element that contains the head.
 */
function documentElement(html) {
  const tag = tagsNamed(html, "html")[0];
  return tag === undefined
    ? undefined
    : { lang: attribute(tag, "lang"), dir: attribute(tag, "dir") };
}

/** Every `link rel="alternate"` with an `hreflang`, from the head only. */
function alternatesOf(html) {
  const head = headOf(html);
  if (head === undefined) {
    return [];
  }
  return tagsNamed(head, "link")
    .filter(
      (tag) =>
        attribute(tag, "rel") === "alternate" && attribute(tag, "hreflang") !== "",
    )
    .map((tag) => ({
      hreflang: attribute(tag, "hreflang"),
      href: attribute(tag, "href"),
    }));
}

/** The site path a built file is served at: `fr/blog/index.html` -> `/fr/blog/`. */
function pathForFile(file) {
  return file === "index.html"
    ? "/"
    : file.endsWith("/index.html")
      ? `/${file.slice(0, -"index.html".length)}`
      : `/${file}`;
}

/**
 * The site path an annotation points at, decoded, or `undefined` for an href
 * that is not a URL on this origin.
 *
 * Decoded because a `href` is percent-encoded and a filename is not: a German
 * guide at `größe/index.html` is announced as `/de/guides/gr%C3%B6%C3%9Fe/`,
 * and comparing the two spellings without this reports a correct annotation
 * as broken. Exactly the trap T-304's `decodePath` exists for, one file over.
 */
function pathOfHref(href, origin) {
  try {
    const url = new URL(href);
    return url.origin === origin.origin
      ? decodeURIComponent(url.pathname)
      : undefined;
  } catch {
    return undefined;
  }
}

/** The locale a site path is prefixed with, or `undefined` for a locale-less one. */
function localeOfPath(path, locales) {
  const first = decodeURIComponent(path).split("/").filter(Boolean)[0];
  return first !== undefined && locales.includes(first) ? first : undefined;
}

/**
 * Everything wrong with the locale annotations in a directory of built
 * output, as printable lines.
 *
 * All of them rather than the first, as the other two gates do: a build that
 * fails once per page wastes one build per page.
 */
export async function findHreflangProblems(dir, origin, { locales, defaultLocale }) {
  const problems = [];

  // Read once. Every rule below is about a pair of pages, so the alternative
  // is re-reading the target of every annotation on every page.
  const pages = new Map();
  for (const file of await htmlFiles(dir)) {
    const html = await readFile(join(dir, file), "utf-8");
    pages.set(pathForFile(file), {
      file,
      element: documentElement(html),
      alternates: alternatesOf(html),
      noindex: isNoindex(html),
    });
  }

  for (const [path, page] of pages) {
    const { file, element, alternates, noindex } = page;
    const locale = localeOfPath(path, locales);

    // ── lang and dir ────────────────────────────────────────────────────
    if (element === undefined) {
      problems.push(`${file}: no html element — nothing declares this page's language`);
      continue;
    }
    if (!locales.includes(element.lang)) {
      problems.push(
        `${file}: lang="${element.lang}" is not an enabled locale — product.config.ts ` +
          `enables ${locales.join(", ")}`,
      );
    } else {
      const expectedDir = localeMeta(element.lang)?.dir ?? "ltr";
      if (element.dir !== expectedDir) {
        problems.push(
          `${file}: lang="${element.lang}" with dir="${element.dir}" — @forge/i18n's ` +
            `registry says ${expectedDir}, and a right-to-left language laid out ` +
            "left-to-right is unreadable rather than merely wrong",
        );
      }
    }
    if (locale !== undefined && element.lang !== locale) {
      problems.push(
        `${file}: served at /${locale}/ and written in "${element.lang}" — the locale ` +
          "in the URL is the promise the page has to keep",
      );
    }

    // ── the annotations themselves ──────────────────────────────────────
    if (noindex) {
      if (alternates.length > 0) {
        problems.push(
          `${file}: is noindex and still carries ${alternates.length} hreflang link(s) — ` +
            "a page that is not the canonical anything does not belong to a language cluster",
        );
      }
      continue;
    }
    if (alternates.length === 0) {
      if (locale !== undefined) {
        problems.push(
          `${file}: indexable, under /${locale}/, and carries no hreflang — pass the ` +
            "locales this page exists in to <Seo> (BaseLayout forwards alternateLocales)",
        );
      }
      continue;
    }

    const targets = new Map();
    const seen = new Set();
    for (const { hreflang, href } of alternates) {
      if (seen.has(hreflang)) {
        problems.push(`${file}: two hreflang="${hreflang}" links — one of them is a lie`);
      }
      seen.add(hreflang);

      if (hreflang !== X_DEFAULT && !locales.includes(hreflang)) {
        problems.push(
          `${file}: hreflang="${hreflang}" is not an enabled locale — a locale ` +
            "product.config.ts turns off has no page and gets no annotation",
        );
      }

      const target = pathOfHref(href, origin);
      if (target === undefined) {
        problems.push(
          `${file}: hreflang="${hreflang}" points at ${href}, which is not an absolute ` +
            `URL on ${origin.origin} — a crawler has no base to resolve one against, and ` +
            "this build's PUBLIC_SITE_URL is what the rest of the site is relative to",
        );
        continue;
      }
      targets.set(hreflang, target);

      if (pageFileFor(dir, target) === undefined) {
        problems.push(
          `${file}: hreflang="${hreflang}" points at ${target}, which was never built`,
        );
        continue;
      }

      const other = pages.get(target);
      if (other === undefined) {
        // Built, but not as a page this walk can read -- an asset, say.
        problems.push(`${file}: hreflang="${hreflang}" points at ${target}, which is not a page`);
        continue;
      }
      if (hreflang === X_DEFAULT) {
        continue;
      }
      if (other.element?.lang !== hreflang) {
        problems.push(
          `${file}: hreflang="${hreflang}" points at ${target}, which is written in ` +
            `"${other.element?.lang}" — announcing a translation that was never made is ` +
            "exactly what hreflang exists to prevent",
        );
        continue;
      }
      // Bidirectional confirmation. A cluster whose members do not all point
      // back at each other is one Google discards whole, so a missing return
      // link takes the annotations off pages that are perfectly correct.
      if (
        target !== path &&
        !other.alternates.some(
          (alternate) =>
            alternate.hreflang === locale &&
            pathOfHref(alternate.href, origin) === path,
        )
      ) {
        problems.push(
          `${file}: hreflang="${hreflang}" points at ${target}, which does not point back ` +
            `with hreflang="${locale}" — an unconfirmed cluster is ignored entirely`,
        );
      }
    }

    if (locale !== undefined && !seen.has(locale)) {
      problems.push(
        `${file}: annotates other locales and not its own — every member of a cluster ` +
          "lists every member, itself included",
      );
    }
    // x-default is demanded only where there is an honest answer to give.
    // A post translated into German and Spanish and never into the default
    // locale has no "if we do not have your language" version, and inventing
    // one would either 404 or send a stranger to a language chosen at random.
    if (seen.has(defaultLocale)) {
      if (!seen.has(X_DEFAULT)) {
        problems.push(
          `${file}: exists in the default locale and declares no ${X_DEFAULT} — that is ` +
            "where a visitor whose language this site does not have gets sent",
        );
      } else if (targets.get(X_DEFAULT) !== targets.get(defaultLocale)) {
        problems.push(
          `${file}: ${X_DEFAULT} points at ${targets.get(X_DEFAULT)} and hreflang=` +
            `"${defaultLocale}" at ${targets.get(defaultLocale)} — they are the same claim ` +
            "and have to be the same URL",
        );
      }
    }
  }

  return problems;
}

/**
 * The Astro integration. Last in the list, because the only thing it cannot
 * do is run before the page an annotation points at has been written.
 */
export function checkHreflang() {
  let locales;
  let defaultLocale;
  let site;

  return {
    name: "forge:check-hreflang",
    hooks: {
      "astro:config:done": ({ config }) => {
        // The same list astro.config.mjs derived from product.config.ts, read
        // back off the resolved config rather than loaded a second time: what
        // this checks is the site that was built, not the file it was built
        // from.
        locales = config.i18n?.locales.map((entry) =>
          typeof entry === "string" ? entry : entry.path,
        );
        defaultLocale = config.i18n?.defaultLocale;
        site = config.site === undefined ? undefined : new URL(config.site);
      },
      "astro:build:done": async ({ dir, logger }) => {
        if (site === undefined || locales === undefined) {
          throw new Error(
            "check-hreflang: astro.config.mjs must set `site` and `i18n`. Without them " +
              "there is no origin to resolve an annotation against and no list of " +
              "locales to hold it to.",
          );
        }
        const problems = await findHreflangProblems(fileURLToPath(dir), site, {
          locales,
          defaultLocale,
        });
        if (problems.length > 0) {
          throw new Error(
            `check-hreflang: ${problems.length} problem(s) with the locale annotations ` +
              "in dist/:\n" +
              problems.map((problem) => `  ${problem}`).join("\n"),
          );
        }
        logger.info(
          `every page declares its language, and every hreflang resolves to a page in it`,
        );
      },
    },
  };
}

async function main() {
  const dir = resolve(process.argv[2] ?? "dist");
  const origin = new URL(process.argv[3] ?? "http://localhost:4321");
  const problems = await findHreflangProblems(
    dir,
    origin,
    enabledLocales(await loadProductConfig()),
  );
  if (problems.length > 0) {
    console.error(`check-hreflang: ${problems.length} problem(s) in ${dir}`);
    for (const problem of problems) {
      console.error(`  ${problem}`);
    }
    process.exit(1);
  }
  console.log(`check-hreflang: ${dir} annotates its locales consistently`);
}

// Importable by astro.config.mjs and by the tests without running the CLI.
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  await main();
}
