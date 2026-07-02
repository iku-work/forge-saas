import type { ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(),
}));
vi.mock("expo-secure-store", () => secureStore);

const mocks = vi.hoisted(() => ({
  verifyEmailApiV1AuthVerifyEmailPost: vi.fn(),
}));
vi.mock("@forge/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@forge/api-client")>();
  return { ...actual, ...mocks };
});

// Stands in for Expo Router's real file-based route matching, which has no
// seam this Vite-based Vitest setup can drive (see the comment on
// vitest.config.ts's react-native-web alias): a real `forge://verify-email/abc`
// deep link is matched to this exact file by Expo Router itself purely from
// its location under app/(auth)/verify-email/[token].tsx, and hands it
// "abc" through this same hook. What's tested here is that the screen
// behind that match does the right thing with the token it's given.
vi.mock("expo-router", () => ({
  useLocalSearchParams: () => ({ token: "abc" }),
  Link: ({ children }: { children: ReactNode }) => children,
}));

const { default: VerifyEmailScreen } = await import("./verify-email/[token]");

describe("(auth)/verify-email/[token]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("verifies the token from the deep link and shows success", async () => {
    mocks.verifyEmailApiV1AuthVerifyEmailPost.mockResolvedValue({
      data: { data: null, error: null },
    });

    render(<VerifyEmailScreen />);

    await waitFor(() => {
      expect(mocks.verifyEmailApiV1AuthVerifyEmailPost).toHaveBeenCalledWith(
        expect.objectContaining({ body: { token: "abc" } }),
      );
    });
    expect(await screen.findByText(/email is verified/i)).toBeTruthy();
  });

  it("shows an error state when the token is rejected", async () => {
    mocks.verifyEmailApiV1AuthVerifyEmailPost.mockRejectedValue(
      new Error("This link is invalid or has expired."),
    );

    render(<VerifyEmailScreen />);

    expect(
      await screen.findByText("This link is invalid or has expired."),
    ).toBeTruthy();
  });
});
