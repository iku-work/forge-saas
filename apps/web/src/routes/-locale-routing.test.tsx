import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { t } from "@forge/i18n";

import i18next, { LOCALE_STORAGE_KEY } from "../i18n";
import { makeUser } from "../test/appShellFixtures";
import { renderApp } from "../test/render";

// T-233's acceptance criteria, exercised through the real router.
//
// Every case below starts from a URL a *user* can actually arrive at -- an
// old bookmark, a link someone pasted into chat, a typo -- rather than from
// an internal navigation, because the redirect rules only exist to make
// those inbound URLs work.

afterEach(async () => {
  // Language first, key second. changeLanguage() here is *not* URL-driven, so
  // i18n.ts's languageChanged handler persists it -- clearing the key before
  // that ran left "en" written straight back, so a test could inherit a
  // preference from whichever test ran before it.
  if (i18next.language !== "en") {
    await i18next.changeLanguage("en");
  }
  localStorage.removeItem(LOCALE_STORAGE_KEY);
});

describe("locale-prefixed routing", () => {
  it("redirects a locale-less URL to the resolved locale, keeping the path", async () => {
    const { router } = renderApp({ initialPath: "/login" });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/en/login");
    });
  });

  it("redirects a deep locale-less URL, keeping its query string", async () => {
    const { router } = renderApp({ initialPath: "/reset-password?token=abc" });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/en/reset-password");
    });
    expect(router.state.location.searchStr).toBe("?token=abc");
  });

  it("uses the signed-in user's own locale for the redirect", async () => {
    // Mirrors the backend's resolution order: User.locale outranks the
    // browser's languages, which in jsdom are en-* only.
    const { router } = renderApp({
      initialPath: "/settings/profile",
      user: makeUser(30, { locale: "fi" }),
    });
    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/fi/settings/profile");
    });
  });

  it("redirects bare / to the resolved locale's dashboard", async () => {
    const { router } = renderApp({ initialPath: "/", user: null });
    await waitFor(() => {
      // Not authenticated, so `_app`'s guard bounces on to /login -- what
      // matters here is that it did so under a locale prefix.
      expect(router.state.location.pathname).toBe("/en/login");
    });
  });

  it("renders a shared /de/ link in German regardless of stored preference", async () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, "en");

    renderApp({ initialPath: "/de/login" });

    // The de bundle is lazy-loaded (see i18n.ts's LOCALE_LOADERS), so wait
    // for the switch to land before asserting on either the page or the
    // preference it must not have touched.
    await waitFor(() => {
      expect(i18next.language).toBe("de");
    });
    expect(
      await screen.findByRole("heading", {
        name: t("auth.login.title", {}, "de"),
      }),
    ).toBeInTheDocument();
    // ...and does not quietly adopt German as the visitor's preference.
    expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
  });

  it("404s an unknown but locale-shaped prefix instead of redirecting", async () => {
    const { router } = renderApp({ initialPath: "/xx/login" });

    // Asserted on the rendered page rather than router.state.status: the
    // notFound() thrown from `/$locale`'s beforeLoad leaves the child match
    // (`/$locale/login`, which will never be rendered) parked at "pending"
    // forever, so the router's *aggregate* status never reaches "idle" even
    // though the 404 is on screen. What matters is what the visitor gets.
    expect(
      await screen.findByText(t("common.not_found.title", {}, "en")),
    ).toBeInTheDocument();
    expect(
      router.state.matches.some((match) => match.status === "notFound"),
    ).toBe(true);
    // Not rewritten to /en/xx/login -- an unknown locale is an error, not a
    // legacy URL to migrate.
    expect(router.state.location.pathname).toBe("/xx/login");
  });

  it("404s an unknown path under a valid locale rather than re-prefixing it", async () => {
    const { router } = renderApp({ initialPath: "/en/nope/nope" });
    await waitFor(() => {
      expect(router.state.status).toBe("idle");
    });
    expect(router.state.location.pathname).toBe("/en/nope/nope");
    expect(router.state.matches.at(-1)?.status).toBe("notFound");
  });

  it("sets <html lang>/<html dir> from the URL locale", async () => {
    renderApp({ initialPath: "/de/login" });
    await waitFor(() => {
      expect(document.documentElement.lang).toBe("de");
    });
    expect(document.documentElement.dir).toBe("ltr");
  });
});

describe("canonical + hreflang head tags", () => {
  it("emits a canonical URL and one alternate per registry locale on public pages", async () => {
    renderApp({ initialPath: "/de/login" });

    await waitFor(() => {
      expect(
        document.head.querySelector('link[rel="canonical"]'),
      ).not.toBeNull();
    });
    expect(
      document.head
        .querySelector('link[rel="canonical"]')
        ?.getAttribute("href"),
    ).toBe(`${window.location.origin}/de/login`);

    const alternates = [
      ...document.head.querySelectorAll('link[rel="alternate"]'),
    ].map((link) => [
      link.getAttribute("hreflang"),
      link.getAttribute("href"),
    ]);
    expect(alternates).toContainEqual([
      "fi",
      `${window.location.origin}/fi/login`,
    ]);
    expect(alternates).toContainEqual([
      "ar",
      `${window.location.origin}/ar/login`,
    ]);
    expect(alternates).toContainEqual([
      "x-default",
      `${window.location.origin}/en/login`,
    ]);
    expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
  });

  it("keeps signed-in pages noindex and alternate-free", async () => {
    renderApp({
      initialPath: "/settings/profile",
      user: makeUser(31, { locale: "en" }),
    });

    await waitFor(() => {
      expect(document.head.querySelector('meta[name="robots"]')).not.toBeNull();
    });
    expect(
      document.head.querySelector('meta[name="robots"]')?.getAttribute("content"),
    ).toBe("noindex, nofollow");
    expect(
      document.head
        .querySelector('link[rel="canonical"]')
        ?.getAttribute("href"),
    ).toBe(`${window.location.origin}/en/settings/profile`);
    expect(document.head.querySelectorAll('link[rel="alternate"]')).toHaveLength(
      0,
    );
  });
});
