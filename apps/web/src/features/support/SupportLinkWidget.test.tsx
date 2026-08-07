import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";

import {
  envelope,
  fixtureConfig,
  fixtureOrg,
  fixtureSupportContext,
  makeUser,
} from "../../test/appShellFixtures";
import { server } from "../../test/server";
import { SupportLink, SUPPORT_OPEN_EVENT } from "./SupportLink";

const API = getApiBaseUrl();
const user = makeUser(1);

// Own file: useSupportSettings reads the /config bootstrap singleton, which
// is cached for a whole test file (same caveat as useModules) -- so a
// widget-tool product config can't share one with SupportLink.test.tsx's
// SUPPORT_TOOL="none".
describe("SupportLink with a widget support tool configured (T-262)", () => {
  const listeners: ((event: Event) => void)[] = [];

  function onSupportOpen(handler: (event: CustomEvent) => void): void {
    const listener = (event: Event): void => {
      handler(event as CustomEvent);
    };
    listeners.push(listener);
    window.addEventListener(SUPPORT_OPEN_EVENT, listener);
  }

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
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            support_tool: "intercom",
            support_url: "https://help.example.com",
          }),
        ),
      ),
    );
  });

  afterEach(() => {
    for (const listener of listeners.splice(0)) {
      window.removeEventListener(SUPPORT_OPEN_EVENT, listener);
    }
  });

  function renderLink() {
    const queryClient = new QueryClient();
    return render(
      <QueryClientProvider client={queryClient}>
        <SupportLink />
      </QueryClientProvider>,
    );
  }

  it("hands the product's widget the user's support context", async () => {
    const seen: unknown[] = [];
    onSupportOpen((event) => {
      seen.push(event.detail);
      // What a real SupportWidget does after booting its vendor: claim the
      // click so the browser doesn't also navigate to the fallback URL.
      event.preventDefault();
    });
    renderLink();
    const link = await screen.findByTestId("support-link");
    await waitFor(() => {
      expect(link).toHaveAttribute("href", "https://help.example.com");
    });

    await userEvent.click(link);

    await waitFor(() => {
      expect(seen).toHaveLength(1);
    });
    expect(seen[0]).toMatchObject({
      userId: fixtureSupportContext.user_id,
      orgId: fixtureSupportContext.org_id,
      plan: fixtureSupportContext.plan,
      isImpersonating: false,
    });
  });

  it("falls back to the plain link when nothing handles the event", async () => {
    renderLink();
    const link = await screen.findByTestId("support-link");
    // A blocked or unwired widget means no listener -- the anchor's own
    // navigation must survive, so support is never a dead button.
    let defaultPrevented = true;
    link.addEventListener("click", (event) => {
      defaultPrevented = event.defaultPrevented;
    });

    await userEvent.click(link);

    expect(defaultPrevented).toBe(false);
    // An external URL opens in a new tab; a mailto: never would (see
    // SupportLink).
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});
