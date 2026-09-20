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
} from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";
import { ALL_SHORTCUTS } from "./registry";

const API = getApiBaseUrl();
const user = makeUser(1);

describe("ShortcutsDialog", () => {
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

  it("opens on ? from an authenticated page", async () => {
    renderApp({ initialPath: "/", user });
    await screen.findByRole("heading", { name: "Dashboard" });

    await userEvent.keyboard("?");

    expect(await screen.findByTestId("shortcuts-dialog")).toBeInTheDocument();
  });

  it("lists every registered shortcut", async () => {
    // The point of the shared registry: a shortcut added to the handler and
    // forgotten in the help, or the reverse, fails here.
    renderApp({ initialPath: "/", user });
    await screen.findByRole("heading", { name: "Dashboard" });
    await userEvent.keyboard("?");
    const dialog = await screen.findByTestId("shortcuts-dialog");

    const terms = within(dialog)
      .getAllByRole("term")
      .map((node) => node.textContent);
    expect(terms).toHaveLength(ALL_SHORTCUTS.length);
  });

  it("is dismissable with Escape", async () => {
    renderApp({ initialPath: "/", user });
    await screen.findByRole("heading", { name: "Dashboard" });
    await userEvent.keyboard("?");
    await screen.findByTestId("shortcuts-dialog");

    await userEvent.keyboard("{Escape}");

    expect(screen.queryByTestId("shortcuts-dialog")).not.toBeInTheDocument();
  });
});
