import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { UserPublic } from "@forge/api-client";

const hooks = vi.hoisted(() => ({
  useAuth: vi.fn(),
  useLocale: vi.fn(() => ({
    locale: "en",
    supportedLocales: ["en", "fi"],
    setLocale: vi.fn(),
  })),
}));
vi.mock("@forge/hooks", () => hooks);

const { ProfileSettingsPage } = await import("./ProfileSettingsPage");

const user: UserPublic = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "jane@example.com",
  full_name: "Jane Doe",
  avatar_url: null,
  locale: "en",
  email_verified_at: "2026-01-01T00:00:00Z",
  totp_enabled: false,
};

describe("ProfileSettingsPage", () => {
  it("shows a loading skeleton while the user is bootstrapping", () => {
    hooks.useAuth.mockReturnValue({ user: null, isLoading: true });

    render(<ProfileSettingsPage />);

    expect(
      screen.getByRole("heading", { name: "Profile" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(user.email)).not.toBeInTheDocument();
  });

  it("renders the user's name and email once loaded", () => {
    hooks.useAuth.mockReturnValue({ user, isLoading: false });

    render(<ProfileSettingsPage />);

    expect(screen.getByText("Jane Doe")).toBeInTheDocument();
    expect(screen.getByText("jane@example.com")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /drop files here/i }),
    ).toBeInTheDocument();
  });

  it("falls back to a dash when the user has no display name", () => {
    hooks.useAuth.mockReturnValue({
      user: { ...user, full_name: null },
      isLoading: false,
    });

    render(<ProfileSettingsPage />);

    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
