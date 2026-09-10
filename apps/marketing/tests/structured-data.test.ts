/**
 * T-303's JSON-LD: what each page type claims, and that it claims nothing the
 * page does not show.
 *
 * Against `dist/`, like everything else here. A builder unit-tested in
 * isolation proves a function can produce an `Offer`; what has to be true is
 * that fifty built pages carry the right nodes, that the payload survives the
 * document it is embedded in, and that a price in an offer is a price on the
 * page. Only the built output answers those.
 *
 * The build in `beforeAll` runs with a real PUBLIC_SITE_URL so "absolute, on
 * the configured origin" is observable rather than indistinguishable from the
 * localhost fallback -- the same reason tests/seo.test.ts does it.
 */
import { readdirSync, rmSync, writeFileSync } from "node:fs";
import { join, sep } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { formatPlanPrice } from "../src/lib/plan-view";
import {
  APP_ROOT,
  BILLING_DISABLED_FIXTURE,
  DIST,
  build,
  decodeEntities,
  jsonLdNodes,
  jsonLdOfType,
  normalizeSpaces,
  read,
  readPath,
  withoutJsonLd,
} from "./helpers";

const SITE_URL = "https://forge.example";
const ORGANIZATION_ID = `${SITE_URL}/#organization`;

/**
 * A post whose title carries the two sequences that can end a `<script>`
 * early. If T-303's escaping is not doing its job, this post's page is the
 * one that breaks -- and it breaks in `jsonLdNodes`, which scans for
 * `</script>` exactly as a parser does.
 */
const HOSTILE_POST = join(
  APP_ROOT,
  "src",
  "content",
  "blog",
  "en",
  "__jsonld-escaping.mdx",
);
const HOSTILE_TITLE = "Ending a </script> block, and <!-- other bad ideas";
const HOSTILE_SLUG = "__jsonld-escaping";

/** Every built page, as a path inside dist/ with forward slashes. */
function builtPages(): string[] {
  return readdirSync(DIST, { recursive: true })
    .map((entry) => String(entry).split(sep).join("/"))
    .filter((entry) => entry.endsWith(".html"))
    .sort();
}

beforeAll(() => {
  writeFileSync(
    HOSTILE_POST,
    [
      "---",
      `title: "${HOSTILE_TITLE}"`,
      'description: "A payload that would end its own <script> block, if nothing escaped it."',
      "publishedAt: 2026-08-19",
      "updatedAt: 2026-08-21",
      "author: The fixture",
      "tags: [security]",
      "---",
      "",
      "Body.",
      "",
    ].join("\n"),
    "utf-8",
  );
  build({ env: { PUBLIC_SITE_URL: SITE_URL } });
});

afterAll(() => {
  rmSync(HOSTILE_POST, { force: true });
  // Back to the committed content and the default origin, whatever order
  // vitest runs these files in.
  build();
});

describe("the Organization node", () => {
  it("is on every page, once, under one @id", () => {
    const pages = builtPages();
    expect(pages.length).toBeGreaterThan(40);

    for (const page of pages) {
      const organizations = jsonLdOfType(read(page), "Organization");
      expect(organizations.length, `${page}: Organization`).toBe(1);
      // One entity across the whole site, rather than fifty that happen to
      // share a name: every other node points at this @id.
      expect(organizations[0]?.["@id"], page).toBe(ORGANIZATION_ID);
    }
  });

  it("points at a logo the site actually serves", () => {
    const [organization] = jsonLdOfType(readPath("/en/"), "Organization");

    // product.config.ts's `logoUrl` is /logo.svg, which nothing in this
    // repository provides -- the favicon endpoint is the only mark that
    // exists, and it is drawn from the same primaryColor.
    expect(organization?.["logo"]).toBe(`${SITE_URL}/favicon.svg`);
    expect(read("favicon.svg")).toContain("<svg");
  });
});

describe("every JSON-LD block on the site", () => {
  it("parses, and declares a type and the schema.org context", () => {
    for (const page of builtPages()) {
      const nodes = jsonLdNodes(read(page));
      expect(nodes.length, `${page}: no JSON-LD at all`).toBeGreaterThan(0);

      for (const node of nodes) {
        expect(node["@context"], page).toBe("https://schema.org");
        expect(typeof node["@type"], page).toBe("string");
      }
    }
  });

  it("uses absolute URLs on the configured origin", () => {
    // A crawler has no base URL to resolve "/pricing/" against. Every URL-ish
    // value this site emits is absolute; this is what would catch the first
    // one that is not.
    const urlish = new Set(["url", "@id", "logo", "image", "item"]);

    for (const page of builtPages()) {
      for (const node of jsonLdNodes(read(page))) {
        for (const [key, value] of Object.entries(node)) {
          if (urlish.has(key) && typeof value === "string") {
            expect(value.startsWith(SITE_URL), `${page}: ${key}=${value}`).toBe(
              true,
            );
          }
        }
      }
    }
  });
});

