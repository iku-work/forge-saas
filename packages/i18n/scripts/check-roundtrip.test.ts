import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { checkRoundtrip } from "./check-roundtrip";
import { renderNamespaceFile } from "./i18n-transfer";

const REAL_LOCALES_DIR = fileURLToPath(new URL("../locales/", import.meta.url));

describe("checkRoundtrip against the real locales/ directory", () => {
  it("finds zero diffs -- en's own export -> import is lossless", () => {
    const report = checkRoundtrip(REAL_LOCALES_DIR);
    expect(report.diffs).toEqual([]);
  });
});

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "forge-i18n-roundtrip-"));
  mkdirSync(join(root, "en"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe("checkRoundtrip against a synthetic tree", () => {
  it("catches a namespace file whose formatting doesn't match JSON.stringify(obj, null, 2)", () => {
    writeFileSync(join(root, "en", "common.json"), '{"greeting":"Hello"}');
    const report = checkRoundtrip(root);
    expect(report.diffs).toEqual(["locales/en/common.json changed after an export -> import round-trip"]);
  });

  it("passes for a properly formatted namespace file", () => {
    writeFileSync(join(root, "en", "common.json"), renderNamespaceFile({ greeting: "Hello" }));
    const report = checkRoundtrip(root);
    expect(report.diffs).toEqual([]);
  });
});
