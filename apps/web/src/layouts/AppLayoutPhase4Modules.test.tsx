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
const user = makeUser(12);

// Own file, like every other AppLayout scenario here: useModules' bootstrap
// is a module-scoped singleton promise, so one /api/v1/config response
// serves the whole file. This one turns the two Phase 4 modules *on*, which
// is the mirror of AppLayoutModulesDisabled.test.tsx -- that file proves the
// surfaces vanish at the committed configuration, this one proves they
// appear when a product enables them, so neither passes vacuously.
describe("AppLayout (public_api and outbound_webhooks enabled)", () => {
  beforeEach(() => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: "token" })),
      ),
      http.get(`${API}/api/v1/auth/me`, () => HttpResponse.json(envelope(user))),
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

  it("shows the API keys and Webhooks nav links to an owner", async () => {
    renderApp({ initialPath: "/", user });

    expect(
      await screen.findByRole("heading", { name: "Dashboard" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "API keys" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Webhooks" })).toBeInTheDocument();
  });
});
