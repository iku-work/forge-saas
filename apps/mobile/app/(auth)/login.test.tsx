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
  login2FaApiV1Auth2FaLoginPost: vi.fn(),
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

  // --- the 2FA challenge -------------------------------------------------
  //
  // Enrolling a second factor used to lock an account out of this app
  // entirely: /auth/login answers every password sign-in with a challenge
  // once TOTP or a security key exists, and the screen rendered that as an
  // ordinary form error with nothing to type it into.

  const challengeResponse = {
    data: { data: { temp_token: "temp-token-abc" }, error: null },
  };

  const sessionResponse = {
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
          totp_enabled: true,
        },
      },
      error: null,
    },
  };

  async function signInUntilChallenged(): Promise<
    ReturnType<typeof userEvent.setup>
  > {
    mocks.loginApiV1AuthLoginPost.mockResolvedValue(challengeResponse);
    const user = userEvent.setup();
    render(<LoginScreen />);

    await user.type(screen.getByLabelText("Email"), "jane@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-password-123");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    return user;
  }

  it("swaps in the code form instead of failing when 2FA is required", async () => {
    await signInUntilChallenged();

    expect(
      await screen.findByLabelText("Authenticator code"),
    ).toBeTruthy();
    // The password form is gone, not merely annotated -- there is nothing
    // useful left to do with it at this point.
    expect(screen.queryByLabelText("Password")).toBeNull();
    expect(mocks.router.replace).not.toHaveBeenCalled();
  });

  it("finishes the sign-in with a code from the authenticator app", async () => {
    mocks.login2FaApiV1Auth2FaLoginPost.mockResolvedValue(sessionResponse);
    const user = await signInUntilChallenged();

    await user.type(await screen.findByLabelText("Authenticator code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await waitFor(() => {
      expect(mocks.login2FaApiV1Auth2FaLoginPost).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { temp_token: "temp-token-abc", code: "123456" },
        }),
      );
    });
    await waitFor(() => {
      expect(mocks.router.replace).toHaveBeenCalledWith("/dashboard");
    });
    // Mobile has no refresh cookie, so the tokens have to arrive in the
    // body and land in the secure store -- otherwise the session ends at
    // the first access-token expiry.
    expect(secureStore.setItemAsync).toHaveBeenCalled();
  });

  it("accepts a backup code in the same field", async () => {
    mocks.login2FaApiV1Auth2FaLoginPost.mockResolvedValue(sessionResponse);
    const user = await signInUntilChallenged();

    // The backend tries a backup code against the same field, so the form
    // must not validate for six digits -- the person reaching for a backup
    // code is the one who has lost their authenticator.
    await user.type(
      await screen.findByLabelText("Authenticator code"),
      "a1b2c3d4e5",
    );
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await waitFor(() => {
      expect(mocks.login2FaApiV1Auth2FaLoginPost).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { temp_token: "temp-token-abc", code: "a1b2c3d4e5" },
        }),
      );
    });
  });

  it("keeps the user on the challenge when the code is wrong", async () => {
    // The envelope the backend actually returns, not a bare Error:
    // getErrorMessage resolves `message_key` through this app's own
    // catalog, and a bare Error would instead surface its raw `message`
    // and prove nothing about that path.
    mocks.login2FaApiV1Auth2FaLoginPost.mockRejectedValue({
      data: null,
      error: {
        code: "authentication_failed",
        message_key: "errors.invalid_totp_code",
        message: "Invalid two-factor code",
      },
    });
    const user = await signInUntilChallenged();

    await user.type(await screen.findByLabelText("Authenticator code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("Invalid two-factor code.")).toBeTruthy();
    expect(mocks.router.replace).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Authenticator code")).toBeTruthy();
  });

  it("says where to go when the only second factor is a security key", async () => {
    await signInUntilChallenged();

    // WebAuthn is a browser API; React Native has no equivalent without a
    // native passkey module. Saying so is the honest version of not
    // offering the button.
    expect(
      await screen.findByText(
        "Signing in with a security key only works in a web browser.",
      ),
    ).toBeTruthy();
  });
});
