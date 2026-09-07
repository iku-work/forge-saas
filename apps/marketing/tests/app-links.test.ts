/**
 * Every link into the app is built from `PUBLIC_APP_URL`, which falls back to
 * this repo's local Caddy host. That fallback is correct for a dev build and
 * silently wrong for a real one, and `appLink()` is the header's Sign in, the
 * hero's Register and Log in, and the pricing CTA -- so forgetting the
 * variable points every conversion path at the visitor's own machine, on a
 * site that otherwise looks perfect.
 *
 * That is not hypothetical: it shipped on this template's first production
 * deploy and was found by a human clicking Sign in.
 *
 * Fixture directories rather than a real build, for the reason
 * tests/budget.test.ts gives for the same shape -- a correct build cannot
 * demonstrate a checker that catches incorrect ones.
 */
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { findLocalLinks } from "../scripts/check-app-links.mjs";

let dir: string;

function page(path: string, body: string): void {
  const full = join(dir, path);
  mkdirSync(join(full, ".."), { recursive: true });
  writeFileSync(full, `<html><body>${body}</body></html>`, "utf8");
}

function fixture(): string {
  dir = mkdtempSync(join(tmpdir(), "forge-app-links-"));
  return dir;
}

afterEach(() => {
  if (dir) rmSync(dir, { recursive: true, force: true });
});

describe("check-app-links", () => {
  it("catches the exact failure that shipped: appLink falling back to localhost", async () => {
    fixture();
    page("en/index.html", '<a href="https://localhost/en/login">Sign in</a>');

    const problems = await findLocalLinks(dir);

    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("/en/index.html");
    expect(problems[0]).toContain("https://localhost/en/login");
  });

  it("catches the .localhost hosts docker-compose serves marketing on", async () => {
    fixture();
    page("de/index.html", '<a href="//marketing.localhost/de/">Marketing</a>');

    expect(await findLocalLinks(dir)).toHaveLength(1);
  });

  it("catches loopback addresses, not just the name", async () => {
    fixture();
    page("en/index.html", '<img src="http://127.0.0.1:9000/logo.png">');

    expect(await findLocalLinks(dir)).toHaveLength(1);
  });

  /**
   * The reason this reads attributes and never text. A guide showing
   * `curl http://localhost:8000/health` is documentation, and flagging it
   * would make the gate something people switch off.
   */
  it("ignores localhost in prose and code samples", async () => {
    fixture();
    page(
      "en/guide/index.html",
      "<pre><code>curl http://localhost:8000/health</code></pre>" +
        "<p>Run the dev server on http://localhost:5173 to try it.</p>",
    );

    expect(await findLocalLinks(dir)).toEqual([]);
  });

  it("passes a correctly configured production build", async () => {
    fixture();
    page(
      "en/index.html",
      '<a href="https://app.mycalendar.party/en/login">Sign in</a>' +
        '<link rel="stylesheet" href="/_astro/styles.css">' +
        '<a href="/en/pricing">Pricing</a>',
    );

    expect(await findLocalLinks(dir)).toEqual([]);
  });

  it("reports every offending page, not just the first", async () => {
    fixture();
    page("en/index.html", '<a href="https://localhost/en/login">Sign in</a>');
    page("fi/index.html", '<a href="https://localhost/fi/register">Aloita</a>');

    const problems = await findLocalLinks(dir);

    expect(problems).toHaveLength(2);
    expect(problems.join("\n")).toContain("/fi/index.html");
  });
});
