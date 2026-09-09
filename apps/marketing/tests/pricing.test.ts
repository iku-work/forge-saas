/**
 * T-301's pricing page: the island, and the two places its data can come from.
 *
 * The fallback is the interesting half. A static site cannot query the billing
 * database at request time, so the build fetches the catalogue once — and the
 * page has to still exist when that fetch does not work, because "the API was
 * restarting during a deploy" must not mean "the pricing page has no prices".
 */
import { createServer, type Server } from "node:http";
import { type AddressInfo } from "node:net";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  build,
  buildAsync,
  normalizeSpaces,
  readPath,
  scriptTagsOf,
} from "./helpers";

/** The catalogue the stub API serves — deliberately unlike the committed one. */
const LIVE_PLANS = [
  {
    id: "6f2b6b1e-0f2a-4d4a-9a6a-2f8f0f7a1c11",
    slug: "solo",
    name: "Solo",
    price_monthly: 1200,
    price_yearly: 12000,
    features: { seats: 1, storage_gb: 5 },
  },
  {
    id: "6f2b6b1e-0f2a-4d4a-9a6a-2f8f0f7a1c12",
    slug: "studio",
    name: "Studio",
    price_monthly: 9900,
    price_yearly: 99000,
    features: { seats: 50, advanced_reports: true, custom_metric: 7 },
  },
];

describe("the pricing page, built from committed data", () => {
  beforeAll(() => {
    // No PUBLIC_API_URL: the normal case for a fresh checkout and for CI,
    // neither of which has a backend running during the frontend build.
    build();
  });

  it("hydrates a @forge/ui component as an island", () => {
    const html = readPath("/en/pricing/");

    // ADR-0004's constraint 1, checked rather than asserted in prose: Radix
    // tabs, imported straight from @forge/ui, hydrating with the package
    // carrying no "use client" directives and no Astro-specific wrapper.
    // This assertion used to live on T-300's island-check.astro; the pricing
    // toggle is the real island that replaced it.
    expect(html).toContain("<astro-island");
    expect(html).toMatch(/component-export="PricingPlans"/);
    expect(scriptTagsOf(html).length).toBeGreaterThan(0);
  });

  it("defers that hydration until the island is on screen (T-306)", () => {
    // `client="visible"`, not `client="load"`, and the directive is worth
    // pinning: this island is React plus every Radix primitive @forge/ui
    // re-exports, 367 kB of it, and on `client:load` that download starts
    // immediately and competes with the stylesheet. Measured, on this page:
    // first paint 1.1 s -> 2.2 s and Largest Contentful Paint 3.4 s, against
    // a 2.5 s budget the other three pages clear at 1.1 s.
    expect(readPath("/en/pricing/")).toContain('client="visible"');
  });

  it("renders both billing periods into the HTML before anything hydrates", () => {
    const html = readPath("/en/pricing/");

    // A visitor whose JavaScript never arrives still sees prices. The yearly
    // panel is in the document too -- Radix hides it, it is not absent.
    expect(html).toContain("$49");
    expect(html).toContain("$490");
    expect(html).toContain("Save 17%");
  });

  it("lists a plan's features from its own data, in a fixed order", () => {
    const html = readPath("/en/pricing/");
    const seats = html.indexOf("25 team members");
    const storage = html.indexOf("50 GB of storage");
    const calls = html.indexOf("100,000 API calls per month");

    expect(seats).toBeGreaterThan(-1);
    expect(seats).toBeLessThan(storage);
    expect(storage).toBeLessThan(calls);
    expect(html).toContain("Advanced reporting");
  });

  it("formats prices for the page's own locale", () => {
    // Same cents, four renderings. This is the whole reason the page is built
    // per locale rather than once with a client-side formatter.
    //
    // normalizeSpaces because Intl puts a no-break space between the number
    // and the symbol, and a narrow one in fr -- see the helper.
    expect(normalizeSpaces(readPath("/fr/pricing/"))).toContain("49 $US");
    expect(normalizeSpaces(readPath("/de/pricing/"))).toContain("49 $");
    expect(readPath("/ar/pricing/")).toContain("مجاني");
    expect(readPath("/en/pricing/")).toContain("Free");
  });

  it("says on the page that the prices came from the repository", () => {
    // An operator looking at a wrong price can tell "the build could not
    // reach the API" from "the database says this" without a build log that
    // has long since scrolled away.
    expect(readPath("/en/pricing/")).toContain(
      "These prices were published with the site",
    );
  });
});

describe("the build-time plan fetch", () => {
  let server: Server;
  let origin: string;
  const requested: string[] = [];

  beforeAll(async () => {
    server = createServer((request, response) => {
      requested.push(request.url ?? "");
      response.writeHead(200, { "content-type": "application/json" });
      response.end(
        JSON.stringify({ data: LIVE_PLANS, meta: { request_id: "stub" } }),
      );
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    origin = `http://127.0.0.1:${String((server.address() as AddressInfo).port)}`;
  });

  afterAll(async () => {
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    // Leave dist/ matching the committed config for whatever runs next.
    build();
  });

  it("renders the API's catalogue, not the committed one", async () => {
    await buildAsync({ env: { PUBLIC_API_URL: origin } });
    const html = readPath("/en/pricing/");

    expect(requested).toContain("/api/v1/billing/plans");
    expect(html).toContain("Solo");
    expect(html).toContain("Studio");
    expect(html).toContain("$99");

    // The committed catalogue's prices, not its plan *names* -- "Pro" is a
    // substring of "Product", the footer heading, so a name check here would
    // pass for the wrong reason and fail for another.
    expect(html).not.toContain("$490");
    expect(html).not.toContain("100,000 API calls per month");

    // Fetched data is current data, so the staleness notice is not shown.
    expect(html).not.toContain("These prices were published with the site");
  });

  it("labels a feature key it has no translation for rather than dropping it", () => {
    // `features` is open JSON from the plan row. A bullet that silently
    // vanished would let a plan advertise less than it sells.
    expect(readPath("/en/pricing/")).toContain("custom_metric: 7");
  });

  it("falls back to committed data, and still builds, when the API is unreachable", async () => {
    // Port 1 refuses immediately -- a deploy where the API is simply not
    // there, which is the case the fallback exists for.
    const output = await buildAsync({
      env: { PUBLIC_API_URL: "http://127.0.0.1:1" },
    });

    expect(output).toContain("could not fetch plans");
    const html = readPath("/en/pricing/");
    expect(html).toContain("Pro");
    expect(html).toContain("These prices were published with the site");
  });
});
