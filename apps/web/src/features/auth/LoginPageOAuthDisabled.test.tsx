import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope, fixtureConfig } from "../../test/appShellFixtures";
import { expectNoA11yViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();

// Own file, not a second `it()` in LoginPage.test.tsx/a11y.test.tsx -- see
// AppLayoutModulesDisabled.test.tsx's top comment for why useModules'
// module-scoped bootstrap singleton needs a fresh file per config scenario.
describe("LoginPage (oauth module disabled)", () => {
  beforeEach(() => {
    server.use(
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            modules: { ...fixtureConfig.modules, oauth: false },
            // The server empties this whenever the module is off, so the
            // fixture does too -- leaving providers listed here would
            // describe a response the backend cannot produce.
            oauth_providers: [],
          }),
        ),
      ),
    );
  });

  it("renders without OAuth buttons", async () => {
    const { container } = renderApp({ initialPath: "/login" });

    expect(
      await screen.findByRole("heading", { name: "Log in" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue with Google" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue with GitHub" }),
    ).not.toBeInTheDocument();
    await expectNoA11yViolations(container);
  });
});
