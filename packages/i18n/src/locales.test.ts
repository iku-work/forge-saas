import { describe, expect, it } from "vitest";

import { LOCALES, localeMeta, SUPPORTED_LOCALES } from "./locales";

describe("LOCALES registry", () => {
  it("has one entry per SUPPORTED_LOCALES code, in order", () => {
    expect(LOCALES.map((locale) => locale.code)).toEqual([...SUPPORTED_LOCALES]);
  });

  it("gives every locale a non-empty native name distinct from its code", () => {
    for (const locale of LOCALES) {
      expect(locale.nativeName.length).toBeGreaterThan(0);
      expect(locale.nativeName).not.toBe(locale.code);
    }
  });

  it("defaults every current locale to ltr (T-231 adds the first rtl locale)", () => {
    for (const locale of LOCALES) {
      expect(locale.dir).toBe("ltr");
    }
  });
});

describe("localeMeta", () => {
  it("looks up a known locale", () => {
    expect(localeMeta("fi")?.dir).toBe("ltr");
  });

  it("returns undefined for an unknown code", () => {
    expect(localeMeta("xx")).toBeUndefined();
  });
});
