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
  resetPasswordApiV1AuthResetPasswordPost: vi.fn(),
}));
vi.mock("@forge/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@forge/api-client")>();
  return { ...actual, ...mocks };
});

vi.mock("expo-router", () => ({
  useLocalSearchParams: () => ({ token: "reset-abc" }),
  Link: ({ children }: { children: ReactNode }) => children,
}));

const { default: ResetPasswordScreen } =
  await import("./reset-password/[token]");

describe("(auth)/reset-password/[token]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("submits the deep link token together with the new password", async () => {
    mocks.resetPasswordApiV1AuthResetPasswordPost.mockResolvedValue({
      data: { data: null, error: null },
    });
    const user = userEvent.setup();

    render(<ResetPasswordScreen />);
    await user.type(
      screen.getByLabelText("New password"),
      "Correct-Password-123",
    );
    await user.type(
      screen.getByLabelText("Repeat new password"),
      "Correct-Password-123",
    );
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    await waitFor(() => {
      expect(
        mocks.resetPasswordApiV1AuthResetPasswordPost,
      ).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { token: "reset-abc", new_password: "Correct-Password-123" },
        }),
      );
    });
    expect(await screen.findByText("Password updated")).toBeTruthy();
  });
});
