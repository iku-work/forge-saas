import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { Toaster } from "@forge/ui";
import type { UserPublic } from "@forge/api-client";

import { envelope } from "../../test/appShellFixtures";
import { server } from "../../test/server";

const hooks = vi.hoisted(() => ({
  useAuth: vi.fn(),
  useLocale: vi.fn(() => ({
    locale: "en",
    supportedLocales: ["en", "fi"],
    setLocale: vi.fn(),
  })),
}));
vi.mock("@forge/hooks", () => hooks);

// T-233: the language switcher on this page now rewrites the URL's locale
// segment, so it needs a live router -- which this file deliberately doesn't
// build (it renders the page bare to unit-test the profile card itself). The
// real switcher, wired through the real router, is covered by
// layouts/AppLayoutLocaleSwitch.test.tsx.
vi.mock("../../lib/RoutedLocaleSwitcher", () => ({
  RoutedLocaleSwitcher: () => <div data-testid="locale-switcher" />,
}));

// Same reason: the "Change email" row links to the security page, and a
// router <Link> (and the locale read from route params) needs a live router.
// A plain anchor keeps the href assertable.
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  Link: ({
    to,
    params,
    children,
    ...rest
  }: {
    to: string;
    params: { locale: string };
    children: React.ReactNode;
  }) => (
    <a href={to.replace("$locale", params.locale)} {...rest}>
      {children}
    </a>
  ),
  useParams: () => "en",
}));

const { ProfileSettingsPage } = await import("./ProfileSettingsPage");

const API = getApiBaseUrl();

const user: UserPublic = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "jane@example.com",
  full_name: "Jane Doe",
  avatar_url: null,
  locale: "en",
  email_verified_at: "2026-01-01T00:00:00Z",
  totp_enabled: false,
};

function renderPage() {
  // retry: false, as in BillingSettingsPage.test.tsx -- an error-response test
  // would otherwise wait out React Query's backoff.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ProfileSettingsPage />
      <Toaster />
    </QueryClientProvider>,
  );
}

function nameInput(): HTMLInputElement {
  return screen.getByLabelText("Name");
}

