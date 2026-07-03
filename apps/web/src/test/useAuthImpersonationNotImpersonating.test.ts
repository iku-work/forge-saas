import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { useAuth } from "@forge/hooks";

import { envelope, fixtureUser } from "./appShellFixtures";
import { server } from "./server";

const API = getApiBaseUrl();

// useAuth() only ever decodes the payload segment client-side (see
// @forge/api-client's getImpersonatorId) -- a real signature is irrelevant
// for this test, only valid base64url JSON in the middle segment matters.
function fakeJwt(payload: Record<string, unknown>): string {
  const segment = btoa(JSON.stringify(payload))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `header.${segment}.signature`;
}

// A dedicated file, not a describe block alongside the impersonating-session
// test in useAuthImpersonationEnd.test.ts -- useAuth's bootstrapPromise/state
// are module-scoped singletons cached for the lifetime of a test file (same
// caveat useModules/useOrg already document), so a second scenario needing a
// different /auth/refresh response must get its own file.
describe("useAuth impersonation state (T-142)", () => {
  it("is not impersonating for an access token with no impersonator_id claim", async () => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: fakeJwt({ sub: "u1" }) })),
      ),
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(fixtureUser)),
      ),
    );

    const { result } = renderHook(() => useAuth());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });
    expect(result.current.isImpersonating).toBe(false);
  });
});
