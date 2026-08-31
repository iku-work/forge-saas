import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { ApiKeyPublic } from "@forge/api-client";

import { envelope, fixtureOrg, makeUser } from "../../test/appShellFixtures";
import { expectNoA11yViolations } from "../../test/axe";
import { server } from "../../test/server";

// Mocking @forge/hooks rather than the SDK calls underneath, for the same
// reason MembersSettingsPage.test.tsx does: useOrg's bootstrap is a
// module-scoped singleton, and this file needs a fresh org per test.
const hooks = vi.hoisted(() => ({
  useOrg: vi.fn(),
  usePermission: vi.fn(),
}));
vi.mock("@forge/hooks", () => hooks);

const { ApiKeysPage } = await import("./ApiKeysPage");

const API = getApiBaseUrl();
const user = makeUser(9);

const activeKey: ApiKeyPublic = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  name: "Billing sync",
  prefix: "a1b2c3d4e5f6",
  scopes: ["orgs:read"],
  created_by: user.id,
  created_at: "2026-02-01T00:00:00Z",
  last_used_at: null,
  expires_at: null,
  revoked_at: null,
  rate_limit_per_minute: null,
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ApiKeysPage />
    </QueryClientProvider>,
  );
}

describe("ApiKeysPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hooks.useOrg.mockReturnValue({
      org: fixtureOrg,
      orgs: [fixtureOrg],
      isLoading: false,
      switchOrg: vi.fn(),
    });
    hooks.usePermission.mockReturnValue(true);
    server.use(
      http.get(`${API}/api/v1/api-keys`, () =>
        HttpResponse.json(envelope([activeKey])),
      ),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists a key by name and prefix, and never by its secret", async () => {
    renderPage();

    expect(await screen.findByText("Billing sync")).toBeInTheDocument();
    expect(screen.getByText("a1b2c3d4e5f6")).toBeInTheDocument();
    expect(screen.getByText("orgs:read")).toBeInTheDocument();
  });

  it("shows the created key exactly once, and forgets it on close", async () => {
    // The property T-313 exists to defend: the secret is component state,
    // not react-query cache, so re-opening the dialog after closing it
    // cannot bring it back -- and neither can a re-render, because the
    // server has no copy to re-fetch either.
    const secret = "fk_live_a1b2c3d4e5f6_supersecretvalue";
    server.use(
      http.post(`${API}/api/v1/api-keys`, () =>
        HttpResponse.json(envelope({ key: secret, api_key: activeKey })),
      ),
    );
    const person = userEvent.setup();
    renderPage();

    await person.click(await screen.findByTestId("api-keys-create-button"));
    await person.type(screen.getByTestId("api-key-name-input"), "CI");
    await person.click(screen.getByTestId("api-key-scope-orgs:read"));
    await person.click(screen.getByTestId("api-key-submit"));

    expect(await screen.findByTestId("api-key-created-value")).toHaveTextContent(
      secret,
    );
    expect(screen.getByTestId("api-key-created-warning")).toBeInTheDocument();

    await person.click(screen.getByTestId("api-key-created-done"));

    await waitFor(() => {
      expect(screen.queryByTestId("api-key-created-value")).not.toBeInTheDocument();
    });
    // Re-opening the create dialog shows an empty form, not the last secret.
    await person.click(screen.getByTestId("api-keys-create-button"));
    expect(screen.queryByText(secret)).not.toBeInTheDocument();
  });

  it("refuses to create a key with no scopes", async () => {
    const person = userEvent.setup();
    renderPage();

    await person.click(await screen.findByTestId("api-keys-create-button"));
    await person.type(screen.getByTestId("api-key-name-input"), "No scopes");
    await person.click(screen.getByTestId("api-key-submit"));

    // The dialog stays open -- a key that grants nothing is a support
    // ticket, not a valid request.
    expect(screen.getByTestId("api-key-name-input")).toBeInTheDocument();
  });

  it("requires the confirmation word before revoking", async () => {
    let revoked = false;
    server.use(
      http.post(`${API}/api/v1/api-keys/:keyId/revoke`, () => {
        revoked = true;
        return HttpResponse.json(
          envelope({ ...activeKey, revoked_at: "2026-02-02T00:00:00Z" }),
        );
      }),
    );
    const person = userEvent.setup();
    renderPage();

    await person.click(await screen.findByTestId(`api-key-revoke-${activeKey.id}`));
    const submit = screen.getByTestId("api-key-revoke-submit");
    expect(submit).toBeDisabled();

    await person.type(screen.getByTestId("api-key-revoke-confirm-input"), "REVOKE");
    expect(submit).toBeEnabled();
    await person.click(submit);

    await waitFor(() => {
      expect(revoked).toBe(true);
    });
  });

  it("shows an empty state rather than an empty table", async () => {
    server.use(
      http.get(`${API}/api/v1/api-keys`, () => HttpResponse.json(envelope([]))),
    );
    renderPage();

    expect(await screen.findByTestId("api-keys-empty")).toBeInTheDocument();
  });

  it("surfaces a load failure instead of rendering an empty list", async () => {
    server.use(
      http.get(`${API}/api/v1/api-keys`, () => HttpResponse.error()),
    );
    renderPage();

    expect(
      await screen.findByText("Couldn't load API keys."),
    ).toBeInTheDocument();
  });

  it("shows a member nothing but why", async () => {
    // The nav entry is hidden without settings:manage, but a direct
    // navigation is not a navigation through the nav -- and a page that
    // renders and then fills with 403 toasts is worse than one that says so.
    hooks.usePermission.mockReturnValue(false);
    renderPage();

    expect(
      await screen.findByTestId("api-keys-permission-denied"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("api-keys-create-button")).not.toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = renderPage();

    await screen.findByText("Billing sync");
    await expectNoA11yViolations(container);
  });
});
