import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { AdminUserDetail } from "@forge/api-client";

import { AppRoot } from "../../AppRoot";
import { createAppRouter } from "../../router";
import { envelope, makeUser } from "../../test/appShellFixtures";
import { server } from "../../test/server";

const API = getApiBaseUrl();

// useAuth() only ever decodes the payload segment client-side (see
// @forge/api-client's getImpersonatorId) -- a real signature is irrelevant
// here, only valid base64url JSON in the middle segment matters. Mirrors
// test/useAuthImpersonationEnd.test.ts's own local helper.
function fakeJwt(payload: Record<string, unknown>): string {
  const segment = btoa(JSON.stringify(payload))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `header.${segment}.signature`;
}

const superadmin = makeUser(30, {
  email: "root@example.com",
  is_superadmin: true,
});
const target = makeUser(31, { email: "target@example.com", is_superadmin: false });

const targetDetail: AdminUserDetail = {
  id: target.id,
  email: target.email,
  full_name: target.full_name,
  is_active: true,
  is_superadmin: false,
  created_at: "2026-01-01T00:00:00Z",
  memberships: [],
  sessions: [],
  audit_summary: { total_count: 0, recent_actions: [] },
};

// This flow needs the real AppRoot (not test/render.tsx's renderApp(), which
// mounts a bare RouterProvider with a static context.auth.user) -- the
// non-dismissable impersonation banner lives above the route tree in
// AppRoot.tsx, and "end impersonation" navigating back to /admin (AppRoot's
// onEnd handler) only exists on that same component.
function renderFullApp(initialPath: string) {
  const queryClient = new QueryClient();
  const router = createAppRouter(
    queryClient,
    createMemoryHistory({ initialEntries: [initialPath] }),
  );
  const result = render(
    <QueryClientProvider client={queryClient}>
      <AppRoot router={router} queryClient={queryClient} />
    </QueryClientProvider>,
  );
  return { ...result, router };
}

describe("Admin impersonation flow (T-142/T-143)", () => {
  it("starts impersonation from the user detail page, shows the banner, and returns to admin on end", async () => {
    const superadminToken = fakeJwt({ sub: superadmin.id });
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: superadminToken })),
      ),
      http.get(`${API}/api/v1/auth/me`, () => HttpResponse.json(envelope(superadmin))),
      http.get(`${API}/api/v1/admin/users/${target.id}`, () =>
        HttpResponse.json(envelope(targetDetail)),
      ),
      http.post(`${API}/api/v1/admin/users/${target.id}/impersonate`, () =>
        HttpResponse.json(
          envelope({
            access_token: fakeJwt({ sub: target.id, impersonator_id: superadmin.id }),
            expires_at: "2026-01-01T01:00:00Z",
            user: target,
          }),
        ),
      ),
      http.post(`${API}/api/v1/admin/impersonate/end`, () =>
        HttpResponse.json(envelope(null)),
      ),
      // Impersonation lands the browsing session on "/" (the ordinary app);
      // an org-less account just sees the "create your organization" prompt.
      http.get(`${API}/api/v1/orgs`, () => HttpResponse.json(envelope([]))),
      // AdminLayout's nav renders <Link>s to every admin section up front
      // (including this one) -- TanStack Router's default "intent" preload
      // can eagerly warm one of them without a real hover/focus event under
      // jsdom, so this needs a handler even though the test itself never
      // navigates to /admin/users.
      http.get(`${API}/api/v1/admin/users`, () => HttpResponse.json(envelope([]))),
      http.get(`${API}/api/v1/admin/orgs`, () => HttpResponse.json(envelope([]))),
      http.get(`${API}/api/v1/admin/audit`, () => HttpResponse.json(envelope([]))),
      http.get(`${API}/api/v1/admin/dashboard`, () =>
        HttpResponse.json(
          envelope({
            org_count: 0,
            user_count: 0,
            mrr_cents: null,
            recent_signups: [],
            recent_payment_failures: [],
          }),
        ),
      ),
    );

    const { router } = renderFullApp(`/admin/users/${target.id}`);

    const impersonateButton = await screen.findByRole("button", {
      name: "Impersonate",
    });
    await userEvent.click(impersonateButton);

    await screen.findByText(`Impersonate ${target.email}`);
    await userEvent.type(
      screen.getByLabelText("Reason (min. 10 characters)"),
      "Investigating a billing support ticket",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Start impersonating" }),
    );

    // The banner (rendered above the whole router tree in AppRoot.tsx) is
    // the visible proof the access token was replaced.
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(`Impersonating ${target.email}`);

    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/");
    });

    await userEvent.click(
      screen.getByRole("button", { name: "End impersonation" }),
    );

    await waitFor(() => {
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/admin");
    });
  }, 15000);
});
