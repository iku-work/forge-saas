import { http, HttpResponse } from "msw";
import { screen, within } from "@testing-library/react";
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

describe("dashboard empty state (F-010)", () => {
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

  it("explains the state and offers a next action, rather than only reporting emptiness", async () => {
    // What this replaced was the whole of the dashboard's copy: "Nothing here
    // yet." The plan's requirement is concept + why + action, so all three
    // are asserted -- a test for the heading alone would pass on the old
    // version too.
    renderApp({ initialPath: "/", user });

    const empty = await screen.findByTestId("dashboard-empty");
    expect(empty).toHaveTextContent(/your workspace is ready/i);
    expect(empty).toHaveTextContent(/because you've just started/i);
    expect(
      within(empty).getByRole("link", { name: /upload your first file/i }),
    ).toBeInTheDocument();
  });
});
