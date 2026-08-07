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

// The default case, and the one worth its own file: this is what every user
// sees on every ordinary day, so a banner component that rendered an empty bar
// (or reserved space for one) would push the whole app down permanently. The
// shared handler in test/handlers.ts already returns fixtureConfig, whose
// `banner` is null.

const API = getApiBaseUrl();
const user = makeUser(44);

describe("GlobalBanner (nothing set)", () => {
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

  it("renders nothing at all", async () => {
    renderApp({ initialPath: "/", user });
    await screen.findByTestId("app-shell");

    expect(screen.queryByTestId("global-banner")).not.toBeInTheDocument();
  });
});