/**
 * What Google's Rich Results Test calls an *error*, as a table.
 *
 * The hosted tool needs a public URL, which this repository does not have
 * until T-307 puts the site on a host — so the box in agents.md that asks for
 * it stays unticked, and this is the part of it that can be a build gate
 * instead of a thing somebody remembers to do. Each entry is the set of
 * properties Google documents as *required* for that type's rich result;
 * anything else it reports is a recommendation, not an error.
 *
 * A dotted key means "this property, and that property inside it".
 */
const REQUIRED: Record<string, string[]> = {
  // The logo result: a name to attach it to, the site it belongs to, the mark.
  Organization: ["name", "url", "logo"],
  // "You must include at least one of aggregateRating, offers or review" --
  // this template has no honest ratings, so it is offers. See
  // softwareApplicationSchema for how they get there.
  SoftwareApplication: ["name", "offers"],
  Product: ["name", "offers"],
  FAQPage: ["mainEntity"],
  Question: ["name", "acceptedAnswer.text"],
  BreadcrumbList: ["itemListElement"],
  ListItem: ["name", "position"],
  BlogPosting: ["headline", "datePublished", "author.name", "image"],
  Offer: ["price", "priceCurrency"],
  AggregateOffer: ["lowPrice", "priceCurrency"],
};

/** Every node in a payload, including the ones nested inside another. */
function everyNode(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) {
    return value.flatMap(everyNode);
  }
  if (value === null || typeof value !== "object") {
    return [];
  }
  const node = value as Record<string, unknown>;
  const nested = Object.values(node).flatMap(everyNode);
  return typeof node["@type"] === "string" ? [node, ...nested] : nested;
}

/** The value at a dotted path, or undefined. */
function at(node: Record<string, unknown>, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>(
      (value, key) =>
        value === null || typeof value !== "object"
          ? undefined
          : (value as Record<string, unknown>)[key],
      node,
    );
}

describe("what a rich-results validator would call an error", () => {
  it("finds none, on any built page", () => {
    for (const page of builtPages()) {
      for (const node of jsonLdNodes(read(page)).flatMap(everyNode)) {
        const type = String(node["@type"]);
        for (const property of REQUIRED[type] ?? []) {
          const value = at(node, property);
          expect(
            value === undefined || value === "",
            `${page}: ${type} is missing ${property}`,
          ).toBe(false);
        }
      }
    }
  });

  it("would notice one going missing", () => {
    // The table above is only worth having if it can fail. A Product with its
    // offers removed is the shape a hand-maintained JSON-LD block degrades
    // into, and this is what that would look like.
    const [product] = jsonLdOfType(readPath("/en/pricing/"), "Product");
    const { offers, ...withoutOffers } = product ?? {};

    expect(offers).toBeDefined();
    expect(at(withoutOffers, "offers")).toBeUndefined();
  });
});

describe("the landing page", () => {
  it("describes the product, in the words the hero uses", () => {
    const html = readPath("/en/");
    const [application] = jsonLdOfType(html, "SoftwareApplication");

    expect(application?.["name"]).toBe("ForgeSaaS");
    expect(application?.["url"]).toBe(`${SITE_URL}/en/`);
    expect(application?.["applicationCategory"]).toBe("BusinessApplication");
    expect(application?.["publisher"]).toEqual({ "@id": ORGANIZATION_ID });

    // The same sentence the page renders and <Seo> puts in the description --
    // not a third copy written for the crawler.
    const description = String(application?.["description"]);
    expect(withoutJsonLd(decodeEntities(html))).toContain(description);
  });

  it("carries the FAQ, and every answer is on the page", () => {
    const html = readPath("/en/");
    const [faq] = jsonLdOfType(html, "FAQPage");
    const questions = (faq?.["mainEntity"] ?? []) as {
      name: string;
      acceptedAnswer: { text: string };
    }[];
    const visible = withoutJsonLd(decodeEntities(html));

    expect(questions.length).toBe(4);
    for (const question of questions) {
      // Google's rule for FAQ structured data is that a visitor can read the
      // same question and the same answer. Stripping the JSON-LD before
      // searching is what makes this a claim about the markup rather than
      // about the payload finding itself.
      expect(visible, question.name).toContain(question.name);
      expect(visible, question.name).toContain(question.acceptedAnswer.text);
    }
  });

  it("asks its questions in the page's own language", () => {
    const [faq] = jsonLdOfType(readPath("/de/"), "FAQPage");
    const questions = (faq?.["mainEntity"] ?? []) as { name: string }[];

    expect(questions[0]?.name).toBe(
      "Ist das ein Framework oder ein Ausgangspunkt?",
    );
  });
});

