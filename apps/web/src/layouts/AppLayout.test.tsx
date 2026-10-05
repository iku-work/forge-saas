import { http, HttpResponse } from "msw";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("renders the dashboard with the org switcher and the primary nav for an owner", async () => {
    renderApp({ initialPath: "/", user });

    expect(
      await screen.findByRole("heading", { name: "Dashboard" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Acme Inc")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-primary-nav")).toBeInTheDocument();
    expect(screen.getByTestId("nav-dashboard")).toBeInTheDocument();
  });

  it("keeps the settings pages out of the sidebar outside settings", async () => {
    // The sidebar shows one group at a time now. Billing is the check because
    // it is owner-only and module-gated, so its absence here has to come from
    // the mode rather than from a gate.
    renderApp({ initialPath: "/", user });

    await screen.findByTestId("sidebar-primary-nav");
    expect(
      screen.queryByTestId("sidebar-settings-nav"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Billing" }),
    ).not.toBeInTheDocument();
  });

  it("swaps the sidebar to the settings group on a settings route", async () => {
    renderApp({ initialPath: "/settings/billing", user });

    expect(
      await screen.findByRole("heading", { name: "Billing" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-settings-nav")).toBeInTheDocument();
    expect(screen.queryByTestId("sidebar-primary-nav")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Profile" })).toBeInTheDocument();
  });

  it("offers a way back to the app from settings", async () => {
    // Settings is its own mode and its sidebar shows no Dashboard link, so
    // this is the first way home, beside the user menu and the palette.
    renderApp({ initialPath: "/settings/billing", user });

    const back = await screen.findByRole("link", { name: "Back to app" });

    expect(back).toHaveAttribute("href", "/en");
  });

  it("groups settings into the account and the organization", async () => {
    renderApp({ initialPath: "/settings/profile", user });

    const account = await screen.findByRole("group", { name: "Your account" });
    const organization = screen.getByRole("group", { name: "Acme Inc" });

    expect(
      within(account).getByRole("link", { name: "Profile" }),
    ).toBeInTheDocument();
    expect(
      within(account).getByRole("link", { name: "Security" }),
    ).toBeInTheDocument();
    expect(
      within(organization).getByRole("link", { name: "Members" }),
    ).toBeInTheDocument();
    expect(
      within(organization).getByRole("link", { name: "Billing" }),
    ).toBeInTheDocument();
  });

  it("offers Dashboard and Settings from the user menu", async () => {
    // With the settings pages out of the sidebar, this menu is the only
    // pointer route into them -- and the only way back out of settings.
    renderApp({ initialPath: "/", user });

    await userEvent.click(await screen.findByTestId("user-menu-trigger"));

    expect(await screen.findByTestId("user-menu-settings")).toBeInTheDocument();
    expect(screen.getByTestId("user-menu-dashboard")).toBeInTheDocument();
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

    // Wait for something that's always present once the shell resolves, so
    // the absence check below isn't just catching an early/loading render.
    // (Not a settings link any more -- those left the sidebar.)
    await screen.findByTestId("nav-dashboard");
    expect(
      screen.queryByRole("link", { name: "Advanced reports" }),
    ).not.toBeInTheDocument();
  });
});
