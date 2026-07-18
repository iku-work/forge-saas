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

  it("defaults every locale to ltr except ar, the first rtl locale (T-231)", () => {
    for (const locale of LOCALES) {
      expect(locale.dir).toBe(locale.code === "ar" ? "rtl" : "ltr");
    }
  });
});

describe("localeMeta", () => {
  it("looks up a known locale", () => {
    expect(localeMeta("fi")?.dir).toBe("ltr");
  });

  it("looks up the rtl locale (T-231)", () => {
    expect(localeMeta("ar")?.dir).toBe("rtl");
  });

  it("returns undefined for an unknown code", () => {
    expect(localeMeta("xx")).toBeUndefined();
  });
});