describe("ProfileSettingsPage", () => {
  it("shows a loading skeleton while the user is bootstrapping", () => {
    hooks.useAuth.mockReturnValue({ user: null, isLoading: true });

    renderPage();

    expect(
      screen.getByRole("heading", { name: "Profile" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(user.email)).not.toBeInTheDocument();
  });

  it("seeds the form with the loaded name", async () => {
    hooks.useAuth.mockReturnValue({ user, isLoading: false });

    renderPage();

    // An input, not text: this page rendered the name in a <p> with no way to
    // change it until 2026-09-12, which is the bug these tests exist for.
    await waitFor(() => {
      expect(nameInput()).toHaveValue("Jane Doe");
    });
    expect(screen.getByText("jane@example.com")).toBeInTheDocument();
  });

  it("saves a changed name and tells the shared auth store", async () => {
    const applyUpdatedUser = vi.fn();
    hooks.useAuth.mockReturnValue({ user, isLoading: false, applyUpdatedUser });

    let sent: unknown = null;
    server.use(
      http.patch(`${API}/api/v1/auth/me`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({
          data: { ...user, full_name: "Ada Lovelace" },
          error: null,
          meta: { request_id: "r1" },
        });
      }),
    );

    renderPage();
    await waitFor(() => {
      expect(nameInput()).toHaveValue("Jane Doe");
    });

    await userEvent.clear(nameInput());
    await userEvent.type(nameInput(), "Ada Lovelace");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(sent).toEqual({ full_name: "Ada Lovelace" });
    });
    // The name renders in the app shell as well, so the store has to be told
    // -- and told what the *server* stored, not what was typed.
    await waitFor(() => {
      expect(applyUpdatedUser).toHaveBeenCalledWith(
        expect.objectContaining({ full_name: "Ada Lovelace" }),
      );
    });
  });

  it("sends null rather than an empty string when the name is cleared", async () => {
    hooks.useAuth.mockReturnValue({
      user,
      isLoading: false,
      applyUpdatedUser: vi.fn(),
    });

    let sent: unknown = null;
    server.use(
      http.patch(`${API}/api/v1/auth/me`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({
          data: { ...user, full_name: null },
          error: null,
          meta: { request_id: "r1" },
        });
      }),
    );

    renderPage();
    await waitFor(() => {
      expect(nameInput()).toHaveValue("Jane Doe");
    });

    await userEvent.clear(nameInput());
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    // "" would be a name made of nothing; null is the absence of one. Only
    // the second clears the column -- see UpdateMeRequest's docstring.
    await waitFor(() => {
      expect(sent).toEqual({ full_name: null });
    });
  });

  it("sends a change of email to the security page", async () => {
    hooks.useAuth.mockReturnValue({
      user,
      isLoading: false,
      applyUpdatedUser: vi.fn(),
    });
    renderPage();

    expect(
      await screen.findByRole("link", { name: "Change email" }),
    ).toHaveAttribute("href", "/en/settings/security");
  });

  it("keeps Save disabled until something actually changes", async () => {
    hooks.useAuth.mockReturnValue({
      user,
      isLoading: false,
      applyUpdatedUser: vi.fn(),
    });

    renderPage();
    await waitFor(() => {
      expect(nameInput()).toHaveValue("Jane Doe");
    });

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await userEvent.type(nameInput(), "!");

    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("removes an uploaded photo", async () => {
    const applyUpdatedUser = vi.fn();
    hooks.useAuth.mockReturnValue({
      user: { ...user, has_uploaded_avatar: true },
      isLoading: false,
      applyUpdatedUser,
    });
    let deleted = false;
    server.use(
      http.delete(`${API}/api/v1/auth/me/avatar`, () => {
        deleted = true;
        return HttpResponse.json(
          envelope({ ...user, has_uploaded_avatar: false }),
        );
      }),
    );

    renderPage();
    await userEvent.click(await screen.findByTestId("profile-avatar-remove"));

    await waitFor(() => {
      expect(applyUpdatedUser).toHaveBeenCalledWith(
        expect.objectContaining({ has_uploaded_avatar: false }),
      );
    });
    expect(deleted).toBe(true);
  });

  it("offers no Remove without an uploaded photo", async () => {
    hooks.useAuth.mockReturnValue({
      user: { ...user, has_uploaded_avatar: false },
      isLoading: false,
      applyUpdatedUser: vi.fn(),
    });

    renderPage();

    await screen.findByTestId("profile-avatar");
    expect(
      screen.queryByTestId("profile-avatar-remove"),
    ).not.toBeInTheDocument();
  });

  it("uploads a photo straight to storage and waits until it is published", async () => {
    const applyUpdatedUser = vi.fn();
    hooks.useAuth.mockReturnValue({ user, isLoading: false, applyUpdatedUser });
    const published =
      "https://storage.example.test/forge/public/avatars/new.webp";
    const sent: Record<string, unknown> = {};
    server.use(
      http.post(
        `${API}/api/v1/auth/me/avatar/upload-form`,
        async ({ request }) => {
          sent.form = await request.json();
          return HttpResponse.json(
            envelope({
              upload_url: "https://storage.example.test/forge",
              fields: { key: "incoming/avatars/u/k" },
              upload_key: "incoming/avatars/u/k",
              expires_at: "2026-12-31T00:00:00Z",
            }),
          );
        },
      ),
      http.post("https://storage.example.test/forge", async ({ request }) => {
        // As text: undici cannot parse a jsdom File back out of the form.
        const body = await request.text();
        sent.storageKey = body.includes("incoming/avatars/u/k");
        return new HttpResponse(null, { status: 204 });
      }),
      http.post(`${API}/api/v1/auth/me/avatar`, async ({ request }) => {
        sent.set = await request.json();
        return HttpResponse.json(
          envelope({ ...user, avatar_processing: true }),
          {
            status: 202,
          },
        );
      }),
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(
          envelope({
            ...user,
            avatar_url: published,
            has_uploaded_avatar: true,
          }),
        ),
      ),
    );

    renderPage();
    await userEvent.upload(
      await screen.findByTestId("profile-avatar-input"),
      new File(["x"], "me.png", { type: "image/png" }),
    );

    await waitFor(
      () => {
        expect(applyUpdatedUser).toHaveBeenCalledWith(
          expect.objectContaining({ avatar_url: published }),
        );
      },
      { timeout: 5_000 },
    );
    expect(sent).toEqual({
      form: { content_type: "image/png" },
      storageKey: true,
      set: { upload_key: "incoming/avatars/u/k" },
    });
  });

  it("says so when the server could not use the image", async () => {
    const applyUpdatedUser = vi.fn();
    hooks.useAuth.mockReturnValue({ user, isLoading: false, applyUpdatedUser });
    server.use(
      http.post(`${API}/api/v1/auth/me/avatar/upload-form`, () =>
        HttpResponse.json(
          envelope({
            upload_url: "https://storage.example.test/forge",
            fields: { key: "incoming/avatars/u/k" },
            upload_key: "incoming/avatars/u/k",
            expires_at: "2026-12-31T00:00:00Z",
          }),
        ),
      ),
      http.post(
        "https://storage.example.test/forge",
        () => new HttpResponse(null, { status: 204 }),
      ),
      http.post(`${API}/api/v1/auth/me/avatar`, () =>
        HttpResponse.json(envelope({ ...user, avatar_processing: true }), {
          status: 202,
        }),
      ),
      // Processing finished and the picture is unchanged: refused.
      http.get(`${API}/api/v1/auth/me`, () =>
        HttpResponse.json(envelope(user)),
      ),
    );

    renderPage();
    await userEvent.upload(
      await screen.findByTestId("profile-avatar-input"),
      new File(["x"], "me.png", { type: "image/png" }),
    );

    expect(
      await screen.findByText(
        "That image couldn’t be used. Try a JPG, PNG or WebP of up to 5 MB.",
        {},
        { timeout: 5_000 },
      ),
    ).toBeInTheDocument();
    expect(applyUpdatedUser).not.toHaveBeenCalled();
  });

  it("leaves the field empty when the user has no name", async () => {
    hooks.useAuth.mockReturnValue({
      user: { ...user, full_name: null },
      isLoading: false,
      applyUpdatedUser: vi.fn(),
    });

    renderPage();

    await waitFor(() => {
      expect(nameInput()).toHaveValue("");
    });
  });
});
