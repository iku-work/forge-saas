import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope, fixtureConfig } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();

// The half-configured case, which is the common one: a product registers a
// Google app and never gets round to GitHub. The page must offer exactly
// what works -- rendering both from one module flag is what UX-01 found,
// and rendering neither would be the over-correction.
//
// Own file, one /config scenario per file -- see
// LoginPageOAuthUnconfigured.test.tsx.
describe("LoginPage (only one provider configured)", () => {
  beforeEach(() => {
    server.use(
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            modules: { ...fixtureConfig.modules, oauth: true },
            oauth_providers: ["google"],
          }),
        ),
      ),
    );
  });

  it("offers the configured provider and not the other", async () => {
    renderApp({ initialPath: "/login" });

    expect(
      await screen.findByRole("button", { name: "Continue with Google" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Continue with GitHub" }),
    ).not.toBeInTheDocument();
  });
});
