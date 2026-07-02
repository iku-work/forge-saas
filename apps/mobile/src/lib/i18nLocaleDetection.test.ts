import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Overrides src/test/setup.ts's own default ("en") registration for these
// two modules -- Vitest resolves a module id to whichever `vi.mock` factory
// was registered last across the run, and this file's hoisted call runs
// after setup.ts's, so the shared `beforeAll(() => initI18n())` it also
// registers observes *this* file's "fi" device locale instead.
const localization = vi.hoisted(() => ({
  locales: [{ languageCode: "fi" }] as { languageCode: string | null }[],
}));
vi.mock("expo-localization", () => ({
  getLocales: () => localization.locales,
  useLocales: () => localization.locales,
}));

const asyncStorage = vi.hoisted(() => ({
  store: new Map<string, string>(),
}));
vi.mock("@react-native-async-storage/async-storage", () => ({
  default: {
    getItem: vi.fn((key: string) =>
      Promise.resolve(asyncStorage.store.get(key) ?? null),
    ),
    setItem: vi.fn((key: string, value: string) => {
      asyncStorage.store.set(key, value);
      return Promise.resolve();
    }),
    removeItem: vi.fn((key: string) => {
      asyncStorage.store.delete(key);
      return Promise.resolve();
    }),
  },
}));

const { applyProfileLocale, useSystemLocaleSync } = await import("./i18n");
const i18next = (await import("i18next")).default;

describe("apps/mobile/src/lib/i18n locale detection (T-070)", () => {
  beforeEach(() => {
    asyncStorage.store.clear();
  });

  // setup.ts's shared beforeAll already called initI18n() once for this
  // file, using the "fi" device locale mocked above -- this is exactly the
  // first-launch scenario (no AsyncStorage entry yet, see the cleared store
  // in beforeEach), so asserting here confirms it took effect.
  it("detects the device locale on first launch", () => {
    expect(i18next.language).toBe("fi");
  });

  it("a signed-in user's profile locale overrides the device default", async () => {
    expect(i18next.language).toBe("fi");

    await applyProfileLocale("en");

    expect(i18next.language).toBe("en");
  });

  it("re-renders when the OS language changes while the app is running", async () => {
    await i18next.changeLanguage("en");
    localization.locales = [{ languageCode: "en" }];
    const { rerender } = renderHook(() => {
      useSystemLocaleSync();
    });

    localization.locales = [{ languageCode: "fi" }];
    rerender();

    await vi.waitFor(() => {
      expect(i18next.language).toBe("fi");
    });
  });
});
