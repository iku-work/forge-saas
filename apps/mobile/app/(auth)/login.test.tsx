import type { ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(),
}));
vi.mock("expo-secure-store", () => secureStore);

const mocks = vi.hoisted(() => ({
  loginApiV1AuthLoginPost: vi.fn(),
  router: { replace: vi.fn(), push: vi.fn() },
}));
vi.mock("@forge/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@forge/api-client")>();
  return { ...actual, ...mocks };
});
vi.mock("expo-router", () => ({
  useRouter: () => mocks.router,
  Link: ({ children }: { children: ReactNode }) => children,
}));

const { default: LoginScreen } = await import("./login");

describe("(auth)/login", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    secureStore.getItemAsync.mockResolvedValue(null);
  });

  it("shows a validation error for an invalid email and does not call the API", async () => {
    const user = userEvent.setup();
    render(<LoginScreen />);

    await user.type(screen.getByLabelText("Email"), "not-an-email");
    await user.type(screen.getByLabelText("Password"), "irrelevant-password");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    // @forge/validation's email error is a @forge/i18n translation key
    // (T-069); FormField.tsx now translates it (T-070), same as
    // packages/ui's FormMessage does on web.
    expect(
      await screen.findByText("Enter a valid email address."),
    ).toBeTruthy();
    expect(mocks.loginApiV1AuthLoginPost).not.toHaveBeenCalled();
  });

  it("logs in and navigates to the dashboard on success", async () => {
    mocks.loginApiV1AuthLoginPost.mockResolvedValue({
      data: {
        data: {
          access_token: "access-token",
          refresh_token: "refresh-token",
          user: {
            id: "11111111-1111-1111-1111-111111111111",
            email: "jane@example.com",
            full_name: null,
            avatar_url: null,
            locale: "en",
            email_verified_at: "2026-01-01T00:00:00Z",
            totp_enabled: false,
          },
        },
        error: null,
      },
    });
    const user = userEvent.setup();
    render(<LoginScreen />);

    await user.type(screen.getByLabelText("Email"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-password-123");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => {
      expect(mocks.loginApiV1AuthLoginPost).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { email: "jane@example.com", password: "correct-password-123" },
        }),
      );
    });
    await waitFor(() => {
      expect(mocks.router.replace).toHaveBeenCalledWith("/dashboard");
    });
  });
});
