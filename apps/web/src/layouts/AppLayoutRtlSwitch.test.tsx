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
import { renderApp } from "../test/render";
import { server } from "../test/server";

const API = getApiBaseUrl();
const user = makeUser(1);

// A dedicated file for the same reason as AppLayoutLocaleSwitch.test.tsx
// (T-069): switching the live i18next singleton to "ar" here would
// otherwise leak into every other it() sharing this module's import.
describe("Profile settings: rtl language switch (T-231)", () => {
  let patchedLocaleBody: unknown = null;

  beforeEach(async () => {
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
        return HttpResponse.json(envelope({ ...user, locale: "ar" }));
      }),
    );
  });

  it("sets <html dir='rtl'> when switching to ar, and back to ltr for en", async () => {
    renderApp({ initialPath: "/settings/profile", user });

    expect(
      await screen.findByText(t("auth.login.email_label", {}, "en")),
    ).toBeInTheDocument();
    expect(document.documentElement.dir).toBe("ltr");

    await userEvent.click(
      screen.getByRole("button", {
        name: `${t("common.locale_switcher.label", {}, "en")} English`,
      }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "العربية" }),
    );

    await waitFor(() => {
      expect(document.documentElement.lang).toBe("ar");
    });
    expect(document.documentElement.dir).toBe("rtl");
    await waitFor(() => {
      expect(patchedLocaleBody).toEqual({ locale: "ar" });
    });

    await userEvent.click(
      screen.getByRole("button", {
        name: `${t("common.locale_switcher.label", {}, "ar")} العربية`,
      }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "English" }),
    );

    await waitFor(() => {
      expect(document.documentElement.dir).toBe("ltr");
    });
  });
});
