import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import {
  envelope,
  fixtureOrg,
  makeMembership,
  makeUser,
} from "../test/appShellFixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

const API = getApiBaseUrl();
const user = makeUser(1);

describe("AppLayout (owner, one org)", () => {
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
    );
  });

  it("renders the dashboard with the org switcher and full nav for an owner", async () => {
    renderApp({ initialPath: "/", user });

    expect(
      await screen.findByRole("heading", { name: "Dashboard" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Acme Inc")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Billing" })).toBeInTheDocument();
  });

  it("renders settings pages nested under the guarded layout", async () => {
    renderApp({ initialPath: "/settings/members", user });

    expect(
      await screen.findByRole("heading", { name: "Members" }),
    ).toBeInTheDocument();
  });

  it("renders the billing settings page when the billing module is enabled", async () => {
    renderApp({ initialPath: "/settings/billing", user });

    expect(
      await screen.findByRole("heading", { name: "Billing" }),
    ).toBeInTheDocument();
  });

  it("renders the Advanced reports nav item once the advanced_reports flag resolves true", async () => {
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/feature-flags`, () =>
        HttpResponse.json(envelope({ flags: { advanced_reports: true } })),
      ),
    );

    renderApp({ initialPath: "/", user });

    expect(
      await screen.findByRole("link", { name: "Advanced reports" }),
    ).toBeInTheDocument();
  });

  it("hides the Advanced reports nav item when the flag is disabled (the default handler)", async () => {
    renderApp({ initialPath: "/", user });

    // Wait for a link that's always present once the shell resolves, so the
    // absence check below isn't just catching an early/loading render.
    await screen.findByRole("link", { name: "Billing" });
    expect(
      screen.queryByRole("link", { name: "Advanced reports" }),
    ).not.toBeInTheDocument();
  });
});
