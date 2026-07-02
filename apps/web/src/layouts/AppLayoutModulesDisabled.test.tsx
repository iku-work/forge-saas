import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import {
  envelope,
  fixtureConfig,
  fixtureOrg,
  makeMembership,
  makeUser,
} from "../test/appShellFixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

const API = getApiBaseUrl();
const user = makeUser(6);

// Own file (not folded into AppLayout.test.tsx) for the same reason
// AppLayoutMemberRole.test.tsx/AppLayoutNoOrgs.test.tsx are -- useModules's
// bootstrap is a module-scoped singleton promise, so every test in this file
// shares the one /api/v1/config response, which disables billing,
// notifications, and multi-org.
describe("AppLayout / routes (billing, notifications, multi-org disabled)", () => {
  beforeEach(() => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: "token" })),
      ),
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(user)),
      ),
      http.get(`${API}/api/v1/orgs`, () =>
        HttpResponse.json(envelope([fixtureOrg])),
      ),
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([makeMembership(user, "owner")])),
      ),
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            modules: {
              ...fixtureConfig.modules,
              billing: false,
              notifications: false,
              multi_org: false,
            },
          }),
        ),
      ),
    );
  });

  it("hides the Billing and Notifications nav links and the org switcher", async () => {
    renderApp({ initialPath: "/", user });

    expect(
      await screen.findByRole("heading", { name: "Dashboard" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Billing" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Notifications" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Acme Inc")).not.toBeInTheDocument();
  });

  it("is a 404 when navigating directly to /settings/billing", async () => {
    const { router } = renderApp({ initialPath: "/settings/billing", user });

    await waitFor(() => {
      expect(router.state.status).toBe("idle");
    });
    expect(router.state.matches.at(-1)?.status).toBe("notFound");
  });

  it("is a 404 when navigating directly to /settings/notifications", async () => {
    const { router } = renderApp({
      initialPath: "/settings/notifications",
      user,
    });

    await waitFor(() => {
      expect(router.state.status).toBe("idle");
    });
    expect(router.state.matches.at(-1)?.status).toBe("notFound");
  });
});
