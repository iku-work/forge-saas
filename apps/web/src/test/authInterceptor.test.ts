import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  authRetryInterceptor,
  getAccessToken,
  onSessionExpired,
  setAccessToken,
} from "@forge/api-client";

const REFRESH_URL = "http://localhost:8000/api/v1/auth/refresh";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// authRetryInterceptor only reads `serializedBody` off the options object,
// so a minimal stand-in is enough -- constructing a real, fully-typed
// ResolvedRequestOptions isn't worth it for a unit test of this function.
const fakeOptions = { serializedBody: undefined } as unknown as Parameters<
  typeof authRetryInterceptor
>[2];

describe("authRetryInterceptor", () => {
  beforeEach(() => {
    setAccessToken("old-token");
  });

  it("refreshes the token and transparently retries on 401", async () => {
    // Both call sites in authInterceptor.ts always pass a plain string URL
    // (never a Request/URL object), so the mock is typed to match real usage.
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url.includes("/auth/refresh")) {
        expect(init?.method).toBe("POST");
        return Promise.resolve(
          jsonResponse(200, {
            data: { access_token: "new-token" },
            error: null,
            meta: { request_id: "req-1" },
          }),
        );
      }
      return Promise.resolve(
        jsonResponse(200, { data: { ok: true }, error: null }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const request = new Request("http://localhost:8000/api/v1/auth/me", {
      headers: { Authorization: "Bearer old-token" },
    });
    const original401 = new Response(null, { status: 401 });

    const result = await authRetryInterceptor(
      original401,
      request,
      fakeOptions,
    );

    expect(result.status).toBe(200);
    expect(getAccessToken()).toBe("new-token");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(REFRESH_URL);

    const retryHeaders = fetchMock.mock.calls[1]?.[1]?.headers as Headers;
    expect(retryHeaders.get("Authorization")).toBe("Bearer new-token");

    vi.unstubAllGlobals();
  });

  it("propagates the original 401 and notifies listeners when refresh fails", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const listener = vi.fn();
    const unsubscribe = onSessionExpired(listener);

    const request = new Request("http://localhost:8000/api/v1/orgs");
    const original401 = new Response(null, { status: 401 });

    const result = await authRetryInterceptor(
      original401,
      request,
      fakeOptions,
    );

    expect(result).toBe(original401);
    expect(getAccessToken()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    vi.unstubAllGlobals();
  });

  it("does not attempt a refresh for the refresh endpoint's own 401", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const request = new Request(REFRESH_URL, { method: "POST" });
    const original401 = new Response(null, { status: 401 });

    const result = await authRetryInterceptor(
      original401,
      request,
      fakeOptions,
    );

    expect(result).toBe(original401);
    expect(fetchMock).not.toHaveBeenCalled();

    vi.unstubAllGlobals();
  });

  it("shares a single refresh call across concurrent 401s", async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/auth/refresh")) {
        return Promise.resolve(
          jsonResponse(200, {
            data: { access_token: "new-token" },
            error: null,
          }),
        );
      }
      return Promise.resolve(
        jsonResponse(200, { data: { ok: true }, error: null }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const requestA = new Request("http://localhost:8000/api/v1/me");
    const requestB = new Request("http://localhost:8000/api/v1/orgs");
    const response401 = new Response(null, { status: 401 });

    await Promise.all([
      authRetryInterceptor(response401, requestA, fakeOptions),
      authRetryInterceptor(response401, requestB, fakeOptions),
    ]);

    const refreshCalls = fetchMock.mock.calls.filter(([url]) =>
      url.includes("/auth/refresh"),
    );
    expect(refreshCalls).toHaveLength(1);

    vi.unstubAllGlobals();
  });
});
