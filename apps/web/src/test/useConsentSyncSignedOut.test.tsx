/**
 * T-308's reconciliation, with nobody signed in.
 *
 * Its own file for the same reason `useSupportContextUnauthenticated.test.tsx`
 * is: `useAuth` caches its bootstrap for the lifetime of a test file, so a
 * case that needs "no session" cannot share one with cases that sign in.
 *
 * This is the branch that matters most for a template. A consent cookie set on
 * the marketing site is visible here as soon as
 * `PUBLIC_CONSENT_COOKIE_DOMAIN` is configured — including to a visitor who
 * has never had an account. Posting it then would mean writing a
 * `consent_record` row with no user to attach it to.
 */
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { CONSENT_COOKIE } from "../lib/consent";
import { useConsentSync } from "../lib/useConsentSync";
import { envelope } from "./appShellFixtures";
import { server } from "./server";

const API = getApiBaseUrl();

describe("useConsentSync with no session (T-308)", () => {
  it("posts nothing, even with a decision in the cookie", async () => {
    const posted: unknown[] = [];
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope(null), { status: 401 }),
      ),
      http.post(`${API}/api/v1/me/consents`, async ({ request }) => {
        const body: unknown = await request.json();
        posted.push(body);
        return HttpResponse.json(envelope({ id: "1" }));
      }),
    );
    document.cookie = `${CONSENT_COOKIE}=${encodeURIComponent("granted|2026-08-30")}; Path=/`;

    renderHook(
      () => {
        useConsentSync();
      },
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <QueryClientProvider client={new QueryClient()}>
            {children}
          </QueryClientProvider>
        ),
      },
    );

    // Long enough that a post would have happened. There is nothing to wait
    // *for* in a test whose subject is an absence.
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(posted).toEqual([]);
  });
});
