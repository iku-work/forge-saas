import { describe, expect, it } from "vitest";

import { DEFAULT_LOCALE, SUPPORTED_LOCALES, loadLocale, t } from "./index";

describe("loadLocale", () => {
  it("loads every supported locale without throwing", () => {
    for (const locale of SUPPORTED_LOCALES) {
      expect(() => loadLocale(locale)).not.toThrow();
    }
  });

  it("rejects an unsupported locale", () => {
    expect(() => loadLocale("de")).toThrow(/not a supported locale/);
  });
});

describe("t()", () => {
  it("resolves a plain key in the default locale", () => {
    expect(t("common.button.save")).toBe("Save");
  });

  it("resolves a plain key directly in a fully-translated locale", () => {
    expect(t("common.button.save", {}, "fi")).toBe("Tallenna");
  });

  it("falls back to the English value when the key is missing in the target locale", () => {
    // billing.json is untranslated in fi (worked-example locale) -- the
    // English string must come back, never the raw key.
    expect(t("billing.upgrade_button", {}, "fi")).toBe("Upgrade");
  });

  it("falls back to the default locale for an unrecognized locale code", () => {
    expect(t("common.button.cancel", {}, "de")).toBe("Cancel");
  });

  it("interpolates a plain {placeholder}", () => {
    expect(t("tenancy.invitation_accept.not_logged_in", { email: "alice@example.com" })).toBe(
      "Log in or create an account with alice@example.com to accept this invitation.",
    );
  });

  it("interpolates ICU plural messages", () => {
    expect(t("notifications.messages.count", { count: 1 })).toBe("1 message");
    expect(t("notifications.messages.count", { count: 5 })).toBe("5 messages");
  });

  it("throws when a key is missing from both the target locale and en", () => {
    expect(() => t("common.does.not.exist")).toThrow(/missing key/);
  });
});

it("DEFAULT_LOCALE is one of SUPPORTED_LOCALES", () => {
  expect(SUPPORTED_LOCALES).toContain(DEFAULT_LOCALE);
});
