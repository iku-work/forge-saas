/**
 * T-263: the global announcement bar on mobile.
 *
 * Same shape as ImpersonationBanner.test.tsx next door, including its last
 * test: "visible on every screen" is a property of *where it is mounted*, not
 * of the component, so the root layout is asserted directly. A test that only
 * rendered this in isolation would keep passing after someone moved it into
 * the (app) group and quietly lost it on the auth screens.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  useGlobalBanner: vi.fn(),
}));

vi.mock("../lib/config", () => ({
  useGlobalBanner: mocks.useGlobalBanner,
}));

const { GlobalBannerBar } = await import("./GlobalBannerBar");

describe("GlobalBannerBar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when no banner is set", () => {
    mocks.useGlobalBanner.mockReturnValue(null);

    const { container } = render(<GlobalBannerBar />);

    // jest-dom isn't wired up in this app's setup, so this is the plain form
    // of toBeEmptyDOMElement().
    expect(container.innerHTML).toBe("");
  });

  it("renders the operator's message verbatim", () => {
    mocks.useGlobalBanner.mockReturnValue({
      type: "warning",
      message: "Uploads are degraded until 14:00 UTC.",
      dismissible: true,
    });

    render(<GlobalBannerBar />);

    // Not translated and not reworded: the operator wrote one string for
    // everyone, and this is it.
    expect(
      screen.getByText("Uploads are degraded until 14:00 UTC."),
    ).toBeTruthy();
  });

  it("hides itself once dismissed", async () => {
    mocks.useGlobalBanner.mockReturnValue({
      type: "info",
      message: "Scheduled maintenance at 03:00 UTC.",
      dismissible: true,
    });

    render(<GlobalBannerBar />);
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() => {
      expect(
        screen.queryByText("Scheduled maintenance at 03:00 UTC."),
      ).toBeNull();
    });
  });

  it("comes back when the operator posts a different message", async () => {
    // Dismissal is keyed on the message, not on a boolean: an operator who
    // changes the banner is saying something new, which a previous dismissal
    // cannot have covered.
    mocks.useGlobalBanner.mockReturnValue({
      type: "info",
      message: "First announcement.",
      dismissible: true,
    });

    const { rerender } = render(<GlobalBannerBar />);
    await userEvent.click(screen.getByRole("button"));
    await waitFor(() => {
      expect(screen.queryByText("First announcement.")).toBeNull();
    });

    mocks.useGlobalBanner.mockReturnValue({
      type: "critical",
      message: "Second announcement.",
      dismissible: true,
    });
    rerender(<GlobalBannerBar />);

    expect(screen.getByText("Second announcement.")).toBeTruthy();
  });

  it("offers no dismiss control when the operator pinned it", () => {
    mocks.useGlobalBanner.mockReturnValue({
      type: "critical",
      message: "Read-only while we migrate.",
      dismissible: false,
    });

    render(<GlobalBannerBar />);

    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("root layout", () => {
  it("mounts the bar below the impersonation banner and above every screen", async () => {
    // Vitest's root is apps/mobile (see vitest.config.ts), so this resolves
    // without needing import.meta.url.
    const source = await import("node:fs").then((fs) =>
      fs.readFileSync("app/_layout.tsx", "utf-8"),
    );

    // Order is the assertion: an operator inside an impersonation session has
    // to be told *that* first, so ImpersonationBanner stays on top. Both sit
    // above <Slot />, i.e. above every route the router can resolve.
    expect(source).toMatch(
      /<ImpersonationBanner \/>[\s\S]*?<GlobalBannerBar \/>[\s\S]*?<RoutedScreens \/>/,
    );
  });
});
