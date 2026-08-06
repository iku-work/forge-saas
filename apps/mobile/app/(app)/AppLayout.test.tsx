// Named AppLayout.test.tsx, not _layout.test.tsx: expo-router's typed-routes
// generator treats any filename starting with "_layout" as a second layout
// candidate for this directory, which silently aborted route generation for
// this entire (app) group -- every route under it (dashboard, files,
// notifications, settings) was missing from .expo/types/router.d.ts, so
// router.push("/dashboard") etc. failed to typecheck. Co-locating a route's
// test next to it is expo-router's own recommended pattern (see
// metro.config.js's blockList comment) -- just never name it "_layout.*".
import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  useSession: vi.fn(),
  useImpersonation: vi.fn(),
  registerPushToken: vi.fn(),
  setupNotificationHandlers: vi.fn(),
}));

vi.mock("../../src/lib/auth", () => ({ useSession: mocks.useSession }));
vi.mock("../../src/lib/push", () => ({
  registerPushToken: mocks.registerPushToken,
  setupNotificationHandlers: mocks.setupNotificationHandlers,
}));
// Mocked rather than left real: the module reaches expo-secure-store, whose
// native EventEmitter doesn't exist under jsdom.
vi.mock("../../src/lib/impersonation", () => ({
  useImpersonation: mocks.useImpersonation,
}));
vi.mock("../../src/components/NotificationBanner", () => ({
  NotificationBanner: () => null,
}));
vi.mock("expo-router", () => ({
  Redirect: () => null,
  Stack: () => null,
}));

const { default: AppLayout } = await import("./_layout");

describe("(app)/_layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.registerPushToken.mockResolvedValue(undefined);
    mocks.setupNotificationHandlers.mockReturnValue(vi.fn());
    mocks.useImpersonation.mockReturnValue(null);
  });

  it("registers for push notifications and sets up handlers once a user is signed in", () => {
    mocks.useSession.mockReturnValue({ user: { id: "u1" }, isLoading: false });

    render(<AppLayout />);

    expect(mocks.registerPushToken).toHaveBeenCalledTimes(1);
    expect(mocks.setupNotificationHandlers).toHaveBeenCalledTimes(1);
  });

  it("does not register for push notifications when signed out", () => {
    mocks.useSession.mockReturnValue({ user: null, isLoading: false });

    render(<AppLayout />);

    expect(mocks.registerPushToken).not.toHaveBeenCalled();
    expect(mocks.setupNotificationHandlers).not.toHaveBeenCalled();
  });

  it("tears down notification handlers on unmount", () => {
    const cleanup = vi.fn();
    mocks.setupNotificationHandlers.mockReturnValue(cleanup);
    mocks.useSession.mockReturnValue({ user: { id: "u1" }, isLoading: false });

    const { unmount } = render(<AppLayout />);
    unmount();

    expect(cleanup).toHaveBeenCalledTimes(1);
  });

  // T-261: the leak this prevents is not subtle. Registering here would point
  // the impersonated user's own notifications at the superadmin's phone, and
  // would keep pointing there after the session ended -- the backend has no
  // way to tell the registration was made under an impersonation token.
  it("does not register this device for push while impersonating", () => {
    mocks.useSession.mockReturnValue({ user: { id: "u1" }, isLoading: false });
    mocks.useImpersonation.mockReturnValue({
      email: "jane@example.com",
      expiresAt: Date.now() + 60_000,
      reason: "Ticket 8812",
    });

    render(<AppLayout />);

    expect(mocks.registerPushToken).not.toHaveBeenCalled();
    // Handlers still run: pushes this device was already subscribed to (the
    // superadmin's own) must keep working.
    expect(mocks.setupNotificationHandlers).toHaveBeenCalledTimes(1);
  });
});