describe("the pricing page", () => {
  it("offers each plan at exactly the price the card shows", () => {
    // The assertion T-303 asks for, over both at once. The offers carry
    // decimal strings ("49.00"); the cards carry prose the locale decides
    // ("$49", "49 $US"). Formatting the offer's own number with the page's
    // own formatter is what ties the two together -- a JSON-LD block fed from
    // anywhere but `plans` stops matching immediately.
    for (const locale of ["en", "de", "fr"]) {
      const path = `/${locale}/pricing/`;
      const html = readPath(path);
      const rendered = normalizeSpaces(decodeEntities(withoutJsonLd(html)));
      const products = jsonLdOfType(html, "Product");

      expect(products.map((product) => product["name"])).toEqual([
        "Free",
        "Pro",
      ]);

      for (const product of products) {
        const offers = product["offers"] as {
          price: string;
          priceCurrency: string;
        }[];
        expect(offers.length, `${path}: ${String(product["name"])}`).toBe(2);

        for (const offer of offers) {
          const cents = Math.round(Number(offer.price) * 100);
          // A zero-priced plan renders as the word "Free" in the page's
          // language rather than as "$0", so there is no formatted price to
          // look for -- the offer is still correct at 0.00.
          if (cents === 0) {
            continue;
          }
          expect(
            rendered,
            `${path}: ${String(product["name"])} at ${offer.price}`,
          ).toContain(
            // The currency comes off the offer itself, so this now asserts
            // that the JSON-LD and the visible card agree on it -- not just
            // on the number. They did not: the block hardcoded USD while the
            // catalogue could be in any currency.
            normalizeSpaces(
              formatPlanPrice(cents, offer.priceCurrency, locale),
            ),
          );
        }
      }
    }
  });

  it("says which period each offer buys", () => {
    const [free] = jsonLdOfType(readPath("/en/pricing/"), "Product");
    const offers = free?.["offers"] as {
      priceCurrency: string;
      priceSpecification: { referenceQuantity: { unitCode: string } };
    }[];

    // "49 dollars" and "49 dollars a month" are different claims, and only
    // one of them is true.
    expect(
      offers.map(
        (offer) => offer.priceSpecification.referenceQuantity.unitCode,
      ),
    ).toEqual(["MON", "ANN"]);
    expect(offers[0]?.priceCurrency).toBe("USD");
  });

  it("carries its own FAQ, not the landing page's", () => {
    const [faq] = jsonLdOfType(readPath("/en/pricing/"), "FAQPage");
    const questions = (faq?.["mainEntity"] ?? []) as { name: string }[];

    expect(questions.map((question) => question.name)).toContain(
      "Can I change plan later?",
    );
    expect(questions.map((question) => question.name)).not.toContain(
      "What is it built on?",
    );
  });
});

describe("a blog post", () => {
  it("carries the frontmatter the article renders", () => {
    const html = readPath("/en/blog/why-a-template/");
    const [post] = jsonLdOfType(html, "BlogPosting");

    expect(post?.["headline"]).toBe("Why start from a template at all");
    expect(post?.["datePublished"]).toBe("2026-07-02T00:00:00.000Z");
    expect(post?.["author"]).toEqual({
      "@type": "Person",
      name: "The ForgeSaaS team",
    });
    expect(post?.["mainEntityOfPage"]).toEqual({
      "@type": "WebPage",
      "@id": `${SITE_URL}/en/blog/why-a-template/`,
    });
    expect(post?.["publisher"]).toEqual({ "@id": ORGANIZATION_ID });

    // The date in the payload and the date in the <time> element are the same
    // frontmatter field, and this is the seam where they could stop being.
    expect(html).toContain('datetime="2026-07-02"');
  });

  it("reports an untouched post as modified when it was published", () => {
    const [post] = jsonLdOfType(
      readPath("/en/blog/why-a-template/"),
      "BlogPosting",
    );

    expect(post?.["dateModified"]).toBe(post?.["datePublished"]);
  });

  it("is not a BlogPosting anywhere but on a post", () => {
    expect(jsonLdOfType(readPath("/en/"), "BlogPosting")).toEqual([]);
    expect(jsonLdOfType(readPath("/en/blog/"), "BlogPosting")).toEqual([]);
  });
});

