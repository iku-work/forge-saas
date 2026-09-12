import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { UserPublic } from "@forge/api-client";

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

const { ProfileSettingsPage } = await import("./ProfileSettingsPage");

const API = getApiBaseUrl();

// The fixture file id the shared presign handler reports (see test/handlers.ts).
const FIXTURE_FILE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-000000000001";

// MSW v2 intercepts fetch but not raw XMLHttpRequest, which is what
// FileUploader uses for the S3 POST -- same stand-in as FileUploader.test.tsx.
class AutoSuccessXHR {
  readonly upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
  status = 204;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open = vi.fn();
  setRequestHeader = vi.fn();
  abort = vi.fn();
  send = vi.fn().mockImplementation(() => {
    void Promise.resolve().then(() => this.onload?.());
  });
}

beforeEach(() => {
  vi.stubGlobal("XMLHttpRequest", AutoSuccessXHR);
});

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

  it("fetches a signed URL for the avatar rather than using a public one", async () => {
    // The point of a private bucket: there is nothing renderable on the user
    // object, so the page has to ask for a short-lived signed link. Asserted
    // on the request rather than on a rendered <img> -- Radix's AvatarImage
    // only mounts one once the browser reports the image loaded, and jsdom
    // never loads images, so the DOM cannot answer this question here.
    hooks.useAuth.mockReturnValue({
      user: { ...user, has_uploaded_avatar: true },
      isLoading: false,
      applyUpdatedUser: vi.fn(),
    });
    let asked = false;
    server.use(
      http.get(`${API}/api/v1/auth/me/avatar`, () => {
        asked = true;
        return HttpResponse.json({
          data: {
            url: "https://storage.example.test/forge/a.jpg?X-Amz-Signature=abc",
            expires_at: "2026-12-31T00:00:00Z",
          },
          error: null,
          meta: { request_id: "r1" },
        });
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(asked).toBe(true);
    });
  });

  it("does not ask for an avatar URL when there is no uploaded avatar", async () => {
    let asked = false;
    hooks.useAuth.mockReturnValue({
      user: { ...user, has_uploaded_avatar: false },
      isLoading: false,
      applyUpdatedUser: vi.fn(),
    });
    server.use(
      http.get(`${API}/api/v1/auth/me/avatar`, () => {
        asked = true;
        return HttpResponse.json(
          { data: null, error: null, meta: {} },
          { status: 404 },
        );
      }),
    );

    renderPage();
    await waitFor(() => {
      expect(nameInput()).toHaveValue("Jane Doe");
    });

    // A guaranteed 404 is not worth a request on every profile render.
    expect(asked).toBe(false);
  });

  it("attaches an uploaded file to the profile as its avatar", async () => {
    const applyUpdatedUser = vi.fn();
    hooks.useAuth.mockReturnValue({ user, isLoading: false, applyUpdatedUser });

    let patched: { avatar_file_id?: string } | null = null;
    server.use(
      http.patch(`${API}/api/v1/auth/me`, async ({ request }) => {
        patched = (await request.json()) as { avatar_file_id?: string };
        return HttpResponse.json({
          data: { ...user, has_uploaded_avatar: true },
          error: null,
          meta: { request_id: "r1" },
        });
      }),
      http.get(`${API}/api/v1/auth/me/avatar`, () =>
        HttpResponse.json({
          data: {
            url: "https://storage.example.test/a.jpg?X-Amz-Signature=abc",
            expires_at: "2026-12-31T00:00:00Z",
          },
          error: null,
          meta: { request_id: "r1" },
        }),
      ),
    );

    renderPage();
    await waitFor(() => {
      expect(nameInput()).toHaveValue("Jane Doe");
    });

    const input =
      document.querySelector<HTMLInputElement>('input[type="file"]');
    if (!input) {
      throw new Error('input[type="file"] not found');
    }
    await userEvent.upload(
      input,
      new File(["x"], "me.png", { type: "image/png" }),
    );

    // The id the uploader reported, never a storage key: the server resolves
    // it and checks the file is this user's, which is what lets the bucket
    // stay private. Before this the page showed a toast and dropped it.
    await waitFor(() => {
      expect(patched).toEqual({ avatar_file_id: FIXTURE_FILE_ID });
    });
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
