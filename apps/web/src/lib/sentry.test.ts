import { afterEach, describe, expect, it, vi } from "vitest";

const sentry = vi.hoisted(() => ({
  init: vi.fn(),
  setUser: vi.fn(),
}));
vi.mock("@sentry/react", () => sentry);

const { initSentry, setSentryUser } = await import("./sentry");

afterEach(() => {
  vi.unstubAllEnvs();
  sentry.init.mockClear();
  sentry.setUser.mockClear();
});

describe("initSentry", () => {
  it("does not initialize the SDK when no DSN is configured", () => {
    vi.stubEnv("VITE_SENTRY_DSN", "");

    initSentry();

    expect(sentry.init).not.toHaveBeenCalled();
  });

  it("turns off every category of personal data the SDK would collect", () => {
    vi.stubEnv("VITE_SENTRY_DSN", "https://public@example.com/1");

    initSentry();

    expect(sentry.init).toHaveBeenCalledWith(
      expect.objectContaining({
        dataCollection: {
          userInfo: false,
          cookies: false,
          httpHeaders: false,
          httpBodies: [],
          urlQueryParams: false,
          genAI: { inputs: false, outputs: false },
          databaseQueryData: false,
        },
      }),
    );
  });
});

describe("setSentryUser", () => {
  it("forwards only the id -- never email or name", () => {
    setSentryUser({ id: "user-1" });

    expect(sentry.setUser).toHaveBeenCalledWith({ id: "user-1" });
  });
});
