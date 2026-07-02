import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import { envelope, makeUser } from "../test/appShellFixtures";
import { renderApp } from "../test/render";
import { server } from "../test/server";

const API = getApiBaseUrl();
const user = makeUser(3);

describe("AppLayout (no organizations yet)", () => {
  beforeEach(() => {
    server.use(
      http.post(`${API}/api/v1/auth/refresh`, () =>
        HttpResponse.json(envelope({ access_token: "token" })),
      ),
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(user)),
      ),
      http.get(`${API}/api/v1/orgs`, () => HttpResponse.json(envelope([]))),
    );
  });

  it("prompts to create an organization instead of rendering the shell", async () => {
    renderApp({ initialPath: "/", user });

    expect(
      await screen.findByRole("heading", { name: "Create your organization" }),
    ).toBeInTheDocument();
  });
});
