import { http, HttpResponse } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import {
  envelope,
  fixtureOrg,
  makeMembership,
  makeUser,
} from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();
const user = makeUser(1);

async function openPalette(): Promise<HTMLElement> {
  await userEvent.click(await screen.findByTestId("palette-trigger"));
  return screen.findByRole("dialog");
}

describe("CommandPalette", () => {
  beforeEach(() => {
    window.localStorage.clear();
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

  it("opens from the header button", async () => {
    renderApp({ initialPath: "/", user });

    const dialog = await openPalette();

    expect(
      within(dialog).getByPlaceholderText(/search pages and actions/i),
    ).toBeInTheDocument();
  });

  it("opens on Ctrl+K from anywhere in the shell", async () => {
    renderApp({ initialPath: "/", user });
    await screen.findByRole("heading", { name: "Dashboard" });

    await userEvent.keyboard("{Control>}k{/Control}");

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("offers the same pages the sidebar shows", async () => {
    renderApp({ initialPath: "/", user });

    const dialog = await openPalette();

    // Billing is an owner-only, module-gated page: its presence here is what
    // proves the palette reads the gated nav rather than a hardcoded list.
    expect(
      within(dialog).getByTestId("palette-nav-/settings/billing"),
    ).toBeInTheDocument();
    expect(within(dialog).getByTestId("palette-nav-/")).toBeInTheDocument();
  });

  it("filters as the user types", async () => {
    renderApp({ initialPath: "/", user });
    const dialog = await openPalette();

    await userEvent.type(
      within(dialog).getByPlaceholderText(/search pages and actions/i),
      "billing",
    );

    await waitFor(() => {
      expect(
        within(dialog).getByTestId("palette-nav-/settings/billing"),
      ).toBeInTheDocument();
    });
    expect(
      within(dialog).queryByTestId("palette-nav-/settings/security"),
    ).not.toBeInTheDocument();
  });

  it("matches on the path as well as the label", async () => {
    // Someone who knows the app types "settings", not "Profile".
    renderApp({ initialPath: "/", user });
    const dialog = await openPalette();

    await userEvent.type(
      within(dialog).getByPlaceholderText(/search pages and actions/i),
      "settings/sec",
    );

    await waitFor(() => {
      expect(
        within(dialog).getByTestId("palette-nav-/settings/security"),
      ).toBeInTheDocument();
    });
  });

  it("navigates when a row is chosen, and closes", async () => {
    renderApp({ initialPath: "/", user });
    const dialog = await openPalette();

    await userEvent.click(
      within(dialog).getByTestId("palette-nav-/settings/security"),
    );

    expect(
      await screen.findByRole("heading", { name: "Security" }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("shows nothing-found text for a query that matches no row", async () => {
    renderApp({ initialPath: "/", user });
    const dialog = await openPalette();

    await userEvent.type(
      within(dialog).getByPlaceholderText(/search pages and actions/i),
      "zzzzzz",
    );

    expect(await within(dialog).findByText(/no matches/i)).toBeInTheDocument();
  });

  it("offers recently visited pages first, once there are any", async () => {
    // The shell records a recent path on every navigation, so visiting a page
    // and reopening the palette is the whole round trip.
    renderApp({ initialPath: "/settings/members", user });
    await screen.findByRole("heading", { name: "Members" });

    const dialog = await openPalette();

    const recentGroup = await within(dialog).findByText(/^recent$/i);
    expect(recentGroup).toBeInTheDocument();
    expect(
      within(dialog).getByTestId("palette-recent-/settings/members"),
    ).toBeInTheDocument();
  });
});
