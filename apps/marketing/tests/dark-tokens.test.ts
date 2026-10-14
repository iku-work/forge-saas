/**
 * The marketing site's dark mode is CSS only: it ships no JavaScript, so it
 * cannot toggle the `.dark` class the app uses, and follows the OS through a
 * copy of the `.dark` token block under `prefers-color-scheme: dark` in
 * packages/ui/styles/tokens.css. A copy drifts; this keeps the two equal.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const TOKENS = readFileSync(
  join(__dirname, "..", "..", "..", "packages", "ui", "styles", "tokens.css"),
  "utf-8",
).replace(/\r\n/g, "\n");

/** The declarations inside the first block that opens with `opener`. */
function declarations(opener: string): string[] {
  const start = TOKENS.indexOf(opener);
  expect(start, `${opener} not found in tokens.css`).toBeGreaterThanOrEqual(0);
  const body = TOKENS.slice(start + opener.length, TOKENS.indexOf("}", start));
  return body
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split(";")
    .map((declaration) => declaration.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

describe("the CSS-only dark mode", () => {
  it("uses exactly the .dark token values", () => {
    const media = TOKENS.indexOf("@media (prefers-color-scheme: dark) {");
    expect(media).toBeGreaterThanOrEqual(0);
    const mirrored = TOKENS.slice(media);

    const dark = declarations(".dark {\n");
    const start = mirrored.indexOf(':root[data-theme="system"] {');
    const body = mirrored.slice(start, mirrored.indexOf("}", start));
    const copy = body
      .slice(body.indexOf("{") + 1)
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split(";")
      .map((declaration) => declaration.replace(/\s+/g, " ").trim())
      .filter(Boolean);

    expect(copy).toEqual(dark);
    expect(dark.length).toBeGreaterThan(20);
  });
});
