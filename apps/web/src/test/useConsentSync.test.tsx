/**
 * T-308's reconciliation, from the app's side: an anonymous visitor's
 * analytics decision has to land in `consent_record` once there is an account
 * to attach it to.
 *
 * The parsing lives in `src/lib/consent.ts` and is unit-tested next to it.
 * What this file covers is the part that is only true in a running app: that
 * the post happens exactly once per decision, that a changed decision posts
 * again, and that nothing is posted for a cookie the app cannot read.
 * `consent_record` is append-only (T-032), so "exactly once" is not tidiness
 * — a hook that fired on every mount would write a row per page load.
 *
 * The signed-out case lives in `useConsentSyncSignedOut.test.tsx`, for the
 * reason `useSupportContextUnauthenticated.test.tsx` exists next door:
 * `useAuth` caches its bootstrap for the lifetime of a test *file*, so once
 * any case here has signed in, a later one cannot sign out again.
 */
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { CONSENT_COOKIE } from "../lib/consent";
import { useConsentSync } from "../lib/useConsentSync";
import { envelope, fixtureOrg, makeUser } from "./appShellFixtures";
import { server } from "./server";

const API = getApiBaseUrl();
const user = makeUser(1);
const VERSION = "2026-08-30";

/** Every consent body the app posted during one test. */
let posted: Record<string, unknown>[] = [];

function recordPosts(): void {
  server.use(
    http.post(`${API}/api/v1/me/consents`, async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      posted.push(body);
      return HttpResponse.json(envelope({ id: "1" }));
    }),
  );
}

function signedIn(): void {
  server.use(
    http.post(`${API}/api/v1/auth/refresh`, () =>
      HttpResponse.json(envelope({ access_token: "token" })),
    ),
    http.get(`${API}/api/v1/auth/me`, () => HttpResponse.json(envelope(user))),
    http.get(`${API}/api/v1/orgs`, () =>
      HttpResponse.json(envelope([fixtureOrg])),
    ),
  );
}

function render() {
  const queryClient = new QueryClient();
  return renderHook(
    () => {
      useConsentSync();
    },
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    },
  );
}

function setCookie(value: string): void {
  document.cookie = `${CONSENT_COOKIE}=${encodeURIComponent(value)}; Path=/`;
}

beforeEach(async () => {
  window.localStorage.clear();
  document.cookie = `${CONSENT_COOKIE}=; Max-Age=0; Path=/`;
  // Let the previous test's unmounted hook finish anything it had already
  // started before the ledger is cleared. `useAuth` caches its bootstrap for
  // the lifetime of a test *file* (see appShellFixtures' note), so a hook
  // mounted in an earlier case resolves its user immediately and can post
  // after that case has ended -- which showed up here as a "does nothing"
  // case seeing the previous one's grant.
  await new Promise((resolve) => setTimeout(resolve, 20));
  posted = [];
  recordPosts();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("useConsentSync (T-308)", () => {
  it("posts the cookie's decision once a session exists", async () => {
    signedIn();
    setCookie(`granted|${VERSION}`);

    render();

    await waitFor(() => {
      expect(posted).toHaveLength(1);
    });
    expect(posted[0]).toEqual({
      consent_type: "analytics",
      granted: true,
      version: VERSION,
    });
  });

  it("posts a refusal as faithfully as a grant", async () => {
    // "They said no" is a thing the record has to be able to say. A hook that
    // only reported grants would leave a withdrawal invisible to anyone
    // reading the table.
    signedIn();
    setCookie(`denied|${VERSION}`);

    render();

    await waitFor(() => {
      expect(posted).toHaveLength(1);
    });
    expect(posted[0]).toMatchObject({ granted: false });
  });

  it("does not post twice for the same decision", async () => {
    signedIn();
    setCookie(`granted|${VERSION}`);

    const first = render();
    await waitFor(() => {
      expect(posted).toHaveLength(1);
    });
    first.unmount();

    render();

    // Give a second post every chance to happen before concluding it did not.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(posted).toHaveLength(1);
  });

  it("posts again when the decision changes", async () => {
    signedIn();
    setCookie(`granted|${VERSION}`);
    const first = render();
    await waitFor(() => {
      expect(posted).toHaveLength(1);
    });
    first.unmount();

    setCookie(`denied|${VERSION}`);
    render();

    await waitFor(() => {
      expect(posted).toHaveLength(2);
    });
    expect(posted[1]).toMatchObject({ granted: false });
  });

  it("does nothing without a cookie", async () => {
    // The template's default: no analytics, so no cookie, so nothing to
    // reconcile. This is the branch a fresh clone always takes.
    signedIn();

    render();

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(posted).toEqual([]);
  });

  it("ignores a cookie value it does not understand", async () => {
    // Posting a guessed decision into an append-only table is not something a
    // later write can take back.
    signedIn();
    setCookie("maybe|whenever");

    render();

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(posted).toEqual([]);
  });
});
