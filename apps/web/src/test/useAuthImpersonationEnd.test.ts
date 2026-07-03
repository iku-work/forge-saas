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

describe("useAuth.endImpersonation (T-142)", () => {
  it("revokes the token server-side then restores the session from the refresh cookie", async () => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(
          envelope({
            access_token: fakeJwt({ sub: "u1", impersonator_id: "admin-1" }),
          }),
        ),
      ),
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(fixtureUser)),
      ),
    );

    const { result } = renderHook(() => useAuth());
    await waitFor(() => {
      expect(result.current.isImpersonating).toBe(true);
    });

    let endCalled = false;
    server.use(
      http.post(`${API}/api/v1/admin/impersonate/end`, () => {
        endCalled = true;
        return HttpResponse.json(envelope(null));
      }),
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: fakeJwt({ sub: "u1" }) })),
      ),
    );

    await result.current.endImpersonation();

    expect(endCalled).toBe(true);
    await waitFor(() => {
      expect(result.current.isImpersonating).toBe(false);
    });
  });
});
