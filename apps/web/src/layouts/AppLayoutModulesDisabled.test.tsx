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
  // `public_api` and `outbound_webhooks` are already false in fixtureConfig
  // (they ship off), so T-313's and T-322's surfaces are covered here by the
  // *committed* configuration rather than by an override -- which is the
  // configuration a template user actually gets.
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

  it("hides the API keys and Webhooks nav links (T-313, T-322)", async () => {
    renderApp({ initialPath: "/", user });

    expect(
      await screen.findByRole("heading", { name: "Dashboard" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "API keys" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Webhooks" }),
    ).not.toBeInTheDocument();
  });

  it("is a 404 when navigating directly to /settings/api-keys", async () => {
    const { router } = renderApp({ initialPath: "/settings/api-keys", user });

    await waitFor(() => {
      expect(router.state.status).toBe("idle");
    });
    expect(router.state.matches.at(-1)?.status).toBe("notFound");
  });

  it("is a 404 when navigating directly to /settings/webhooks", async () => {
    const { router } = renderApp({ initialPath: "/settings/webhooks", user });

    await waitFor(() => {
      expect(router.state.status).toBe("idle");
    });
    expect(router.state.matches.at(-1)?.status).toBe("notFound");
  });
});
