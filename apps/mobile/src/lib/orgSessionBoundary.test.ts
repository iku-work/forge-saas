import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(),
}));

vi.mock("expo-secure-store", () => secureStore);

import { getCurrentOrgId } from "@forge/api-client";

import { clearSession } from "./auth";
import { ensureCurrentOrg } from "./org";

const ORG_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const ORG_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

function orgsResponse(orgId: string): Response {
  return new Response(
    JSON.stringify({
      data: [
        {
          id: orgId,
          name: "Acme Inc",
          slug: "acme-inc",
          logo_url: null,
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
      error: null,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

/**
 * `ensureCurrentOrg()` memoises its lookup, which is right -- every org-scoped
 * screen calls it and only one of them should make the request. What was
 * wrong is how long "once" lasted: nothing cleared the memo, so the pinned
 * org outlived the session that resolved it, and the next user's requests
 * went out carrying the previous user's X-Org-ID.
 */
describe("apps/mobile/src/lib/org across a session boundary", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    secureStore.getItemAsync.mockResolvedValue(null);
    secureStore.setItemAsync.mockResolvedValue(undefined);
    secureStore.deleteItemAsync.mockResolvedValue(undefined);
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    clearSession();
  });

  it("re-resolves the org for the next session instead of reusing the last one", async () => {
    fetchMock.mockResolvedValueOnce(orgsResponse(ORG_A));
    await ensureCurrentOrg();
    expect(getCurrentOrgId()).toBe(ORG_A);

    // The session ends -- a logout, an ended impersonation, or an expired
    // refresh token; all three route through clearSession().
    clearSession();
    expect(getCurrentOrgId()).toBeNull();

    fetchMock.mockResolvedValueOnce(orgsResponse(ORG_B));
    await ensureCurrentOrg();

    expect(getCurrentOrgId()).toBe(ORG_B);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
