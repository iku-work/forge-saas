/**
 * T-261: the non-dismissable impersonation banner.
 *
 * "Visible on every screen" is a property of where it is mounted, not of the
 * component, so the last test asserts the mounting: the root layout renders it
 * as a sibling of <Slot />, which is every screen this app has. A test that
 * only rendered the component in isolation would keep passing after someone
 * moved it into the (app) group and quietly lost it on the auth screens.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  useImpersonation: vi.fn(),
  endImpersonation: vi.fn(),
  router: { replace: vi.fn() },
}));

vi.mock("expo-router", () => ({
  useRouter: () => mocks.router,
}));
vi.mock("../lib/impersonation", () => ({
  useImpersonation: mocks.useImpersonation,
  endImpersonation: mocks.endImpersonation,
}));

const { ImpersonationBanner } = await import("./ImpersonationBanner");

describe("ImpersonationBanner", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.endImpersonation.mockResolvedValue(undefined);
  });

  it("renders nothing when the session is the operator's own", () => {
    mocks.useImpersonation.mockReturnValue(null);

    const { container } = render(<ImpersonationBanner />);

    // jest-dom isn't wired up in this app's setup, so this is the plain form
    // of toBeEmptyDOMElement().
    expect(container.innerHTML).toBe("");
  });

  it("names the impersonated user and counts the session down", () => {
    mocks.useImpersonation.mockReturnValue({
      email: "jane@example.com",
      expiresAt: Date.now() + 12 * 60_000 + 34_000,
      reason: "Ticket 8812",
    });

    render(<ImpersonationBanner />);

    expect(
      screen.getByText(/Impersonating jane@example.com · 12:3\d left/),
    ).toBeTruthy();
  });

  it("offers no way to dismiss it other than ending the session", async () => {
    mocks.useImpersonation.mockReturnValue({
      email: "jane@example.com",
      expiresAt: Date.now() + 60_000,
      reason: "Ticket 8812",
    });

    render(<ImpersonationBanner />);

    // One control, and it is the one that ends the session -- no close, no
    // "hide", nothing that leaves an operator acting as someone else with no
    // indication of it.
    expect(screen.getAllByRole("button")).toHaveLength(1);

    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(mocks.endImpersonation).toHaveBeenCalledTimes(1);
    });
    // Back to sign-in, not to a previous session: mobile holds one session per
    // install, so there is nothing underneath this one.
    await waitFor(() => {
      expect(mocks.router.replace).toHaveBeenCalledWith("/login");
    });
  });

  it("signs the device out even when ending the session fails server-side", async () => {
    mocks.endImpersonation.mockRejectedValue(new Error("offline"));
    mocks.useImpersonation.mockReturnValue({
      email: "jane@example.com",
      expiresAt: Date.now() + 60_000,
      reason: "Ticket 8812",
    });

    render(<ImpersonationBanner />);
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(mocks.router.replace).toHaveBeenCalledWith("/login");
    });
  });
});

describe("root layout", () => {
  it("mounts the banner outside the (app) group, so it covers every screen", async () => {
    // Vitest's root is apps/mobile (see vitest.config.ts), so this resolves
    // without needing import.meta.url -- which under this transform is not a
    // file: URL.
    const source = await import("node:fs").then((fs) =>
      fs.readFileSync("app/_layout.tsx", "utf-8"),
    );

    // Above the Slot: every route the router can resolve renders underneath
    // it, auth screens included. T-263 put GlobalBannerBar between the two
    // (an operator inside someone else's session must be told *that* first),
    // so this now asserts the order of all three rather than an adjacent
    // pair -- matched as a sequence rather than by comparing indexes, because
    // the surrounding comments mention the Slot too and would satisfy a
    // looser check.
    expect(source).toMatch(
      /<ImpersonationBanner \/>[\s\S]*?<GlobalBannerBar \/>[\s\S]*?<RoutedScreens \/>/,
    );
  });
});
