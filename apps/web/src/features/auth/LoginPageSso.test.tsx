import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { t } from "@forge/i18n";

import { envelope, fixtureConfig } from "../../test/appShellFixtures";
import { expectNoA11yViolations } from "../../test/axe";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();

// Own file, not more cases in LoginPage.test.tsx -- useModules' bootstrap is a
// module-scoped singleton, so a config scenario needs a fresh file (the same
// reason LoginPageOAuthDisabled.test.tsx exists).
describe("LoginPage (enterprise_sso enabled)", () => {
  beforeEach(() => {
    server.use(
      http.get(`${API}/api/v1/config`, () =>
        HttpResponse.json(
          envelope({
            ...fixtureConfig,
            modules: { ...fixtureConfig.modules, enterprise_sso: true },
          }),
        ),
      ),
    );
  });

  const hintFor = (available: boolean) =>
    http.post(`${API}/api/v1/auth/sso/hint`, () =>
      HttpResponse.json(
        envelope(
          available
            ? {
                sso_available: true,
                org_slug: "acme",
                provider_label: "Acme Okta",
              }
            : { sso_available: false, org_slug: null, provider_label: null },
        ),
      ),
    );

  const emailField = () =>
    screen.getByLabelText(t("auth.login.email_label", {}, "en"));

  async function typeEmailAndBlur(
    user: ReturnType<typeof userEvent.setup>,
    address: string,
  ): Promise<void> {
    await user.type(emailField(), address);
    await user.tab();
  }

  it("offers the provider instead of a password once the domain is known", async () => {
    server.use(hintFor(true));
    const user = userEvent.setup();
    const { container } = renderApp({ initialPath: "/login" });
    await screen.findByRole("heading", {
      name: t("auth.login.title", {}, "en"),
    });

    await typeEmailAndBlur(user, "someone@acme.example");

    const button = await screen.findByRole("button", {
      name: t("auth.login.sso_continue", { provider: "Acme Okta" }, "en"),
    });
    expect(button).toBeInTheDocument();
    // The password field is the thing being replaced -- if it is still there,
    // the hint has decorated the form rather than redirected it.
    expect(
      screen.queryByLabelText(t("auth.login.password_label", {}, "en")),
    ).not.toBeInTheDocument();
    await expectNoA11yViolations(container);
  });

  it("keeps the password field when the domain has no opt-in connection", async () => {
    server.use(hintFor(false));
    const user = userEvent.setup();
    renderApp({ initialPath: "/login" });
    await screen.findByRole("heading", {
      name: t("auth.login.title", {}, "en"),
    });

    await typeEmailAndBlur(user, "someone@example.com");

    await waitFor(() => {
      expect(
        screen.getByLabelText(t("auth.login.password_label", {}, "en")),
      ).toBeInTheDocument();
    });
    expect(screen.queryByTestId("login-sso-continue")).not.toBeInTheDocument();
  });

  it("keeps the password field when the lookup fails", async () => {
    // The endpoint is rate limited, so this is a real state and not a
    // hypothetical: a 429 must not stand between somebody and their password.
    server.use(
      http.post(`${API}/api/v1/auth/sso/hint`, () =>
        HttpResponse.json({ detail: "Too many requests" }, { status: 429 }),
      ),
    );
    const user = userEvent.setup();
    renderApp({ initialPath: "/login" });
    await screen.findByRole("heading", {
      name: t("auth.login.title", {}, "en"),
    });

    await typeEmailAndBlur(user, "someone@acme.example");

    await waitFor(() => {
      expect(
        screen.getByLabelText(t("auth.login.password_label", {}, "en")),
      ).toBeInTheDocument();
    });
    expect(screen.queryByTestId("login-sso-continue")).not.toBeInTheDocument();
  });

  it("gives the password field back on request", async () => {
    // The break-glass: an org's last remaining owner still has a password,
    // and the hint must not be the thing that hides it from them.
    server.use(hintFor(true));
    const user = userEvent.setup();
    renderApp({ initialPath: "/login" });
    await screen.findByRole("heading", {
      name: t("auth.login.title", {}, "en"),
    });

    await typeEmailAndBlur(user, "owner@acme.example");
    await screen.findByTestId("login-sso-continue");

    await user.click(screen.getByTestId("login-sso-use-password"));

    expect(
      await screen.findByLabelText(t("auth.login.password_label", {}, "en")),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("login-sso-continue")).not.toBeInTheDocument();
  });

  it("drops a stale hint as soon as the address changes", async () => {
    server.use(hintFor(true));
    const user = userEvent.setup();
    renderApp({ initialPath: "/login" });
    await screen.findByRole("heading", {
      name: t("auth.login.title", {}, "en"),
    });

    await typeEmailAndBlur(user, "someone@acme.example");
    await screen.findByTestId("login-sso-continue");

    // Editing the address invalidates the provider on screen -- sending
    // somebody to acme's IdP for a gmail address would be worse than useless.
    await user.type(emailField(), "x");

    await waitFor(() => {
      expect(
        screen.queryByTestId("login-sso-continue"),
      ).not.toBeInTheDocument();
    });
  });
});
