import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(),
}));

vi.mock("expo-secure-store", () => secureStore);

import { ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY } from "./api";
import {
  bootstrapSession,
  getSessionSnapshot,
  login,
  logout,
  register,
  TwoFactorRequiredError,
} from "./auth";

const USER = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "jane@example.com",
  full_name: null,
  avatar_url: null,
  locale: "en",
  email_verified_at: "2026-01-01T00:00:00Z",
  totp_enabled: false,
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apps/mobile/src/lib/auth", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    secureStore.getItemAsync.mockResolvedValue(null);
    secureStore.setItemAsync.mockResolvedValue(undefined);
    secureStore.deleteItemAsync.mockResolvedValue(undefined);
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("login stores tokens and updates the session store with the returned user", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        data: {
          access_token: "access-token",
          refresh_token: "refresh-token",
          user: USER,
        },
        error: null,
      }),
    );

    const user = await login("jane@example.com", "correct-password-123");

    expect(user.email).toBe("jane@example.com");
    expect(secureStore.setItemAsync).toHaveBeenCalledWith(
      ACCESS_TOKEN_KEY,
      "access-token",
    );
    expect(secureStore.setItemAsync).toHaveBeenCalledWith(
      REFRESH_TOKEN_KEY,
      "refresh-token",
    );
    expect(getSessionSnapshot().user?.email).toBe("jane@example.com");
  });

  it("login throws TwoFactorRequiredError when the backend issues a 2FA challenge", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        data: { temp_token: "temp-token" },
        error: null,
      }),
    );

    await expect(login("jane@example.com", "correct-password-123")).rejects.toBeInstanceOf(
      TwoFactorRequiredError,
    );
  });

  it("register calls the register endpoint with the given credentials", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { data: null, error: null }));

    await register("new@example.com", "correct-password-123");

    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.url).toContain("/api/v1/auth/register");
    const body: unknown = await request.clone().json();
    expect(body).toEqual({
      email: "new@example.com",
      password: "correct-password-123",
    });
  });

  it("logout clears stored tokens and the session store even if the request fails", async () => {
    fetchMock.mockRejectedValueOnce(new Error("network down"));

    await logout();

    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(ACCESS_TOKEN_KEY);
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(REFRESH_TOKEN_KEY);
    expect(getSessionSnapshot().user).toBeNull();
  });

  it("bootstrapSession resolves the session from a stored access token via /me", async () => {
    secureStore.getItemAsync.mockImplementation((key: string) =>
      Promise.resolve(key === ACCESS_TOKEN_KEY ? "stored-access-token" : null),
    );
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { data: USER, error: null }));

    const found = await bootstrapSession();

    expect(found).toBe(true);
    expect(getSessionSnapshot()).toEqual({ user: USER, isLoading: false });
  });

  it("bootstrapSession resolves to a signed-out session when no token is stored", async () => {
    const found = await bootstrapSession();

    expect(found).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(getSessionSnapshot()).toEqual({ user: null, isLoading: false });
  });
});
