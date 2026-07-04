import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { AdminUserSummary } from "@forge/api-client";

import { envelope, makeUser } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();
const superadmin = makeUser(40, { is_superadmin: true });

function makeAdminUser(id: string, email: string): AdminUserSummary {
  return {
    id,
    email,
    full_name: null,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
  };
}

describe("AdminUsersPage", () => {
  it("debounces the search box instead of querying on every keystroke", async () => {
    const seenQueries: (string | null)[] = [];
    server.use(
      http.get(`${API}/api/v1/admin/users`, ({ request }) => {
        seenQueries.push(new URL(request.url).searchParams.get("q"));
        return HttpResponse.json(envelope([makeAdminUser("u1", "jane@example.com")]));
      }),
    );

    renderApp({ initialPath: "/admin/users", user: superadmin });
    await screen.findByText("jane@example.com");

    const search = screen.getByPlaceholderText("Search by email");
    await userEvent.type(search, "jane");

    // Only the initial (unfiltered) load and one debounced, fully-typed
    // query should ever reach the server -- not one request per keystroke.
    await waitFor(
      () => {
        expect(seenQueries.at(-1)).toBe("jane");
      },
      { timeout: 2000 },
    );
    expect(seenQueries.length).toBeLessThan("jane".length + 1);
  });

  it("shows a Load more button that fetches the next cursor page", async () => {
    server.use(
      http.get(`${API}/api/v1/admin/users`, ({ request }) => {
        const after = new URL(request.url).searchParams.get("after");
        if (after === null) {
          return HttpResponse.json({
            ...envelope([makeAdminUser("u1", "first@example.com")]),
            meta: { request_id: "req-1", next_cursor: "u1" },
          });
        }
        return HttpResponse.json(envelope([makeAdminUser("u2", "second@example.com")]));
      }),
    );

    renderApp({ initialPath: "/admin/users", user: superadmin });

    const loadMore = await screen.findByRole("button", { name: "Load more" });
    await userEvent.click(loadMore);

    await waitFor(() => {
      expect(screen.getByText("second@example.com")).toBeInTheDocument();
    });
    expect(screen.getByText("first@example.com")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });
});
