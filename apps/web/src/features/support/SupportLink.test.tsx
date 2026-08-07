import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import {
  envelope,
  fixtureOrg,
  fixtureSupportContext,
  makeUser,
} from "../../test/appShellFixtures";
import { expectNoA11yViolations } from "../../test/axe";
import { server } from "../../test/server";
import { SupportLink } from "./SupportLink";

const API = getApiBaseUrl();
const user = makeUser(1);

// SUPPORT_TOOL="none" (the template default, from the shared fixtureConfig).
// The widget-tool behaviour needs a different /config response, and
// useSupportSettings caches that per test file -- hence SupportLinkWidget.test.tsx.
describe("SupportLink with no support tool configured (T-262)", () => {
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
    );
  });

  function renderLink() {
    const queryClient = new QueryClient();
    return render(
      <QueryClientProvider client={queryClient}>
        <SupportLink />
      </QueryClientProvider>,
    );
  }

  it("links to the configured support URL with the user's context prefilled", async () => {
    renderLink();

    const link = await screen.findByTestId("support-link");
    await waitFor(() => {
      expect(link).toHaveAttribute(
        "href",
        expect.stringContaining(
          encodeURIComponent(fixtureSupportContext.email),
        ),
      );
    });
    const href = link.getAttribute("href") ?? "";
    expect(href.startsWith("mailto:support@example.com?")).toBe(true);
    const body = new URLSearchParams(href.split("?")[1]).get("body") ?? "";
    expect(body).toContain(fixtureSupportContext.org_name);
    expect(body).toContain(fixtureSupportContext.plan ?? "");
  });

  it("is still a usable link before the context resolves", async () => {
    server.use(
      http.get(`${API}/api/v1/me/support-context`, () =>
        HttpResponse.json(envelope(null), { status: 500 }),
      ),
    );

    renderLink();

    const link = await screen.findByTestId("support-link");
    // No context to attach, so no query string at all -- but the address is
    // the same one, and clicking it still reaches support.
    expect(link).toHaveAttribute("href", "mailto:support@example.com");
  });

  it("has no accessibility violations", async () => {
    const { container } = renderLink();
    await screen.findByTestId("support-link");

    await expectNoA11yViolations(container);
  });
});
