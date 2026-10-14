import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { MembershipPublic } from "@forge/api-client";

import { expectNoA11yViolations } from "../../test/axe";
import { fixtureOrg, makeUser } from "../../test/appShellFixtures";
import { server } from "../../test/server";
import {
  getVirtualScrollContainer,
  installVirtualLayout,
  scrollToBottom,
} from "../../test/virtualLayout";

// T-241: virtualization of the member list, exercised against the *real*
// @tanstack/react-virtual (MembersSettingsPage.test.tsx covers the page's
// behaviour with a handful of rows; this file is about what happens at
// 1,000). installVirtualLayout gives jsdom the layout numbers the
// virtualizer needs -- see test/virtualLayout.ts for why that's necessary.

// Must match MembersSettingsPage's own constants: the DOM-node budget below
// is only meaningful relative to the viewport/row ratio the page ships.
const ROW_HEIGHT_PX = 72;
const VIEWPORT_HEIGHT_PX = 420;

// The budget the task sets: 1,000 rows of data, fewer than 50 row nodes.
const MAX_ROW_NODES = 50;

const hooks = vi.hoisted(() => ({
  useAuth: vi.fn(),
  useOrg: vi.fn(),
  usePermission: vi.fn(),
}));
vi.mock("@forge/hooks", () => hooks);

const { MembersSettingsPage } = await import("./MembersSettingsPage");

const API = getApiBaseUrl();
const user = makeUser(4);

function makeMembers(count: number, offset = 0): MembershipPublic[] {
  return Array.from({ length: count }, (_, index) => {
    const n = offset + index;
    return {
      id: `33333333-3333-3333-3333-${String(n).padStart(12, "0")}`,
      user_id: `44444444-4444-4444-4444-${String(n).padStart(12, "0")}`,
      org_id: fixtureOrg.id,
      role: "member",
      invited_by: null,
      created_at: "2026-01-01T00:00:00Z",
    };
  });
}

function membersEnvelope(
  members: MembershipPublic[],
  nextCursor: string | null,
): {
  data: MembershipPublic[];
  error: null;
  meta: { request_id: string; next_cursor: string | null };
} {
  return {
    data: members,
    error: null,
    meta: { request_id: "req-1", next_cursor: nextCursor },
  };
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MembersSettingsPage />
    </QueryClientProvider>,
  );
}

describe("MembersSettingsPage virtualization", () => {
  let restoreLayout: () => void;

  beforeEach(() => {
    restoreLayout = installVirtualLayout({
      rowHeight: ROW_HEIGHT_PX,
      viewportHeight: VIEWPORT_HEIGHT_PX,
    });
    vi.clearAllMocks();
    hooks.useAuth.mockReturnValue({
      user,
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
      register: vi.fn(),
    });
    hooks.useOrg.mockReturnValue({
      org: fixtureOrg,
      orgs: [fixtureOrg],
      role: "owner",
      isLoading: false,
      switchOrg: vi.fn(),
      refreshOrgs: vi.fn(),
    });
    hooks.usePermission.mockReturnValue(true);
  });

  afterEach(() => {
    restoreLayout();
  });

  it("renders far fewer row nodes than members for a 1,000-member org", async () => {
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(membersEnvelope(makeMembers(1000), null)),
      ),
    );

    renderPage();

    await screen.findAllByTestId("member-row");
    const rows = screen.getAllByTestId("member-row");
    expect(rows.length).toBeLessThan(MAX_ROW_NODES);
    // ...and the rows that do exist are real rows, not an empty shell: the
    // spacer is sized for all 1,000 so the scrollbar is honest.
    expect(rows.length).toBeGreaterThan(0);
    expect(screen.getByRole("list")).toHaveStyle({
      height: `${String(1000 * ROW_HEIGHT_PX)}px`,
    });
    expect(screen.getAllByRole("listitem").length).toBe(rows.length);
  });

  it("scrolling to the bottom of the loaded rows fetches exactly one next page", async () => {
    const requestedCursors: (string | null)[] = [];
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/members`, ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("after");
        requestedCursors.push(cursor);
        return HttpResponse.json(
          cursor === null
            ? membersEnvelope(makeMembers(40), "cursor-page-2")
            : membersEnvelope(makeMembers(40, 40), "cursor-page-3"),
        );
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(requestedCursors).toEqual([null]);
    });
    // The first screenful is nowhere near the end of 40 loaded rows, so
    // nothing has been fetched beyond page one yet.
    expect(screen.getAllByTestId("member-row").length).toBeLessThan(40);

    const container = getVirtualScrollContainer();
    act(() => {
      scrollToBottom(container);
    });

    await waitFor(() => {
      expect(requestedCursors).toEqual([null, "cursor-page-2"]);
    });
    // Page two lands, the rendered range is no longer near the end of the
    // (now 80) loaded rows, and no third request follows.
    await waitFor(() => {
      expect(screen.getByRole("list")).toHaveStyle({
        height: `${String(80 * ROW_HEIGHT_PX)}px`,
      });
    });
    expect(requestedCursors).toEqual([null, "cursor-page-2"]);
  });

  it("has no a11y violations with the list virtualized", async () => {
    server.use(
      http.get(`${API}/api/v1/orgs/:orgId/members`, () =>
        HttpResponse.json(membersEnvelope(makeMembers(200), null)),
      ),
    );

    const { container } = renderPage();
    await screen.findAllByTestId("member-row");

    await expectNoA11yViolations(container);
  });
});
