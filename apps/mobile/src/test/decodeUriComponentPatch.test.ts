/**
 * patches/decode-uri-component@0.2.2.patch backports the fixes for
 * GHSA-qr28-fpjm-wq7f and GHSA-vcc3-ghjq-m6fr (denial of service on malformed
 * percent-encoding) into the copy query-string 7 uses under expo-router. The
 * fixed release, 0.5.1, is ESM-only and query-string require()s it, so it
 * cannot simply be installed.
 *
 * Both scanners still see version 0.2.2 and are told the advisories are fixed
 * (.trivyignore.yaml, pnpm-workspace.yaml auditConfig). This test is what
 * makes that true: it loads query-string through the same dependency chain
 * the app bundles and fails if the slow decoder is back, e.g. a lockfile
 * change that resolves an unpatched copy.
 */
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";

interface QueryString {
  parse: (query: string) => Record<string, unknown>;
}

function loadQueryString(): QueryString {
  let load = createRequire(import.meta.url);
  for (const pkg of [
    "expo-router",
    "@react-navigation/native",
    "@react-navigation/core",
  ]) {
    load = createRequire(load.resolve(`${pkg}/package.json`));
  }
  return load("query-string") as QueryString;
}

describe("decode-uri-component patch", () => {
  const queryString = loadQueryString();

  it("decodes many distinct malformed runs in linear time", () => {
    // The advisory's proof of concept: 32,000 distinct runs and a trailing
    // `%zz` that forces the fallback decoder. Unpatched: ~15 s. Patched:
    // ~0.1 s. The bound leaves room for a slow CI runner.
    let payload = "";
    for (let index = 0; index < 32_000; index++) {
      const high = Math.floor(index / 256)
        .toString(16)
        .padStart(2, "0");
      const low = (index % 256).toString(16).padStart(2, "0");
      payload += `%${high}%${low}x`;
    }
    payload += "%zz";

    const start = performance.now();
    queryString.parse(`q=${payload}`);
    expect(performance.now() - start).toBeLessThan(2000);
  });

  it("keeps 0.2.2's behaviour that query-string relies on", () => {
    expect(queryString.parse("a=b+c%20d&e=%C3%A5%zz&f=%FE%FF")).toEqual({
      a: "b c d",
      e: "å%zz",
      f: "\uFFFD\uFFFD",
    });
  });
});
