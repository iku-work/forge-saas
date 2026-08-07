/**
 * T-261, device side. The three properties here are the ones whose failure
 * mode is a leak rather than a broken screen: what is left in secure storage
 * after adopting a session, after ending one, and after the app is killed.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(),
}));
vi.mock("expo-secure-store", () => secureStore);

const client = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  interceptors: {
    request: { use: vi.fn() },
    response: { use: vi.fn() },
  },
}));
vi.mock("@forge/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@forge/api-client")>();
  return { ...actual, createClient: () => client };
});

const {
  IMPERSONATION_SESSION_KEY,
  acceptHandoff,
  clearImpersonationOnLaunch,
  declineHandoff,
  endImpersonation,
  getImpersonationSnapshot,
  previewHandoff,
} = await import("./impersonation");
const { ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, getCachedAccessToken } =
  await import("./api");
const { getSessionSnapshot } = await import("./auth");

const SESSION_EXPIRES_AT = "2026-08-06T12:30:00Z";

function exchangeResponse(accessToken = "impersonation-jwt") {
  return {
    data: {
      data: {
        access_token: accessToken,
        session_expires_at: SESSION_EXPIRES_AT,
        user_email: "jane@example.com",
        reason: "Ticket 8812",
      },
    },
  };
}

describe("impersonation", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    secureStore.getItemAsync.mockResolvedValue(null);
    // The store is a module-level singleton shared by every it() below --
    // reset through the public path rather than by reaching into it.
    await clearImpersonationOnLaunch();
    vi.clearAllMocks();
    secureStore.getItemAsync.mockResolvedValue(null);
  });

  it("previews a handoff without consuming it, and without putting the token in the URL", async () => {
    client.post.mockResolvedValue({
      data: {
        data: {
          user_email: "jane@example.com",
          reason: "Ticket 8812",
          session_expires_at: SESSION_EXPIRES_AT,
        },
      },
    });

    const preview = await previewHandoff("exchange-abc");

    expect(preview.reason).toBe("Ticket 8812");
    // In the body, not the path: the token is a live credential for two
    // minutes, and a URL is the one part of a request that reliably ends up
    // in proxy logs and in Sentry's unscrubbed `request.url`.
    // The url is a fixed path with no interpolation, and the token rides in
    // the body -- that pairing is the assertion.
    expect(client.post).toHaveBeenCalledWith(
      expect.objectContaining({
        url: "/api/v1/admin/impersonate/mobile-handoff/preview",
        body: { token: "exchange-abc" },
      }),
    );
  });

  it("stores the access token and deletes any refresh token when a handoff is accepted", async () => {
    client.post.mockResolvedValue(exchangeResponse());
    secureStore.getItemAsync.mockResolvedValue("impersonation-jwt");

    const session = await acceptHandoff("exchange-abc");

    expect(session.email).toBe("jane@example.com");
    expect(session.expiresAt).toBe(new Date(SESSION_EXPIRES_AT).getTime());
    expect(secureStore.setItemAsync).toHaveBeenCalledWith(
      ACCESS_TOKEN_KEY,
      "impersonation-jwt",
    );
    // The one that matters: an impersonation session has no refresh token of
    // its own, so a leftover one from a previous sign-in would let the 401
    // interceptor silently refresh *that* account back into place the moment
    // the impersonation token expired.
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(REFRESH_TOKEN_KEY);
    expect(getCachedAccessToken()).toBe("impersonation-jwt");
    expect(getImpersonationSnapshot()?.email).toBe("jane@example.com");
  });

  it("marks the session in secure storage so a cold start can find it", async () => {
    client.post.mockResolvedValue(exchangeResponse());

    await acceptHandoff("exchange-abc");

    const stored = secureStore.setItemAsync.mock.calls.find(
      ([key]) => key === IMPERSONATION_SESSION_KEY,
    );
    expect(stored).toBeDefined();
    expect(JSON.parse(String(stored?.[1]))).toMatchObject({
      email: "jane@example.com",
    });
  });

  it("burns the exchange token server-side when a handoff is declined", async () => {
    client.post.mockResolvedValue({ data: { data: null } });

    await declineHandoff("exchange-abc");

    expect(client.post).toHaveBeenCalledWith(
      expect.objectContaining({
        url: "/api/v1/admin/impersonate/mobile-cancel",
        body: { token: "exchange-abc" },
      }),
    );
  });

  it("ends the session as the mobile channel and wipes the device", async () => {
    client.post.mockResolvedValue(exchangeResponse());
    await acceptHandoff("exchange-abc");
    client.post.mockResolvedValue({ data: { data: null } });

    await endImpersonation();

    expect(client.post).toHaveBeenLastCalledWith(
      expect.objectContaining({
        url: "/api/v1/admin/impersonate/end",
        body: { channel: "mobile" },
      }),
    );
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(
      IMPERSONATION_SESSION_KEY,
    );
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(ACCESS_TOKEN_KEY);
    expect(getImpersonationSnapshot()).toBeNull();
  });

  it("still wipes the device when the server call to end fails", async () => {
    client.post.mockResolvedValue(exchangeResponse());
    await acceptHandoff("exchange-abc");
    client.post.mockRejectedValue(new Error("offline"));

    await expect(endImpersonation()).rejects.toThrow("offline");

    // A network error must never leave an operator stuck inside someone
    // else's identity with no way out of it.
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(ACCESS_TOKEN_KEY);
    expect(getImpersonationSnapshot()).toBeNull();
  });

  it("clears an impersonation session found at cold start", async () => {
    secureStore.getItemAsync.mockResolvedValue(
      JSON.stringify({ email: "jane@example.com", expiresAt: 0, reason: "" }),
    );

    const found = await clearImpersonationOnLaunch();

    expect(found).toBe(true);
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(
      IMPERSONATION_SESSION_KEY,
    );
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(ACCESS_TOKEN_KEY);
    expect(getImpersonationSnapshot()).toBeNull();
  });

  it("leaves an ordinary session alone at cold start", async () => {
    secureStore.getItemAsync.mockResolvedValue(null);

    const found = await clearImpersonationOnLaunch();

    expect(found).toBe(false);
    expect(secureStore.deleteItemAsync).not.toHaveBeenCalled();
  });

  it("drops the impersonated user from the session store when the session ends", async () => {
    client.post.mockResolvedValue(exchangeResponse());
    await acceptHandoff("exchange-abc");
    client.post.mockResolvedValue({ data: { data: null } });

    await endImpersonation();

    // Not just the tokens: useSession() has to stop handing out the
    // impersonated identity, or the next navigation re-renders it with the
    // banner already gone -- an operator acting as someone else with nothing
    // on screen saying so.
    expect(getSessionSnapshot().user).toBeNull();
  });
});
