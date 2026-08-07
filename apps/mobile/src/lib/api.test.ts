import { meApiV1AuthMeGet } from "@forge/api-client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(),
}));

vi.mock("expo-secure-store", () => secureStore);

import {
  ACCESS_TOKEN_KEY,
  client,
  getCachedAccessToken,
  hydrateAccessTokenCache,
  IMPERSONATION_SESSION_KEY,
  REFRESH_TOKEN_KEY,
} from "./api";
// Imported for its side effect as much as its exports: T-261's module
// registers an onSessionExpired listener at load, exactly as the root layout
// causes it to in the real app.
import { acceptHandoff, getImpersonationSnapshot } from "./impersonation";

const OLD_ACCESS_TOKEN = "old-access-token";
const OLD_REFRESH_TOKEN = "old-refresh-token";
const NEW_ACCESS_TOKEN = "new-access-token";
const NEW_REFRESH_TOKEN = "new-refresh-token";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apps/mobile/src/lib/api", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    secureStore.getItemAsync.mockImplementation((key: string) => {
      if (key === ACCESS_TOKEN_KEY) return OLD_ACCESS_TOKEN;
      if (key === REFRESH_TOKEN_KEY) return OLD_REFRESH_TOKEN;
      return null;
    });
    secureStore.setItemAsync.mockResolvedValue(undefined);
    secureStore.deleteItemAsync.mockResolvedValue(undefined);

    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    // Simulates the cold-start hydration apps/mobile/app/_layout.tsx
    // triggers via auth.ts's bootstrapSession() before any request fires.
    await hydrateAccessTokenCache();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("hydrates the in-memory access token from secure store", () => {
    expect(getCachedAccessToken()).toBe(OLD_ACCESS_TOKEN);
  });

  it("on a 401, refreshes via the stored refresh token and retries the original request", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(401, { data: null, error: { code: "unauthorized" } }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          data: {
            access_token: NEW_ACCESS_TOKEN,
            refresh_token: NEW_REFRESH_TOKEN,
          },
          error: null,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, {
          data: {
            id: "11111111-1111-1111-1111-111111111111",
            email: "alice@example.com",
            full_name: null,
            avatar_url: null,
            locale: "en",
            email_verified_at: null,
            totp_enabled: false,
          },
          error: null,
        }),
      );

    const { data: envelope } = await meApiV1AuthMeGet({
      client,
      throwOnError: true,
    });

    expect(fetchMock).toHaveBeenCalledTimes(3);

    const calls = fetchMock.mock.calls as unknown as [
      string | Request,
      RequestInit?,
    ][];
    const refreshUrl = calls[1]?.[0];
    const refreshInit = calls[1]?.[1];
    expect(refreshUrl).toContain("/api/v1/auth/refresh");
    const refreshBody: unknown = JSON.parse(refreshInit?.body as string);
    expect(refreshBody).toEqual({ refresh_token: OLD_REFRESH_TOKEN });

    const retryHeaders = new Headers(calls[2]?.[1]?.headers);
    expect(retryHeaders.get("Authorization")).toBe(
      `Bearer ${NEW_ACCESS_TOKEN}`,
    );
    expect(retryHeaders.get("X-Auth-Retried")).toBe("1");

    expect(envelope.data?.email).toBe("alice@example.com");
    expect(getCachedAccessToken()).toBe(NEW_ACCESS_TOKEN);
    expect(secureStore.setItemAsync).toHaveBeenCalledWith(
      ACCESS_TOKEN_KEY,
      NEW_ACCESS_TOKEN,
    );
    expect(secureStore.setItemAsync).toHaveBeenCalledWith(
      REFRESH_TOKEN_KEY,
      NEW_REFRESH_TOKEN,
    );
  });

  it("clears stored tokens when the refresh itself fails", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(401, { data: null, error: { code: "unauthorized" } }),
      )
      .mockResolvedValueOnce(
        jsonResponse(401, { data: null, error: { code: "session_invalid" } }),
      );

    await meApiV1AuthMeGet({ client });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(ACCESS_TOKEN_KEY);
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(REFRESH_TOKEN_KEY);
    // T-261: and the impersonation flag, which is stored beside them. A wipe
    // that left it behind would make the next cold start read the operator's
    // own session as an impersonation one and discard it.
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(
      IMPERSONATION_SESSION_KEY,
    );
    expect(getCachedAccessToken()).toBeNull();
  });

  it("does not treat a 401 from a handoff leg as the session ending", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(401, {
        data: null,
        error: { message_key: "errors.invalid_exchange_token" },
      }),
    );

    const { response } = await client.post({
      url: "/api/v1/admin/impersonate/mobile-handoff/preview",
      body: { token: "expired" },
    });

    // An expired QR code says nothing about this device's own session: the
    // 401 comes straight back, with no refresh attempt and no token wipe --
    // and so no forced redirect to /login over the screen that was about to
    // say "this link has expired".
    expect(response?.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(secureStore.deleteItemAsync).not.toHaveBeenCalled();
  });

  it("takes an impersonation session down completely when its token expires", async () => {
    // An impersonation session never has a refresh token, so the first 401
    // after its access token expires is immediately terminal.
    secureStore.getItemAsync.mockImplementation((key: string) =>
      key === ACCESS_TOKEN_KEY ? "impersonation-jwt" : null,
    );
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, {
        data: {
          access_token: "impersonation-jwt",
          session_expires_at: "2026-08-06T12:30:00Z",
          user_email: "jane@example.com",
          reason: "Ticket 8812",
        },
      }),
    );
    await acceptHandoff("exchange-abc");
    expect(getImpersonationSnapshot()).not.toBeNull();

    fetchMock.mockResolvedValueOnce(
      jsonResponse(401, { data: null, error: { code: "unauthorized" } }),
    );
    await meApiV1AuthMeGet({ client });

    // Otherwise the red banner keeps counting down "00:00 left" over the login
    // screen the operator was just bounced to.
    expect(getImpersonationSnapshot()).toBeNull();
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(
      IMPERSONATION_SESSION_KEY,
    );
  });
});
