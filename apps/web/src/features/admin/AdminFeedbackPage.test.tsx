import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { AdminFeedbackItem } from "@forge/api-client";

import { envelope, makeUser } from "../../test/appShellFixtures";
import { renderApp } from "../../test/render";
import { server } from "../../test/server";

const API = getApiBaseUrl();
const superadmin = makeUser(41, { is_superadmin: true });

const ITEM: AdminFeedbackItem = {
  id: "fb-1",
  category: "bug",
  message: "The upload button does nothing",
  page: "/en/files",
  created_at: "2026-11-17T00:00:00Z",
  resolved_at: null,
  user_id: "u-1",
  user_email: "sender@example.com",
  org_id: "o-1",
  org_name: "Acme",
};

describe("AdminFeedbackPage", () => {
  it("lists open feedback with who sent it and from where", async () => {
    let status: string | null = null;
    server.use(
      http.get(`${API}/api/v1/admin/feedback`, ({ request }) => {
        status = new URL(request.url).searchParams.get("status");
        return HttpResponse.json(envelope([ITEM]));
      }),
    );

    renderApp({ initialPath: "/admin/feedback", user: superadmin });

    expect(await screen.findByText(ITEM.message)).toBeInTheDocument();
    expect(screen.getByText("sender@example.com")).toBeInTheDocument();
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("/en/files")).toBeInTheDocument();
    expect(status).toBe("open");
  });

  it("marks an item handled and reloads the list", async () => {
    let resolved = false;
    server.use(
      http.get(`${API}/api/v1/admin/feedback`, ({ request }) => {
        const wanted = new URL(request.url).searchParams.get("status");
        const open = wanted === "open" && !resolved;
        return HttpResponse.json(envelope(open ? [ITEM] : []));
      }),
      http.post(`${API}/api/v1/admin/feedback/fb-1/resolve`, () => {
        resolved = true;
        return HttpResponse.json(
          envelope({ ...ITEM, resolved_at: "2026-11-17T01:00:00Z" }),
        );
      }),
    );

    renderApp({ initialPath: "/admin/feedback", user: superadmin });
    await userEvent.click(
      await screen.findByRole("button", { name: "Mark handled" }),
    );

    await waitFor(() => {
      expect(screen.getByText("No open feedback.")).toBeInTheDocument();
    });
  });
});
