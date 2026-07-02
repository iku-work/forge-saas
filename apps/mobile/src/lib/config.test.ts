import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// config.ts imports ./api.ts for its `client` instance, which imports this
// native module -- mocked the same way as auth.test.ts even though this
// file never calls into it directly.
vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn().mockResolvedValue(null),
  setItemAsync: vi.fn().mockResolvedValue(undefined),
  deleteItemAsync: vi.fn().mockResolvedValue(undefined),
}));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const CONFIG_BODY = {
  data: {
    app_name: "ForgeSaaS",
    primary_color: "#7c6ff7",
    logo_url: "/logo.svg",
    support_email: "support@example.com",
    modules: {
      billing: true,
      notifications: true,
      file_storage: true,
      feature_flags: true,
      gdpr: true,
      admin: true,
      oauth: true,
      totp_2fa: true,
      multi_org: true,
      usage_metering: false,
      outbound_webhooks: false,
    },
  },
  error: null,
};

describe("apps/mobile/src/lib/config", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.resetModules();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("denies every optional module before the config request resolves", async () => {
    const { getModulesSnapshot } = await import("./config");

    expect(getModulesSnapshot()).toEqual({
      billing: false,
      notifications: false,
      fileStorage: false,
      featureFlags: false,
      gdpr: false,
      admin: false,
      oauth: false,
      totp2fa: false,
      multiOrg: false,
      usageMetering: false,
      outboundWebhooks: false,
    });
  });

  it("bootstrapModules resolves the active modules reported by GET /api/v1/config", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, CONFIG_BODY));
    const { bootstrapModules, getModulesSnapshot } = await import("./config");

    await bootstrapModules();

    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.url).toContain("/api/v1/config");
    expect(getModulesSnapshot()).toEqual({
      billing: true,
      notifications: true,
      fileStorage: true,
      featureFlags: true,
      gdpr: true,
      admin: true,
      oauth: true,
      totp2fa: true,
      multiOrg: true,
      usageMetering: false,
      outboundWebhooks: false,
    });
  });

  it("bootstrapModules stays denied-by-default when the request fails", async () => {
    fetchMock.mockRejectedValueOnce(new Error("network down"));
    const { bootstrapModules, getModulesSnapshot } = await import("./config");

    await bootstrapModules();

    expect(getModulesSnapshot().billing).toBe(false);
  });

  it("bootstrapModules only fetches once across concurrent callers", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, CONFIG_BODY));
    const { bootstrapModules } = await import("./config");

    await Promise.all([bootstrapModules(), bootstrapModules()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
