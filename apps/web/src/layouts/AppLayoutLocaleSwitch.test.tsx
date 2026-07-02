import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { t } from "@forge/i18n";

import i18next from "../i18n";
import {
  envelope,
  fixtureOrg,
  makeMembership,
  makeUser,
} from "../test/appShellFixtures";
import { expectNoA11yViolations } from "../test/axe";
import { renderApp } from "../test/render";
import { server } from "../test/server";

const API = getApiBaseUrl();
const user = makeUser(1);

// A dedicated file -- not added to AppLayout.test.tsx -- because switching
// the live i18next instance to "fi" here would otherwise leak into every
// other it() sharing this module's import (same module-singleton caveat as
// useAuth/useOrg/useModules; see AppLayoutModulesDisabled.test.tsx's notes).
describe("Profile settings: language switcher (T-069)", () => {
  let patchedLocaleBody: unknown = null;

  beforeEach(async () => {
    // i18next is a real module-scoped singleton (see i18n.ts) -- without
    // resetting it, the previous test's switch to "fi" would leak into
    // this one, same caveat as useAuth/useOrg/useModules.
    await i18next.changeLanguage("en");
    patchedLocaleBody = null;
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
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(envelope([makeMembership(user, "owner")])),
      ),
      http.patch(`${API}/api/v1/auth/me`, async ({ request }) => {
        patchedLocaleBody = await request.json();
        return HttpResponse.json(envelope({ ...user, locale: "fi" }));
      }),
    );
  });

  it("switches the UI language, persists it, and updates <html lang>", async () => {
    renderApp({ initialPath: "/settings/profile", user });

    // ProfileSettingsPage's email field is read-only (a <p>, not an
    // <input>) -- assert on the label's own text rather than
    // getByLabelText, which requires a labellable form control.
    expect(
      await screen.findByText(t("auth.login.email_label", {}, "en")),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", {
        name: t("common.locale_switcher.label", {}, "en"),
      }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "suomi" }),
    );

    expect(
      await screen.findByText(t("auth.login.email_label", {}, "fi")),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(document.documentElement.lang).toBe("fi");
    });
    await waitFor(() => {
      expect(patchedLocaleBody).toEqual({ locale: "fi" });
    });
  });

  it("has no a11y violations with the language switcher open", async () => {
    renderApp({ initialPath: "/settings/profile", user });
    await screen.findByText(t("auth.login.email_label", {}, "en"));

    await userEvent.click(
      screen.getByRole("button", {
        name: t("common.locale_switcher.label", {}, "en"),
      }),
    );
    await screen.findByRole("menuitem", { name: "suomi" });

    // Radix's DropdownMenuContent renders into a body-level portal, outside
    // renderApp()'s own container -- scan the full document so the open
    // menu itself is actually covered, not just the page underneath it.
    await expectNoA11yViolations(document.body);
  });
});
