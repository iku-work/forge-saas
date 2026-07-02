import type { ReactNode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(),
}));
vi.mock("expo-secure-store", () => secureStore);

const mocks = vi.hoisted(() => ({
  previewInvitationApiV1InvitationsTokenGet: vi.fn(),
  acceptInvitationApiV1InvitationsTokenAcceptPost: vi.fn(),
  meApiV1AuthMeGet: vi.fn(),
  router: { replace: vi.fn(), push: vi.fn() },
}));
vi.mock("@forge/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@forge/api-client")>();
  return { ...actual, ...mocks };
});
vi.mock("expo-router", () => ({
  useLocalSearchParams: () => ({ token: "invite-abc" }),
  useRouter: () => mocks.router,
  Link: ({ children }: { children: ReactNode }) => children,
}));

const { default: InvitationScreen } = await import("./invitations/[token]");
const { ACCESS_TOKEN_KEY } = await import("../src/lib/api");
const { bootstrapSession } = await import("../src/lib/auth");

const PREVIEW = {
  org_id: "22222222-2222-2222-2222-222222222222",
  org_name: "Acme Inc",
  email: "jane@example.com",
  role: "member",
};

const USER = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "jane@example.com",
  full_name: null,
  avatar_url: null,
  locale: "en",
  email_verified_at: "2026-01-01T00:00:00Z",
  totp_enabled: false,
};

describe("invitations/[token]", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    // Establishes a deterministic, signed-out useSession() before each test
    // -- auth.ts's session store is a module-level singleton shared by every
    // it() in this file, so without this the second test's signed-in state
    // would leak backwards into the first.
    secureStore.getItemAsync.mockResolvedValue(null);
    await bootstrapSession();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("previews the invitation using the deep link token and prompts to log in when signed out", async () => {
    mocks.previewInvitationApiV1InvitationsTokenGet.mockResolvedValue({
      data: { data: PREVIEW, error: null },
    });

    render(<InvitationScreen />);

    await waitFor(() => {
      expect(
        mocks.previewInvitationApiV1InvitationsTokenGet,
      ).toHaveBeenCalledWith(
        expect.objectContaining({ path: { token: "invite-abc" } }),
      );
    });
    expect(await screen.findByText("You're invited")).toBeTruthy();
    expect(screen.getByText("Join Acme Inc as member.")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Accept invitation" }),
    ).toBeNull();
  });

  it("accepts the invitation and navigates to the dashboard once signed in", async () => {
    mocks.previewInvitationApiV1InvitationsTokenGet.mockResolvedValue({
      data: { data: PREVIEW, error: null },
    });
    mocks.acceptInvitationApiV1InvitationsTokenAcceptPost.mockResolvedValue({
      data: { data: null, error: null },
    });
    mocks.meApiV1AuthMeGet.mockResolvedValue({
      data: { data: USER, error: null },
    });
    secureStore.getItemAsync.mockImplementation((key: string) =>
      Promise.resolve(key === ACCESS_TOKEN_KEY ? "stored-access-token" : null),
    );
    await bootstrapSession();

    const user = userEvent.setup();
    render(<InvitationScreen />);

    const acceptButton = await screen.findByRole("button", {
      name: "Accept invitation",
    });
    await user.click(acceptButton);

    await waitFor(() => {
      expect(
        mocks.acceptInvitationApiV1InvitationsTokenAcceptPost,
      ).toHaveBeenCalledWith(
        expect.objectContaining({ path: { token: "invite-abc" } }),
      );
    });
    await waitFor(() => {
      expect(mocks.router.replace).toHaveBeenCalledWith("/dashboard");
    });
  });
});
