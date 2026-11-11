import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope, makeUser } from "../test/appShellFixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

const API = getApiBaseUrl();
const user = makeUser(3);

// Its own file: the org store is module state, shared by every test in a file.
describe("AppLayout (organizations failed to load)", () => {
  beforeEach(() => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: "token" })),
      ),
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(user)),
      ),
      // What a maintenance window answers before the config poll notices it.
      http.get(`${API}/api/v1/orgs`, () =>
        HttpResponse.json(
          { data: null, error: { code: "maintenance", message: "Down" } },
          { status: 503 },
        ),
      ),
    );
  });

  it("offers a reload, not to create a first organization", async () => {
    renderApp({ initialPath: "/", user });

    expect(await screen.findByTestId("org-load-failed")).toBeInTheDocument();
    expect(
      screen.queryByTestId("create-first-org-prompt"),
    ).not.toBeInTheDocument();
  });
});
