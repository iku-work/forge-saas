/**
 * T-300's build-output assertions.
 *
 * Everything here is about `dist/`, because everything here is a claim about
 * what ships: that the landing page is rendered rather than an empty shell,
 * that it costs no JavaScript, that a @forge/ui component still hydrates on a
 * page that asks for it, and that the module toggle switches the whole build
 * off. None of those can be checked by rendering a component in isolation.
 */
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { beforeAll, describe, expect, it } from "vitest";

import { readMarketingEnabled } from "../scripts/guard-enabled.mjs";

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST = join(APP_ROOT, "dist");
const GUARD = join(APP_ROOT, "scripts", "guard-enabled.mjs");
const MINIMAL_FIXTURE = resolve(
  APP_ROOT,
  "..",
  "..",
  "backend",
  "tests",
  "fixtures",
  "module_configs",
  "minimal.config.ts",
);
const BILLING_DISABLED_FIXTURE = join(
  APP_ROOT,
  "tests",
  "fixtures",
  "billing-disabled.config.ts",
);

/** Runs the build the way package.json does -- through the guard, never around it. */
function build(productConfig?: string): string {
  return execFileSync(process.execPath, [GUARD, "astro", "build"], {
    cwd: APP_ROOT,
    encoding: "utf-8",
    env: envWith(productConfig),
  });
}

/** The committed config is the *absence* of the override, not an override pointing back at it. */
function envWith(productConfig?: string): NodeJS.ProcessEnv {
  const env = { ...process.env };
  if (productConfig) {
    env["FORGE_PRODUCT_CONFIG"] = productConfig;
  } else {
    delete env["FORGE_PRODUCT_CONFIG"];
  }
  return env;
}

function read(page: string): string {
  return readFileSync(join(DIST, page), "utf-8");
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
function scriptTagsOf(html: string): string[] {
  return [
    ...html.matchAll(/<script\b[^>]*>[\s\S]*?<\/script>|<script\b[^>]*\/>/g),
  ]
    .map((match) => match[0])
    .filter((tag) => !/type\s*=\s*["']application\/ld\+json["']/i.test(tag));
}

// Declared before the enabled build on purpose: this case deletes dist/, so
// running it first leaves the working tree with a real, complete build in
// place rather than the wreckage of a skipped one.
describe("the marketing module toggle", () => {
  it("reads as enabled from the committed product config", async () => {
    await expect(readMarketingEnabled()).resolves.toBe(true);
  });

  it("reads as disabled from the module matrix's minimal fixture", async () => {
    await expect(readMarketingEnabled(MINIMAL_FIXTURE)).resolves.toBe(false);
  });

  it("builds nothing, and succeeds, when the module is off", () => {
    const output = build(MINIMAL_FIXTURE);

    // Exit 0 is asserted by execFileSync itself: it throws on a non-zero
    // status, and a product that turned marketing off did not ask for a
    // failing build.
    expect(output).toContain("marketing is disabled");
    expect(existsSync(DIST)).toBe(false);
  });

  it("removes a dist/ left behind from before the toggle flipped", () => {
    build();
    expect(existsSync(join(DIST, "index.html"))).toBe(true);

    build(MINIMAL_FIXTURE);

    // A stale site is worse than no site: T-307's Caddy would serve it and
    // T-304's sitemap would list it, both of them describing a product that
    // no longer has a marketing surface.
    expect(existsSync(DIST)).toBe(false);
  });
});

// Also before the committed-config build, so the last thing this file does is
// leave dist/ matching product.config.ts rather than a fixture.
describe("the landing page's module awareness", () => {
  it("drops the billing highlight when the billing module is off", () => {
    build(BILLING_DISABLED_FIXTURE);
    const html = read("index.html");

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
    const html = read("index.html");

    // The strings, not a root div: a SPA shell would pass any "the file
    // exists" check while being exactly what P4-A exists to avoid.
    expect(html).toContain("Ship the product, not the plumbing");
    expect(html).toContain("Accounts, teams, billing and an admin console");
    expect(html).toContain("What you start with");
  });

  it("declares the default locale and its direction on <html>", () => {
    expect(read("index.html")).toContain('<html lang="en" dir="ltr"');
  });

  it("costs the visitor zero bytes of JavaScript on /", () => {
    expect(scriptTagsOf(read("index.html"))).toEqual([]);
  });

  it("renders @forge/ui's button classes as static markup on /", () => {
    const html = read("index.html");

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

  it("hydrates a @forge/ui component as an island where a page asks for one", () => {
    const html = read(join("island-check", "index.html"));

    // ADR-0004's constraint 1, checked rather than asserted in prose: this
    // island imports @forge/ui directly, and the package carries no
    // "use client" directives and no Astro-specific wrapper.
    expect(html).toContain("<astro-island");
    expect(html).toContain('client="load"');
    expect(html).toMatch(/component-export="IslandCheck"/);
    expect(scriptTagsOf(html).length).toBeGreaterThan(0);
  });

  it("draws the favicon from primaryColor instead of a second copy of it", () => {
    expect(read("favicon.svg")).toContain("#7c6ff7");
  });
});
