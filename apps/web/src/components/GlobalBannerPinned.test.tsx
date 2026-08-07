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

// A separate file from GlobalBanner.test.tsx for the reason documented there:
// one /config response per file.

const API = getApiBaseUrl();
const user = makeUser(43);

describe("GlobalBanner (non-dismissible)", () => {
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
            banner: {
              type: "warning",
              message: "Read-only while we migrate.",
              dismissible: false,
            },
          }),
        ),
      ),
    );
  });

  it("offers no dismiss control when the operator pinned it", async () => {
    renderApp({ initialPath: "/", user });
    const banner = await screen.findByTestId("global-banner");

    expect(banner).toHaveTextContent("Read-only while we migrate.");
    expect(
      within(banner).queryByRole("button", { name: "Dismiss announcement" }),
    ).not.toBeInTheDocument();
  });
});