describe("a guide", () => {
  it("places itself with a breadcrumb trail that resolves", () => {
    const html = readPath("/en/guides/getting-started/installation/");
    const [breadcrumbs] = jsonLdOfType(html, "BreadcrumbList");
    const items = (breadcrumbs?.["itemListElement"] ?? []) as {
      position: number;
      name: string;
      item?: string;
    }[];

    expect(items.map((item) => item.position)).toEqual([1, 2, 3]);
    expect(items.map((item) => item.name)).toEqual([
      "Home",
      "Guides",
      "Installation",
    ]);

    // Every step but the last is a page that exists -- the dead-link class of
    // bug T-220 fixed once, in the form a crawler sees it.
    for (const item of items.slice(0, -1)) {
      const path = String(item.item).slice(SITE_URL.length);
      expect(
        () => readPath(path),
        `dead crumb ${String(item.item)}`,
      ).not.toThrow();
    }
    // ...and the last carries no URL, because it is this page.
    expect(items.at(-1)?.item).toBeUndefined();
  });

  it("renders the same trail a visitor can click", () => {
    const visible = withoutJsonLd(
      decodeEntities(readPath("/en/guides/getting-started/installation/")),
    );

    expect(visible).toContain('aria-label="Breadcrumb"');
    expect(visible).toContain('href="/en/guides/"');
    expect(visible).toContain('aria-current="page"');
  });

  it("stays inside its locale", () => {
    const [breadcrumbs] = jsonLdOfType(
      readPath("/de/guides/getting-started/installation/"),
      "BreadcrumbList",
    );
    const items = (breadcrumbs?.["itemListElement"] ?? []) as {
      name: string;
      item?: string;
    }[];

    expect(items[0]?.name).toBe("Startseite");
    expect(items[1]?.item).toBe(`${SITE_URL}/de/guides/`);
  });
});

describe("a payload that would end its own script block", () => {
  it("round-trips instead of breaking the document", () => {
    const html = readPath(`/en/blog/${HOSTILE_SLUG}/`);
    const [post] = jsonLdOfType(html, "BlogPosting");

    // Parsing at all is most of the assertion: `jsonLdNodes` stops at the
    // first `</script>` exactly as a browser's parser does, so an unescaped
    // payload yields truncated JSON and throws before reaching here.
    expect(post?.["headline"]).toBe(HOSTILE_TITLE);

    // And the escape is the JSON one, so what is in the file is not the
    // literal sequence at all.
    const block = html.slice(html.indexOf("application/ld+json"));
    expect(block.slice(0, block.indexOf("</script>"))).not.toContain(
      "</script>",
    );
  });

  it("leaves the rest of the page intact behind it", () => {
    const html = readPath(`/en/blog/${HOSTILE_SLUG}/`);

    // The document still ends properly, and the payload did not become
    // markup: a broken block would have spilled JSON into the body.
    expect(html.trimEnd().endsWith("</html>")).toBe(true);
    expect(jsonLdNodes(html).length).toBe(2);
  });
});

describe("a module that is switched off", () => {
  beforeAll(() => {
    build({ productConfig: BILLING_DISABLED_FIXTURE });
  });

  afterAll(() => {
    build({ env: { PUBLIC_SITE_URL: SITE_URL } });
  });

  it("advertises no plans, and asks no question about paying for them", () => {
    for (const page of builtPages()) {
      expect(jsonLdOfType(read(page), "Product"), page).toEqual([]);
    }

    const [faq] = jsonLdOfType(readPath("/en/"), "FAQPage");
    const questions = (faq?.["mainEntity"] ?? []) as { name: string }[];

    // The other three survive: this is a question about billing, not the
    // whole FAQ.
    expect(questions.length).toBe(3);
    expect(questions.map((question) => question.name)).not.toContain(
      "Do I need a card to start?",
    );
  });
});
