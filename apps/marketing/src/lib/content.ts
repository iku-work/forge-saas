/**
 * Reading the collections the way the pages want them: per locale, in order,
 * and with drafts already gone (T-301).
 *
 * The locale filtering lives here rather than in each page because the rule
 * has an edge every page would otherwise have to remember -- see
 * `legalDocument` below, which is the one collection that *does* fall back to
 * the default locale, and says why.
 */
import { getCollection, type CollectionEntry } from "astro:content";

import { defaultLocale, enabledLocales, splitEntryId } from "./routing";

export type Collection = "blog" | "guides" | "legal";

export interface Localised<C extends Collection> {
  locale: string;
  /** The path segment(s) under the collection's route: `hello-world`, `deploying/caddy`. */
  slug: string;
  entry: CollectionEntry<C>;
}

/**
 * Drafts render in `astro dev` and are absent from a build.
 *
 * Keyed off the *command*, not off an env var, so there is no way to ship one
 * by forgetting a flag: the same command that builds the site is the one that
 * drops them. Astro replaces this at compile time, so a draft's body never
 * reaches `dist/` at all rather than being hidden in it.
 *
 * **`MODE`, deliberately, and not `import.meta.env.PROD`.** Vite derives `PROD`
 * from `NODE_ENV`, and `NODE_ENV` is set by things that have nothing to do with
 * whether this is a deploy -- `vitest` exports `NODE_ENV=test`, and plenty of
 * CI images export `development`. Under either of those `PROD` is false during
 * `astro build`, and the drafts ship. `MODE` comes from the Vite command
 * (`serve` -> `development`, `build` -> `production`) and only moves when
 * somebody passes `--mode` on purpose. tests/content.test.ts runs its builds
 * with vitest's own `NODE_ENV=test` still in the environment, which is exactly
 * the case that used to be wrong.
 */
function isPublished(entry: CollectionEntry<"blog">): boolean {
  return !(entry.data.draft && import.meta.env.MODE !== "development");
}

/** Entries of a collection that belong to `locale`, with their ids split. */
async function localised<C extends Collection>(
  collection: C,
  locale: string,
): Promise<Localised<C>[]> {
  const entries = await getCollection(collection);
  return entries.flatMap((entry) => {
    const split = splitEntryId(entry.id);
    return split && split.locale === locale
      ? [{ ...split, entry: entry as CollectionEntry<C> }]
      : [];
  });
}

/** Published posts for a locale, newest first. */
export async function blogPosts(locale: string): Promise<Localised<"blog">[]> {
  const posts = await localised("blog", locale);
  return posts
    .filter(({ entry }) => isPublished(entry))
    .sort(
      (a, b) =>
        b.entry.data.publishedAt.getTime() - a.entry.data.publishedAt.getTime(),
    );
}

/** Guides for a locale, in `order`, ties broken by title so the list is stable. */
export async function guides(locale: string): Promise<Localised<"guides">[]> {
  const entries = await localised("guides", locale);
  return entries.sort(
    (a, b) =>
      a.entry.data.order - b.entry.data.order ||
      a.entry.data.title.localeCompare(b.entry.data.title),
  );
}

/** Guides for a locale grouped into their sections, sections ordered by their first guide. */
export async function guideSections(
  locale: string,
): Promise<{ section: string; guides: Localised<"guides">[] }[]> {
  const sections: { section: string; guides: Localised<"guides">[] }[] = [];
  for (const guide of await guides(locale)) {
    const section = sections.find(
      (s) => s.section === guide.entry.data.section,
    );
    if (section) {
      section.guides.push(guide);
    } else {
      sections.push({ section: guide.entry.data.section, guides: [guide] });
    }
  }
  return sections;
}

/**
 * The locales one entry is actually published in, in the product's order.
 *
 * A blog post written only in English exists at exactly one URL, and T-302's
 * `og:alternate_locale` must not claim otherwise -- announcing a German
 * translation of a post nobody translated is the metadata form of a dead
 * link. Drafts are already gone from `blogPosts`, so a post that is published
 * in `en` and still a draft in `de` correctly reports `["en"]`.
 *
 * T-305's `hreflang` links are built from this same list, through <Seo>'s
 * `alternateLocales` -- one answer to "where does this page exist", rather
 * than two that can disagree.
 */
export async function entryLocales(
  collection: "blog" | "guides",
  slug: string,
): Promise<string[]> {
  const found: string[] = [];
  for (const locale of enabledLocales) {
    const entries =
      collection === "blog" ? await blogPosts(locale) : await guides(locale);
    if (entries.some((entry) => entry.slug === slug)) {
      found.push(locale);
    }
  }
  return found;
}

/**
 * The locales a legal document has actually been translated into.
 *
 * Not "the locales it is reachable in", which is every enabled one -- see
 * `legalDocument` below for why this is the collection that falls back. The
 * distinction is exactly what T-305's hreflang cluster needs: `/fi/privacy/`
 * exists, and it is not the Finnish privacy policy, so nothing may annotate
 * it as one.
 */
export async function legalLocales(slug: string): Promise<string[]> {
  const all = await getCollection("legal");
  return enabledLocales.filter((locale) =>
    all.some((entry) => entry.id === `${locale}/${slug}`),
  );
}

/**
 * A legal document for a locale, falling back to the default locale's copy.
 *
 * This is the deliberate exception to T-305's "an untranslated document is not
 * published in that locale" rule, and the reason is that the rule is about
 * *discoverability*: an English blog post at `/fi/blog/x` with a `hreflang=fi`
 * annotation asks a search engine to send Finnish readers to English prose,
 * which is the problem hreflang exists to solve. A privacy policy is not
 * discovered, it is *linked* -- from the footer of every page, from the
 * sign-up form, from an email. Returning a 404 there because nobody has
 * translated the terms yet is a worse answer than showing the authoritative
 * English text with a notice saying so, which is what the page does with the
 * `translated` flag below.
 *
 * The exception is to the *routing* rule only, and T-305 pays for it where
 * crawlers are concerned: a fallback copy is `noindex` and carries no
 * hreflang, so six URLs of the same English text are one indexable document
 * with five reachable translations-in-waiting behind it.
 *
 * `undefined` means the document does not exist in the default locale either,
 * which is a content bug, not a translation gap -- the page turns that into a
 * build failure rather than a silent 404.
 */
export async function legalDocument(
  locale: string,
  slug: string,
): Promise<
  { entry: CollectionEntry<"legal">; translated: boolean } | undefined
> {
  const all = await getCollection("legal");
  const find = (target: string): CollectionEntry<"legal"> | undefined =>
    all.find((entry) => entry.id === `${target}/${slug}`);

  const translated = find(locale);
  if (translated) {
    return { entry: translated, translated: true };
  }
  const fallback = find(defaultLocale);
  return fallback ? { entry: fallback, translated: false } : undefined;
}
