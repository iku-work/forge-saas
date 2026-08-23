/**
 * The content collections (T-301).
 *
 * Collections rather than a `import.meta.glob` over `src/content/**`, for one
 * reason: a frontmatter mistake becomes a *build failure naming the file*
 * instead of a page that renders "undefined" in production. A blog post
 * without a `publishedAt` cannot be ordered, cannot carry a valid
 * `BlogPosting` (T-303), and cannot be given a `<lastmod>` (T-304), so the
 * build is the right place to find out.
 *
 * Two naming notes for anyone diffing this against the plan:
 *
 * - The plan calls the second collection `docs`; here it is `guides`, matching
 *   the route. `/docs*` is routed to the backend's Swagger UI by the repo-root
 *   Caddyfile, which is why the plan renames the *path* — and a `docs`
 *   collection feeding a `/guides` route is the same confusion one level down.
 *   One name, used everywhere.
 * - The plan says `src/content/config.ts`. That is Astro's pre-5 location;
 *   this version resolves `src/content.config.ts` first and treats the old
 *   path as legacy. Same file, current name.
 *
 * Every collection is keyed by locale in its path -- `<locale>/<slug>.mdx` --
 * so `id` is already `en/hello-world`. T-305 depends on that layout: an
 * untranslated post must be absent from a locale rather than served in
 * English under a `hreflang="fi"` URL, and "absent" is only expressible if
 * the locale is part of the entry's identity.
 */
// `astro/zod` rather than the workspace's own `zod` (v3) or the deprecated
// re-export from `astro:content`: the schemas here are handed to Astro's
// validator, so they have to be built by the exact zod Astro validates with.
// Two zod majors in one workspace, disagreeing about what a schema object is,
// is a failure that would show up as a frontmatter error nobody can explain.
import { z } from "astro/zod";
import { glob } from "astro/loaders";
import { defineCollection } from "astro:content";

/** `<locale>/<slug>`, and for guides `<locale>/<section>/<slug>`. */
const MARKDOWN = "**/*.{md,mdx}";

/**
 * A frontmatter date, and a readable failure when there is not one.
 *
 * Coerced because YAML hands back a `Date` for `2026-08-22` and a string for
 * `"2026-08-22"`, and which one an author wrote is not worth caring about.
 * The custom `error` is what makes that coercion safe to report on:
 * `z.coerce.date()` turns a *missing* field into `new Date(undefined)` -- an
 * Invalid Date, which is still an object -- so the default message is
 * "Expected type date, received object", which tells an author nothing about
 * what to type. This says it.
 */
const frontmatterDate = z.coerce.date({
  error: "is required, and must be a date like 2026-08-22",
});

const blog = defineCollection({
  loader: glob({ pattern: MARKDOWN, base: "./src/content/blog" }),
  schema: z.object({
    title: z.string().min(1),
    description: z.string().min(1),
    publishedAt: frontmatterDate,
    updatedAt: frontmatterDate.optional(),
    author: z.string().min(1),
    tags: z.array(z.string()).default([]),
    // Defaulted rather than optional: `draft` is a two-state thing, and code
    // that reads it should never have to handle a third.
    draft: z.boolean().default(false),
    ogImage: z.string().optional(),
  }),
});

const guides = defineCollection({
  loader: glob({ pattern: MARKDOWN, base: "./src/content/guides" }),
  schema: z.object({
    title: z.string().min(1),
    description: z.string().min(1),
    /** Groups guides in the index. Free text -- ordering across sections is `order`. */
    section: z.string().min(1),
    order: z.number().int(),
    ogImage: z.string().optional(),
  }),
});

const legal = defineCollection({
  loader: glob({ pattern: MARKDOWN, base: "./src/content/legal" }),
  schema: z.object({
    title: z.string().min(1),
    // Required here as it is everywhere else (T-302). These two pages used to
    // reuse their title as their meta description, which is the degenerate
    // form of having none: a search result then shows "Privacy policy" twice
    // and says nothing about whose.
    description: z.string().min(1),
    effectiveDate: frontmatterDate,
  }),
});

export const collections = { blog, guides, legal };
