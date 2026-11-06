import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope, fixtureConfig } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();

// A first sign-in with a provider creates the account, so the sign-up page
// offers the same buttons as the login page.
//
// One /config scenario per file -- see LoginPageOAuthUnconfigured.test.tsx.
describe("RegisterPage (identity providers)", () => {
  it("offers the configured providers", async () => {
    server.use(
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({ ...fixtureConfig, oauth_providers: ["google"] }),
        ),
      ),
    );
    renderApp({ initialPath: "/register" });

    expect(
      await screen.findByRole("button", { name: "Continue with Google" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue with GitHub" }),
    ).not.toBeInTheDocument();
  });
});
