import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
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
import { SIDEBAR_STORAGE_KEY } from "../lib/useSidebarCollapsed";

const API = getApiBaseUrl();
const user = makeUser(1);

/** The panel's toggle, by what a screen reader hears. */
function toggle(name: "Collapse sidebar" | "Expand sidebar"): HTMLElement {
  return screen.getByRole("button", { name });
}

describe("AppLayout sidebar collapse (ticket 05)", () => {
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

  it("collapses and expands from the panel's toggle", async () => {
    renderApp({ initialPath: "/", user });
    await screen.findByTestId("sidebar-primary-nav");

    await userEvent.click(toggle("Collapse sidebar"));

    expect(toggle("Expand sidebar")).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(toggle("Expand sidebar"));

    expect(toggle("Collapse sidebar")).toHaveAttribute("aria-expanded", "true");
  });

  it("toggles with [ from anywhere but a text field", async () => {
    renderApp({ initialPath: "/", user });
    await screen.findByTestId("sidebar-primary-nav");

    await userEvent.keyboard("[["); // "[[" is a literal [ in user-event syntax
    expect(toggle("Expand sidebar")).toBeInTheDocument();

    // Typing a bracket is typing, not a command.
    const field = document.createElement("input");
    document.body.append(field);
    field.focus();
    await userEvent.keyboard("[["); // "[[" is a literal [ in user-event syntax
    expect(toggle("Expand sidebar")).toBeInTheDocument();
    field.remove();
  });

  it("remembers the choice on this device", async () => {
    const first = renderApp({ initialPath: "/", user });
    await screen.findByTestId("sidebar-primary-nav");
    await userEvent.click(toggle("Collapse sidebar"));
    expect(window.localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe("collapsed");
    first.unmount();

    renderApp({ initialPath: "/", user });
    await screen.findByTestId("sidebar-primary-nav");

    expect(toggle("Expand sidebar")).toBeInTheDocument();
  });

  it("keeps every row's name when collapsed to icons", async () => {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, "collapsed");
    renderApp({ initialPath: "/", user });
    await screen.findByTestId("sidebar-primary-nav");

    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /search/i })).toBeInTheDocument();
  });

  it("lists the shortcut in the cheat sheet", async () => {
    renderApp({ initialPath: "/", user });
    await screen.findByTestId("sidebar-primary-nav");

    await userEvent.keyboard("?");

    expect(
      await screen.findByText("Collapse or expand the sidebar"),
    ).toBeInTheDocument();
  });
});
