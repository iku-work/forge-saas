import { http, HttpResponse } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
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

// T-263. One /config response per test *file*, not per test: @forge/hooks's
// config store memoizes its first fetch for the lifetime of the module, and
// vitest isolates modules per file -- so a second `server.use` in this file
// would be ignored. Same reason useModules.test.ts, useModulesDisabled.test.ts
// and useModulesDenyByDefault.test.ts are three files. The other two banner
// shapes live in GlobalBannerPinned.test.tsx and GlobalBannerAbsent.test.tsx.

const API = getApiBaseUrl();
const user = makeUser(42);

describe("GlobalBanner (dismissible, critical)", () => {
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
              type: "critical",
              message: "Payments are degraded.",
              dismissible: true,
            },
          }),
        ),
      ),
    );
  });

  it("renders the operator's message above the app shell", async () => {
    renderApp({ initialPath: "/", user });

    const banner = await screen.findByTestId("global-banner");
    // Verbatim: the message is operator prose, never a translation key.
    expect(banner).toHaveTextContent("Payments are degraded.");
    // `alert` interrupts a screen reader; `status` waits for a pause. A
    // critical banner is worth the interruption -- the other two severities
    // are not.
    expect(banner).toHaveAttribute("role", "alert");
  });

  it("hides itself once dismissed", async () => {
    renderApp({ initialPath: "/", user });
    const banner = await screen.findByTestId("global-banner");

    await userEvent.click(
      within(banner).getByRole("button", { name: "Dismiss announcement" }),
    );

    await waitFor(() => {
      expect(screen.queryByTestId("global-banner")).not.toBeInTheDocument();
    });
  });
});
