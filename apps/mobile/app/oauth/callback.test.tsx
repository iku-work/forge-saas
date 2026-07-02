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
  router: { replace: vi.fn(), push: vi.fn() },
  params: {},
}));
vi.mock("expo-router", () => ({
  useLocalSearchParams: () => mocks.params,
  useRouter: () => mocks.router,
  Link: ({ children }: { children: ReactNode }) => children,
}));

const { default: OAuthCallbackScreen } = await import("./callback");
const { ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY } = await import(
  "../../src/lib/api"
);

describe("oauth/callback", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    secureStore.setItemAsync.mockResolvedValue(undefined);
    mocks.params = {};
  });

  it("stores tokens carried on the deep link and navigates to the dashboard", async () => {
    mocks.params = {
      access_token: "oauth-access-token",
      refresh_token: "oauth-refresh-token",
    };

    render(<OAuthCallbackScreen />);

    await waitFor(() => {
      expect(secureStore.setItemAsync).toHaveBeenCalledWith(
        ACCESS_TOKEN_KEY,
        "oauth-access-token",
      );
    });
    expect(secureStore.setItemAsync).toHaveBeenCalledWith(
      REFRESH_TOKEN_KEY,
      "oauth-refresh-token",
    );
    await waitFor(() => {
      expect(mocks.router.replace).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("shows an error and a way back to login when no tokens are attached", async () => {
    render(<OAuthCallbackScreen />);

    expect(await screen.findByText(/couldn't complete that sign-in/i)).toBeTruthy();
    expect(screen.getByText("Back to log in")).toBeTruthy();
    expect(secureStore.setItemAsync).not.toHaveBeenCalled();
  });
});
