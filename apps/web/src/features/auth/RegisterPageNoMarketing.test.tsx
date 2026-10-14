import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope, fixtureConfig } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();

// UX-02, the other side of it: with the marketing module off there is no
// terms page and no privacy page anywhere, so the notice has nothing to
// link to. A dead link to a policy is worse than no link -- it looks like
// the document exists.
//
// This leaves a product in that configuration asserting acceptance of terms
// it does not publish, which is a content decision rather than a bug; see
// the content stage of docs/production-readiness-plan-2026-09-25.md.
//
// Own file, one /config scenario per file -- see
// LoginPageOAuthUnconfigured.test.tsx.
describe("RegisterPage (no marketing site)", () => {
  beforeEach(() => {
    server.use(
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            modules: { ...fixtureConfig.modules, marketing: false },
            marketing_url: "",
          }),
        ),
      ),
    );
  });

  it("omits the legal notice rather than linking nowhere", async () => {
    renderApp({ initialPath: "/register" });

    expect(
      await screen.findByRole("heading", { name: "Create your account" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Terms of Service" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Privacy Policy" }),
    ).not.toBeInTheDocument();
    // Nor the page footer's links (ticket 11).
    expect(
      screen.queryByRole("link", { name: "Terms" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Privacy" }),
    ).not.toBeInTheDocument();
  });
});
