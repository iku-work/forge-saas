import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// support.ts -> ./api.ts -> expo-secure-store, whose native module doesn't
// exist under jsdom (same mock as config.test.ts/auth.test.ts).
vi.mock("expo-secure-store", () => ({
  getItemAsync: vi.fn().mockResolvedValue(null),
  setItemAsync: vi.fn().mockResolvedValue(undefined),
  deleteItemAsync: vi.fn().mockResolvedValue(undefined),
}));

const linking = vi.hoisted(() => ({
  canOpenURL: vi.fn().mockResolvedValue(true),
  openURL: vi.fn().mockResolvedValue(undefined),
}));
// Partial mock: the shared test setup initialises src/lib/i18n.ts, which
// reaches for react-native's I18nManager (see FilesScreen.test.tsx for the
// same pattern).
vi.mock("react-native", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-native")>();
  return { ...actual, Linking: { ...actual.Linking, ...linking } };
});

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function configBody(overrides: Record<string, unknown> = {}): unknown {
  return {
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
        marketing: true,
        public_api: false,
        enterprise_sso: false,
      },
      support_tool: "none",
      support_url: "mailto:support@example.com",
      ...overrides,
    },
    error: null,
  };
}

const ORGS_BODY = {
  data: [{ id: "org-1", name: "Acme Inc", slug: "acme", logo_url: null }],
  error: null,
};

const CONTEXT_BODY = {
  data: {
    user_id: "user-1",
    email: "jane@example.com",
    full_name: "Jane Doe",
    org_id: "org-1",
    org_name: "Acme Inc",
    plan: "pro",
    subscription_status: "active",
    signup_at: "2026-01-01T00:00:00Z",
    locale: "en",
    is_impersonating: false,
  },
  error: null,
};

// Routes by URL rather than call order: openSupport fires /config, /orgs and
// /me/support-context, and only the first two are strictly sequential.
function routeFetch(
  responses: { config?: unknown; context?: unknown } = {},
): ReturnType<typeof vi.fn> {
  return vi.fn((request: Request) => {
    if (request.url.includes("/api/v1/config")) {
      return Promise.resolve(
        jsonResponse(200, responses.config ?? configBody()),
      );
    }
    if (request.url.includes("/api/v1/orgs")) {
      return Promise.resolve(jsonResponse(200, ORGS_BODY));
    }
    if (request.url.includes("/me/support-context")) {
      const body = responses.context;
      return Promise.resolve(
        body === null
          ? jsonResponse(500, { data: null, error: { code: "internal_error" } })
          : jsonResponse(200, body ?? CONTEXT_BODY),
      );
    }
    throw new Error(`unexpected request: ${request.url}`);
  });
}

describe("apps/mobile/src/lib/support (T-262)", () => {
  beforeEach(() => {
    vi.resetModules();
    linking.canOpenURL.mockResolvedValue(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("opens the support mailto with the user's account context attached", async () => {
    vi.stubGlobal("fetch", routeFetch());
    const { openSupport } = await import("./support");

    const opened = await openSupport("Support request");

    expect(opened).toBe(true);
    const url = linking.openURL.mock.calls[0]?.[0] as string;
    expect(url.startsWith("mailto:support@example.com?")).toBe(true);
    const params = new URLSearchParams(url.split("?")[1]);
    expect(params.get("subject")).toBe("Support request");
    expect(params.get("body")).toContain("jane@example.com");
    expect(params.get("body")).toContain("Acme Inc (org-1)");
    expect(params.get("body")).toContain("Plan: pro (active)");
  });

  it("still opens support when the context request fails", async () => {
    // The user who can't reach the API is exactly the one who needs support.
    vi.stubGlobal("fetch", routeFetch({ context: null }));
    const { openSupport } = await import("./support");

    const opened = await openSupport();

    expect(opened).toBe(true);
    expect(linking.openURL).toHaveBeenCalledWith("mailto:support@example.com");
  });

  it("opens a configured help-centre URL unchanged, without appending context", async () => {
    vi.stubGlobal(
      "fetch",
      routeFetch({
        config: configBody({
          support_tool: "crisp",
          support_url: "https://help.example.com",
        }),
      }),
    );
    const { openSupport } = await import("./support");

    await openSupport("Support request");

    expect(linking.openURL).toHaveBeenCalledWith("https://help.example.com");
  });

  it("lets a product-registered handler take over and suppress the URL", async () => {
    vi.stubGlobal("fetch", routeFetch());
    const { openSupport, setSupportHandler } = await import("./support");
    const handler = vi.fn().mockResolvedValue(true);
    setSupportHandler(handler);

    const opened = await openSupport();

    expect(opened).toBe(true);
    expect(linking.openURL).not.toHaveBeenCalled();
    expect(handler.mock.calls[0]?.[0]).toMatchObject({
      userId: "user-1",
      orgName: "Acme Inc",
      isImpersonating: false,
    });
    expect(handler.mock.calls[0]?.[1]).toEqual({
      tool: "none",
      url: "mailto:support@example.com",
    });
  });

  it("falls back to the URL when the handler declines", async () => {
    vi.stubGlobal("fetch", routeFetch());
    const { openSupport, setSupportHandler } = await import("./support");
    setSupportHandler(vi.fn().mockResolvedValue(false));

    const opened = await openSupport();

    expect(opened).toBe(true);
    expect(linking.openURL).toHaveBeenCalled();
  });

  it("reports failure rather than opening nothing when the device can't open the URL", async () => {
    vi.stubGlobal("fetch", routeFetch());
    linking.canOpenURL.mockResolvedValue(false);
    const { openSupport } = await import("./support");

    const opened = await openSupport();

    expect(opened).toBe(false);
    expect(linking.openURL).not.toHaveBeenCalled();
  });

  it("reports failure when /config never resolved a support URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("network down")),
    );
    const { openSupport } = await import("./support");

    const opened = await openSupport();

    expect(opened).toBe(false);
    expect(linking.openURL).not.toHaveBeenCalled();
  });
});
