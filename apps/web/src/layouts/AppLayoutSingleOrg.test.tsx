import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
const user = makeUser(1);

// Own file: useModules' bootstrap is a module-scoped singleton, so each
// config scenario needs a fresh one (see AppLayoutModulesDisabled.test.tsx).
describe("AppLayout (multi-org off)", () => {
  beforeEach(() => {
    server.use(
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            modules: { ...fixtureConfig.modules, multi_org: false },
          }),
        ),
      ),
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

  it("opens the organization's settings from the user menu, with no switcher", async () => {
    renderApp({ initialPath: "/", user });

    await userEvent.click(await screen.findByTestId("user-menu-trigger"));
    await userEvent.click(await screen.findByTestId("user-menu-org-settings"));

    expect(
      await screen.findByRole("group", { name: "Acme Inc settings" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("org-switcher-trigger"),
    ).not.toBeInTheDocument();
  });
});
