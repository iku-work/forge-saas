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
// configuration. Same reason GlobalBanner's three shapes are three files.
const API = getApiBaseUrl();
const user = makeUser(1);

describe("dashboard empty state with file storage off (F-010)", () => {
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

  it("offers a reachable action rather than a link into a disabled module", async () => {
    // With file storage off, pointing at /files would send the user to a
    // route the shell does not render -- the worst possible first action on
    // the first screen of a new account.
    renderApp({ initialPath: "/", user });

    const empty = await screen.findByTestId("dashboard-empty");
    expect(
      within(empty).queryByRole("link", { name: /upload your first file/i }),
    ).not.toBeInTheDocument();
    expect(
      within(empty).getByRole("link", { name: /invite a teammate/i }),
    ).toBeInTheDocument();
  });
});
