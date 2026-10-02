import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { checkLocales, compareLocale } from "./check-locales";

const LOCALES_DIR = fileURLToPath(new URL("../locales/", import.meta.url));

describe("checkLocales against the real locales/ directory", () => {
  it("passes for every locale currently shipped", () => {
    const report = checkLocales(LOCALES_DIR);
    expect(report.errors).toEqual([]);
  });
});

describe("compareLocale", () => {
  const enKeys = new Set(["a.one", "a.two", "a.three", "a.four", "a.five"]);

  it("passes when coverage is within the 5% missing threshold", () => {
    const values = new Map([
      ["a.one", "1"],
      ["a.two", "2"],
      ["a.three", "3"],
      ["a.four", "4"],
      ["a.five", "5"],
    ]);
    const result = compareLocale("xx", enKeys, values);
    expect(result.missingKeys).toEqual([]);
    expect(result.missingRatio).toBe(0);
  });

  it("fails once more than 5% of en keys are missing", () => {
    // Deleting 1 of 5 keys is 20% missing, comfortably over the 5% gate --
    // mirrors this task's "deliberately deleting 6% of de keys fails it".
    const values = new Map([
      ["a.one", "1"],
      ["a.two", "2"],
      ["a.three", "3"],
      ["a.four", "4"],
    ]);
    const result = compareLocale("xx", enKeys, values);
    expect(result.missingKeys).toEqual(["a.five"]);
    expect(result.missingRatio).toBeGreaterThan(0.05);
  });

  it("flags a key present in the locale but absent from en as an orphan", () => {
    const values = new Map([
      ["a.one", "1"],
      ["a.two", "2"],
      ["a.three", "3"],
      ["a.four", "4"],
      ["a.five", "5"],
      ["a.six", "an orphan not in en"],
    ]);
    const result = compareLocale("xx", enKeys, values);
    expect(result.orphanKeys).toEqual(["a.six"]);
  });
});

describe("checkLocales end-to-end against a synthetic locale tree", () => {
  it("reports an error for a non-partial locale under the coverage threshold", async () => {
    const { mkdtempSync, writeFileSync, mkdirSync, rmSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const { join } = await import("node:path");

    const root = mkdtempSync(join(tmpdir(), "forge-i18n-check-"));
    try {
      mkdirSync(join(root, "en"));
      mkdirSync(join(root, "de"));
      // 20 keys in en; de deliberately ships only 18 (10% missing, over the
      // 5% gate) plus one orphan key en doesn't have.
      const en: Record<string, string> = {};
      const de: Record<string, string> = {};
      for (let i = 0; i < 20; i++) {
        en[`key_${i}`] = `value ${i}`;
        if (i < 18) de[`key_${i}`] = `wert ${i}`;
      }
      de.orphan_key = "verwaist";
      writeFileSync(join(root, "en", "common.json"), JSON.stringify(en));
      writeFileSync(join(root, "de", "common.json"), JSON.stringify(de));

      const report = checkLocales(root);
      expect(report.errors.some((e) => e.includes("missing"))).toBe(true);
      expect(report.errors.some((e) => e.includes("orphan"))).toBe(true);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("reports an error when an en value is empty", async () => {
    const { mkdtempSync, writeFileSync, mkdirSync, rmSync } = await import("node:fs");
    const { tmpdir } = await import("node:os");
    const { join } = await import("node:path");

    const root = mkdtempSync(join(tmpdir(), "forge-i18n-check-"));
    try {
      mkdirSync(join(root, "en"));
      writeFileSync(join(root, "en", "common.json"), JSON.stringify({ blank: "" }));

      const report = checkLocales(root);
      expect(report.emptyEnKeys).toEqual(["common.blank"]);
      expect(report.errors.some((e) => e.includes("empty"))).toBe(true);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
