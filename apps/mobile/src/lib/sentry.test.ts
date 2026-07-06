import { afterEach, describe, expect, it, vi } from "vitest";

const sentry = vi.hoisted(() => ({
  init: vi.fn(),
  setUser: vi.fn(),
}));
vi.mock("@sentry/react-native", () => sentry);
vi.mock("expo-constants", () => ({
  default: { expoConfig: { version: "1.2.3" } },
}));

const updates = vi.hoisted(() => ({ updateId: null as string | null }));
vi.mock("expo-updates", () => updates);

const { initSentry, setSentryUser } = await import("./sentry");

afterEach(() => {
  vi.unstubAllEnvs();
  sentry.init.mockClear();
  sentry.setUser.mockClear();
  updates.updateId = null;
});

describe("initSentry", () => {
  it("does not initialize the SDK when no DSN is configured", () => {
    vi.stubEnv("EXPO_PUBLIC_SENTRY_DSN", "");

    initSentry();

    expect(sentry.init).not.toHaveBeenCalled();
  });

  it("tags the release with the app version and embedded fallback when no OTA update is active", () => {
    vi.stubEnv("EXPO_PUBLIC_SENTRY_DSN", "https://public@example.com/1");

    initSentry();

    expect(sentry.init).toHaveBeenCalledWith(
      expect.objectContaining({ release: "1.2.3+embedded" }),
    );
  });

  it("tags the release with the active OTA update id once one is running", () => {
    vi.stubEnv("EXPO_PUBLIC_SENTRY_DSN", "https://public@example.com/1");
    updates.updateId = "8f2b1e7a-0000-4000-8000-000000000000";

    initSentry();

    expect(sentry.init).toHaveBeenCalledWith(
      expect.objectContaining({
        release: "1.2.3+8f2b1e7a-0000-4000-8000-000000000000",
      }),
    );
  });
});

describe("setSentryUser", () => {
  it("forwards only the id -- never email or name", () => {
    setSentryUser({ id: "user-1" });

    expect(sentry.setUser).toHaveBeenCalledWith({ id: "user-1" });
  });

  it("clears the user on null", () => {
    setSentryUser(null);

    expect(sentry.setUser).toHaveBeenCalledWith(null);
  });
});
