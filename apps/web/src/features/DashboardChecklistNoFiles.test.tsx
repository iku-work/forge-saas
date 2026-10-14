import { http, HttpResponse } from "msw";
import { screen, within } from "@testing-library/react";
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

// A second file, not a second test: @forge/hooks's config store memoizes its
// first /config fetch for the lifetime of the module, and vitest isolates
// modules per *file* -- so a `server.use` override inside the other file
// would be ignored and this would silently assert against the default
// configuration.
const API = getApiBaseUrl();
const user = makeUser(1);

describe("dashboard checklist with file storage off (ticket 10)", () => {
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
            modules: { ...fixtureConfig.modules, file_storage: false },
          }),
        ),
      ),
    );
  });

  it("never sends anyone to a module that is off", async () => {
    // With file storage off, a step pointing at /files would send the user to
    // a route the shell does not render -- the worst possible first action on
    // the first screen of a new account.
    renderApp({ initialPath: "/", user });

    const list = await screen.findByTestId("getting-started");
    expect(
      within(list).queryByText("Upload your first file"),
    ).not.toBeInTheDocument();
    expect(within(list).getByText("Invite a member")).toBeInTheDocument();
  });

  it("counts a profile with a name as complete, since there is no photo to add", async () => {
    renderApp({ initialPath: "/", user });

    const list = await screen.findByTestId("getting-started");
    const profile = within(list)
      .getByText("Complete your profile")
      .closest("li");
    expect(profile).not.toBeNull();
    expect(
      within(profile as HTMLElement).getByText("Done"),
    ).toBeInTheDocument();
  });
});
