import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope, fixtureConfig } from "../../test/appShellFixtures";
import { expectNoA11yViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();

// UX-01, and the exact state every deployment of this template starts in:
// the `oauth` module is enabled by default, but no client IDs are set, so
// `get_oauth_clients()` is empty and both provider endpoints 404. The login
// page used to render "Continue with Google" and "Continue with GitHub"
// from the module flag alone -- two buttons that could only ever fail.
//
// Own file rather than another `it()` in LoginPageOAuthDisabled.test.tsx:
// useModules' bootstrap singleton resolves once per module registry, so one
// /config scenario per test file (see AppLayoutModulesDisabled.test.tsx).
describe("LoginPage (oauth module on, no provider configured)", () => {
  beforeEach(() => {
    server.use(
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            modules: { ...fixtureConfig.modules, oauth: true },
            oauth_providers: [],
          }),
        ),
      ),
    );
  });

  it("offers no social sign-in at all", async () => {
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
    // The "or" stays because the magic link is always on its other side
    // (ticket 11 moved it from the footer into the alternatives).
    expect(screen.getByText("or")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Use a magic link" }),
    ).toBeInTheDocument();
    await expectNoA11yViolations(container);
  });
});
