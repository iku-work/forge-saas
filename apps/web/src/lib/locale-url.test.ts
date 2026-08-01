import { describe, expect, it } from "vitest";

import {
  isLocaleShaped,
  localeOf,
  resolvePreferredLocale,
  stripLocale,
  withLocale,
} from "./locale-url";

describe("isLocaleShaped", () => {
  it.each(["en", "fi", "de", "xx", "zz", "pt-BR"])(
    "treats %s as a locale slot",
    (segment) => {
      expect(isLocaleShaped(segment)).toBe(true);
    },
  );

  // The whole legacy-URL redirect rule rests on these being distinguishable
  // from an unknown locale -- see routes/$locale.tsx.
  it.each(["login", "settings", "admin", "accept-invitation", "EN", "e", "e1"])(
    "treats %s as an ordinary path segment",
    (segment) => {
      expect(isLocaleShaped(segment)).toBe(false);
    },
  );
});

describe("localeOf", () => {
  it("reads a supported locale off the front of the path", () => {
    expect(localeOf("/de/settings/profile")).toBe("de");
    expect(localeOf("/en")).toBe("en");
  });

  it("returns undefined for locale-less and unknown-locale paths", () => {
    expect(localeOf("/settings/profile")).toBeUndefined();
    expect(localeOf("/xx/settings")).toBeUndefined();
    expect(localeOf("/")).toBeUndefined();
  });
});

describe("stripLocale", () => {
  it("removes the locale segment", () => {
    expect(stripLocale("/de/settings/profile")).toBe("/settings/profile");
    expect(stripLocale("/en")).toBe("/");
    expect(stripLocale("/en/")).toBe("/");
  });

  it("leaves a locale-less path alone", () => {
    expect(stripLocale("/settings/profile")).toBe("/settings/profile");
    expect(stripLocale("/xx/settings")).toBe("/xx/settings");
  });
});

describe("withLocale", () => {
  it("swaps an existing locale segment", () => {
    expect(withLocale("/de/settings/profile", "fi")).toBe(
      "/fi/settings/profile",
    );
  });

  it("prefixes a locale-less path", () => {
    expect(withLocale("/settings/profile", "fi")).toBe("/fi/settings/profile");
    expect(withLocale("/", "fi")).toBe("/fi");
  });

  // This is the switcher's "never loses the page the user is on" guarantee.
  it("preserves query and hash", () => {
    expect(withLocale("/en/verify-email?token=abc#top", "de")).toBe(
      "/de/verify-email?token=abc#top",
    );
    expect(withLocale("/reset-password?token=abc", "de")).toBe(
      "/de/reset-password?token=abc",
    );
  });

  it("does not mistake an unknown locale for one it can replace", () => {
    expect(withLocale("/xx/settings", "de")).toBe("/de/xx/settings");
  });
});

describe("resolvePreferredLocale", () => {
  it("prefers the signed-in user's stored preference", () => {
    expect(
      resolvePreferredLocale({
        userLocale: "fi",
        storedLocale: "de",
        browserLocales: ["fr-FR"],
      }),
    ).toBe("fi");
  });

  it("falls back to an explicit in-session choice, then the browser", () => {
    expect(
      resolvePreferredLocale({ storedLocale: "de", browserLocales: ["fr-FR"] }),
    ).toBe("de");
    expect(resolvePreferredLocale({ browserLocales: ["fr-FR"] })).toBe("fr");
  });

  it("skips tiers naming a locale the registry does not have", () => {
    expect(
      resolvePreferredLocale({
        userLocale: "xx",
        storedLocale: "yy",
        browserLocales: ["zz", "de-AT"],
      }),
    ).toBe("de");
  });

  it("defaults to en when nothing matches", () => {
    expect(resolvePreferredLocale({ browserLocales: ["zz"] })).toBe("en");
    expect(resolvePreferredLocale({})).toBe("en");
  });
});
