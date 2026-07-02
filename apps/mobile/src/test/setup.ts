import { cleanup } from "@testing-library/react";
import { afterEach, beforeAll, vi } from "vitest";

// vitest.config.ts doesn't set `test.globals: true`, so RTL's automatic
// afterEach cleanup (which detects globals) never registers -- without this,
// each render() in a multi-test file piles onto the previous one's DOM.
afterEach(() => {
  cleanup();
});

// Sane defaults for the two native modules lib/i18n.ts's initI18n() touches
// on every test file (it's awaited unconditionally below, mirroring
// apps/web/src/test/setup.ts's own unconditional `await initI18n()`) --
// jsdom has no equivalent of these the way it has `navigator`/`localStorage`
// for web, so every test file needs *some* mock in place or the shared
// beforeAll itself throws. A test file that cares about detection/sync
// behavior specifically (see src/lib/i18nLocaleDetection.test.ts) declares
// its own `vi.mock` for these same two modules; Vitest resolves a module's
// mock factory from whichever `vi.mock` call was registered last across the
// whole run, so that file's override takes effect before this shared
// beforeAll's initI18n() call below ever resolves a real locale.
vi.mock("expo-localization", () => ({
  getLocales: () => [{ languageCode: "en" }],
  useLocales: () => [{ languageCode: "en" }],
}));
vi.mock("@react-native-async-storage/async-storage", () => ({
  default: {
    getItem: vi.fn().mockResolvedValue(null),
    setItem: vi.fn().mockResolvedValue(undefined),
    removeItem: vi.fn().mockResolvedValue(undefined),
  },
}));

beforeAll(async () => {
  const { initI18n } = await import("../lib/i18n");
  await initI18n();
});
