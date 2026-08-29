/**
 * T-300's build-output assertions: the module toggle, and the landing page.
 *
 * The claims here are that the landing page is rendered rather than an empty
 * shell, that it costs no JavaScript, that the shared token layer reached the
 * stylesheet, and that `modules.marketing` switches the whole build off.
 * T-301's routes, content and pricing live in their own files beside this one.
 */
import { existsSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { readMarketingEnabled } from "../scripts/guard-enabled.mjs";
import {
  BILLING_DISABLED_FIXTURE,
  DIST,
  MINIMAL_FIXTURE,
  build,
  read,
  readPath,
  scriptTagsOf,
} from "./helpers";

/**
 * The landing page, which T-305 moved from `/` to `/en/`.
 *
 * Every URL carries a locale segment now, the default locale's included, and
 * the file at the root is the locale-less door in front of the six home pages
 * (see tests/routes.test.ts). Spelled once here so the claims below are about
 * the landing page rather than about whichever page happens to be at `/`.
 */
const LANDING = "/en/";

describe("the marketing module toggle", () => {
  // Leaves dist/ matching the committed config, whatever order vitest runs
  // these files in -- the cases below deliberately build against fixtures.
  afterAll(() => {
    build();
  });

  it("reads as enabled from the committed product config", async () => {
    await expect(readMarketingEnabled()).resolves.toBe(true);
  });

  it("reads as disabled from the module matrix's minimal fixture", async () => {
    await expect(readMarketingEnabled(MINIMAL_FIXTURE)).resolves.toBe(false);
  });

  it("builds nothing, and succeeds, when the module is off", () => {
    const output = build({ productConfig: MINIMAL_FIXTURE });

    // Exit 0 is asserted by execFileSync itself: it throws on a non-zero
    // status, and a product that turned marketing off did not ask for a
    // failing build.
    expect(output).toContain("marketing is disabled");
    expect(existsSync(DIST)).toBe(false);
  });

  it("removes a dist/ left behind from before the toggle flipped", () => {
    build();
    expect(existsSync(join(DIST, "en", "index.html"))).toBe(true);

    build({ productConfig: MINIMAL_FIXTURE });

    // A stale site is worse than no site: T-307's Caddy would serve it and
    // T-304's sitemap would list it, both of them describing a product that
    // no longer has a marketing surface.
    expect(existsSync(DIST)).toBe(false);
  });

  it("drops the billing highlight from the landing page when billing is off", () => {
    build({ productConfig: BILLING_DISABLED_FIXTURE });
    const html = readPath(LANDING);

    expect(html).toContain("Accounts and teams");
    expect(html).not.toContain("Subscriptions that reconcile");
  });
});

describe("the built marketing site", () => {
  beforeAll(() => {
    rmSync(DIST, { recursive: true, force: true });
    build();
  });

  it("renders the landing copy into the HTML rather than an empty shell", () => {
    const html = readPath(LANDING);

    // The strings, not a root div: a SPA shell would pass any "the file
    // exists" check while being exactly what P4-A exists to avoid.
    expect(html).toContain("Ship the product, not the plumbing");
    expect(html).toContain("Accounts, teams, billing and an admin console");
    expect(html).toContain("What you start with");
  });

  it("declares the default locale and its direction on <html>", () => {
    expect(readPath(LANDING)).toContain('<html lang="en" dir="ltr"');
  });

  it("costs the visitor zero bytes of JavaScript on the landing page", () => {
    // Held to /en/ rather than to "whatever is at /" since T-305: the root is
    // a redirect, and a redirect legitimately costs a few hundred bytes of
    // script. Pointing this at / would have made T-300's strictest claim pass
    // on a page that was never the landing page.
    expect(scriptTagsOf(readPath(LANDING))).toEqual([]);
  });

  it("renders @forge/ui's button classes as static markup on the landing page", () => {
    const html = readPath(LANDING);

    // buttonVariants() on an <a>: the same classes the React component would
    // emit, with none of the React. This is the island rule's other half --
    // a link needs no JavaScript, so it gets none.
    expect(html).toMatch(/class="[^"]*bg-primary[^"]*text-primary-foreground/);
  });

  it("emits the shared token layer, including classes only @forge/ui uses", () => {
    const stylesheets = readdirSync(join(DIST, "_astro")).filter((file) =>
      file.endsWith(".css"),
    );
    expect(stylesheets).toHaveLength(1);
    const css = readFileSync(join(DIST, "_astro", stylesheets[0]!), "utf-8");

    // --primary proves packages/ui/styles/tokens.css was imported at all;
    // the utilities prove Tailwind scanned the component package, which it
    // does not do for node_modules without the @source in global.css.
    expect(css).toContain("--primary:");
    expect(css).toContain("bg-primary");
    expect(css).toContain("text-primary-foreground");
  });

  it("draws the favicon from primaryColor instead of a second copy of it", () => {
    expect(read("favicon.svg")).toContain("#7c6ff7");
  });

  it("serves a noindex 404 in the default locale", () => {
    const html = read("404.html");

    // One 404 for the whole site: a static server has exactly one file to
    // fall back to, and the path is unmatched precisely because no locale was
    // parsed out of it.
    expect(html).toContain("That page does not exist");
    expect(html).toContain('content="noindex, nofollow"');
  });
});
