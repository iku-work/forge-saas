import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
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

const API = getApiBaseUrl();
const user = makeUser(13);

// T-313/T-322: both modules on, but the signed-in user is a plain *member*.
// The module toggle and the permission are two independent gates, and this
// file is the second one — a member with `public_api` enabled must still see
// nothing, because a key that can mint a key and an endpoint that can be
// pointed anywhere are each an escalation. Separate file for the same
// bootstrap-singleton reason as its sibling.
describe("AppLayout (Phase 4 modules on, plain member)", () => {
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
        HttpResponse.json(envelope([makeMembership(user, "member")])),
      ),
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            modules: {
              ...fixtureConfig.modules,
              public_api: true,
              outbound_webhooks: true,
            },
          }),
        ),
      ),
    );
  });

  it("hides both entries from a member who cannot manage settings", async () => {
    renderApp({ initialPath: "/", user });

    expect(
      await screen.findByRole("heading", { name: "Welcome to Acme Inc" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "API keys" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Webhooks" }),
    ).not.toBeInTheDocument();
  });
});
