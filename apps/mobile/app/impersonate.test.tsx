/**
 * T-261: the confirmation screen a `forge://impersonate` deep link lands on.
 *
 * The screen's job is to make adopting someone else's identity a decision
 * rather than a side effect of opening a link, so what is tested here is the
 * decision: nothing is adopted before Confirm, Cancel burns the link, and a
 * dead link says so instead of failing silently on the next screen.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  // Reassigned per test -- a deep link is exactly as trustworthy as whatever
  // the OS handed the app, including one with no token in it at all. Declared
  // through an annotated binding rather than an `as` cast so both shapes type-
  // check without one.
  const params: { token?: string; user_email?: string } = {};
  return {
    params,
    router: { replace: vi.fn(), push: vi.fn() },
    previewHandoff: vi.fn(),
    acceptHandoff: vi.fn(),
    declineHandoff: vi.fn(),
    bootstrapSession: vi.fn(),
  };
});

vi.mock("expo-router", () => ({
  useLocalSearchParams: () => mocks.params,
  useRouter: () => mocks.router,
}));
vi.mock("../src/lib/impersonation", () => ({
  previewHandoff: mocks.previewHandoff,
  acceptHandoff: mocks.acceptHandoff,
  declineHandoff: mocks.declineHandoff,
}));
vi.mock("../src/lib/auth", () => ({
  bootstrapSession: mocks.bootstrapSession,
}));

const { default: ImpersonateConfirmScreen } = await import("./impersonate");

const PREVIEW = {
  user_email: "jane@example.com",
  reason: "Upload sheet crashes on iOS, ticket 8812",
  session_expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
};

describe("impersonate confirmation screen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.params = { token: "exchange-abc", user_email: "jane@example.com" };
    mocks.previewHandoff.mockResolvedValue(PREVIEW);
    mocks.acceptHandoff.mockResolvedValue({
      email: PREVIEW.user_email,
      expiresAt: Date.now() + 30 * 60_000,
      reason: PREVIEW.reason,
    });
    mocks.declineHandoff.mockResolvedValue(undefined);
    mocks.bootstrapSession.mockResolvedValue(true);
  });

  it("shows who is being impersonated and why before anything is adopted", async () => {
    render(<ImpersonateConfirmScreen />);

    expect(await screen.findByText("User: jane@example.com")).toBeTruthy();
    expect(
      screen.getByText("Reason: Upload sheet crashes on iOS, ticket 8812"),
    ).toBeTruthy();
    // The countdown is the session's remaining half hour, not the link's two
    // minutes -- the two are different deadlines and swapping them would show
    // an operator a session that looks about to end.
    expect(screen.getByText(/Session ends in (30:00|29:5\d)/)).toBeTruthy();
    expect(mocks.acceptHandoff).not.toHaveBeenCalled();
  });

  it("adopts the session and lands on the dashboard on confirm", async () => {
    render(<ImpersonateConfirmScreen />);
    await screen.findByText("User: jane@example.com");

    await userEvent.click(screen.getByLabelText("Confirm"));

    await waitFor(() => {
      expect(mocks.acceptHandoff).toHaveBeenCalledWith("exchange-abc");
    });
    // Without this the (app) group's guard would bounce straight back to
    // /login: the token is stored but useSession() hasn't resolved a user yet.
    expect(mocks.bootstrapSession).toHaveBeenCalled();
    await waitFor(() => {
      expect(mocks.router.replace).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("invalidates the link and returns to sign-in on cancel", async () => {
    render(<ImpersonateConfirmScreen />);
    await screen.findByText("User: jane@example.com");

    await userEvent.click(screen.getByLabelText("Cancel"));

    await waitFor(() => {
      expect(mocks.declineHandoff).toHaveBeenCalledWith("exchange-abc");
    });
    expect(mocks.acceptHandoff).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(mocks.router.replace).toHaveBeenCalledWith("/login");
    });
  });

  it("reports an expired or already-used link instead of a blank screen", async () => {
    mocks.previewHandoff.mockRejectedValue(new Error("401"));

    render(<ImpersonateConfirmScreen />);

    expect(
      await screen.findByText(/This handoff link is invalid or has expired/),
    ).toBeTruthy();
  });

  it("treats a link with no token as invalid without calling the server", async () => {
    mocks.params = { user_email: "jane@example.com" };

    render(<ImpersonateConfirmScreen />);

    expect(
      await screen.findByText(/This handoff link is invalid or has expired/),
    ).toBeTruthy();
    expect(mocks.previewHandoff).not.toHaveBeenCalled();
  });
});
