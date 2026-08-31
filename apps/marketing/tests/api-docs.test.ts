/**
 * T-314's published API reference, as it actually ships.
 *
 * The claim this page makes is "a newcomer can create a key and make a
 * successful call using only this page", and every part of that claim is a
 * property of the built HTML rather than of a component: the header names,
 * the scope each endpoint needs, the rate-limit headers, the error envelope,
 * and the versioning policy all have to survive the build into `dist/`.
 *
 * It also guards the committed snapshot (`src/content/api-reference.json`),
 * which is what renders when the backend is unreachable -- the normal case,
 * since the committed product config has `public_api` off. A snapshot that
 * drifted into containing an internal route would publish it to the world
 * with nobody noticing, so the negative assertion is here too.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { beforeAll, describe, expect, it } from "vitest";

import { APP_ROOT, build, read } from "./helpers";

const PAGE = "/en/guides/api/index.html";

/** The page's prose, with tags and entity noise flattened out. */
function textOf(page: string): string {
  return read(page)
    .replace(/<[^>]+>/g, " ")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ");
}

describe("the published API reference", () => {
  let text = "";

  beforeAll(() => {
    build();
    text = textOf(PAGE);
  });

  it("renders the document's opening line as prose, not as a heading", () => {
    // It is the text before the first `##`, so it has no heading of its own.
    // Rendering it with one would put the opening sentence in an <h2>; a
    // sloppier bug puts `[object Object]` there, which is how this assertion
    // earned its place.
    expect(text).toContain("Every endpoint an API key can call, and nothing else.");
    expect(text).not.toContain("[object Object]");
  });

  it("tells a newcomer how to authenticate", () => {
    expect(text).toContain("Authorization: Bearer fk_live_");
    expect(text).toContain("X-Org-ID");
  });

  it("names every scope in the vocabulary", () => {
    for (const scope of [
      "orgs:read",
      "members:read",
      "members:write",
      "files:read",
    ]) {
      expect(text).toContain(scope);
    }
  });

  it("documents the rate-limit headers and the error envelope", () => {
    expect(text).toContain("X-RateLimit-Limit");
    expect(text).toContain("Retry-After");
    expect(text).toContain("request_id");
  });

  it("states the versioning and deprecation policy", () => {
    expect(text).toContain("90 days");
    expect(text.toLowerCase()).toContain("breaking");
  });

  it("lists each endpoint with the scope it needs", () => {
    expect(text).toContain("GET /api/v1/orgs/{org_id}/members");
    expect(text).toContain("Requires the members:read scope");
  });

  it("renders no leftover Markdown emphasis", () => {
    // The source is Markdown (it is an OpenAPI `description`), and this page
    // has no Markdown renderer -- so `**bold**` and `backticks` are stripped
    // rather than shown to a reader as punctuation.
    const body = read(PAGE).split("<article")[1] ?? "";
    expect(body).not.toContain("**");
  });

  it("publishes only the scoped routes, never an internal one", () => {
    const snapshot: unknown = JSON.parse(
      readFileSync(
        join(APP_ROOT, "src", "content", "api-reference.json"),
        "utf-8",
      ),
    );
    const paths = Object.keys(
      (snapshot as { paths: Record<string, unknown> }).paths,
    );
    expect(paths).toEqual([
      "/api/v1/orgs/{org_id}",
      "/api/v1/orgs/{org_id}/members",
    ]);
    for (const forbidden of [
      "/api/v1/admin",
      "/api/v1/api-keys",
      "/api/v1/auth",
      "/api/v1/webhooks",
    ]) {
      expect(JSON.stringify(snapshot)).not.toContain(forbidden);
    }
  });

  it("is reachable from the guides index, not only by URL", () => {
    // The page is a route rather than a content entry (it is generated from
    // the live document), so the index's collection-driven list cannot find
    // it. A published contract nobody can navigate to is a contract nobody
    // reads.
    const index = read("/en/guides/index.html");
    expect(index).toContain('data-testid="guides-api-link"');
    // Trailing slash: `localePath` builds directory-style URLs, which is what
    // the sitemap and the hreflang cluster also emit.
    expect(index).toContain('href="/en/guides/api/"');
  });

  it("exists in every enabled locale", () => {
    for (const locale of ["en", "de", "fr"]) {
      expect(() => read(`/${locale}/guides/api/index.html`)).not.toThrow();
    }
  });
});
