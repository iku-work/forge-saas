import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { TRANSLATION_KEYS } from "../src/keys";
import { collectTranslationKeys } from "./generate-types";

const localesDir = (locale: string): string =>
  fileURLToPath(new URL(`../locales/${locale}/`, import.meta.url));

describe("generated keys.ts", () => {
  it("matches the keys currently present in locales/en/", () => {
    // If this fails, locales/en/*.json changed without re-running
    // `pnpm --filter @forge/i18n run generate-types` -- exactly the
    // staleness check this package's "Done when" criteria require of CI.
    expect([...TRANSLATION_KEYS].sort()).toEqual(collectTranslationKeys());
  });
});

describe("locale JSON files", () => {
  it("parse as valid JSON for every namespace in every shipped locale", () => {
    for (const locale of ["en", "fi"]) {
      const dir = localesDir(locale);
      const files = readdirSync(dir).filter((file) => file.endsWith(".json"));
      expect(files.sort()).toEqual([
        "auth.json",
        "billing.json",
        "common.json",
        "emails.json",
        "errors.json",
        "files.json",
        "notifications.json",
        "tenancy.json",
        "validation.json",
      ]);
      for (const file of files) {
        expect(() => JSON.parse(readFileSync(`${dir}${file}`, "utf-8"))).not.toThrow();
      }
    }
  });
});
