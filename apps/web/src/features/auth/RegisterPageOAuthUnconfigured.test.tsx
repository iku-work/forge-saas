import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope, fixtureConfig } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();

// Own file: /config is cached per module, so one scenario per file. See
// LoginPageOAuthUnconfigured.test.tsx.
describe("RegisterPage (no identity provider configured)", () => {
  it("shows no separator with nothing under it", async () => {
    let configServed = false;
    server.use(
      http.get(`${API}/api/v1/config`, () => {
        configServed = true;
        return HttpResponse.json(
          envelope({ ...fixtureConfig, oauth_providers: [] }),
        );
      }),
    );
    renderApp({ initialPath: "/register" });

    await screen.findByRole("heading", { name: "Create your account" });
    await waitFor(() => {
      expect(configServed).toBe(true);
    });
    expect(screen.queryByText("or")).not.toBeInTheDocument();
  });
});
