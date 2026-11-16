import type { ReactNode } from "react";
import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import type { AdminOrgFile, AdminOrgMember } from "@forge/api-client";

import { server } from "../../test/server";

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: ReactNode }) => (
    <a href="#user">{children}</a>
  ),
  useParams: (options?: {
    select?: (params: { locale: string }) => unknown;
  }) => (options?.select ? options.select({ locale: "en" }) : { locale: "en" }),
}));

const { AdminOrgFilesTab, AdminOrgMembersTab } =
  await import("./AdminOrgContentTabs");

const API = getApiBaseUrl();
const ORG = "11111111-1111-1111-1111-111111111111";

function page<T>(data: T[], nextCursor: string | null = null) {
  return {
    data,
    error: null,
    meta: { request_id: "r", next_cursor: nextCursor },
  };
}

function file(
  id: string,
  status: string,
  filename = `${id}.png`,
): AdminOrgFile {
  return {
    id,
    filename,
    content_type: "image/png",
    size_bytes: 2048,
    status,
    uploaded_by_email: "uploader@example.com",
    created_at: "2026-11-01T00:00:00Z",
  };
}

function renderWith(node: ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AdminOrgMembersTab", () => {
  it("lists members and loads the next page", async () => {
    const first: AdminOrgMember = {
      user_id: "a",
      email: "owner@example.com",
      full_name: null,
      role: "owner",
      joined_at: "2026-11-01T00:00:00Z",
    };
    server.use(
      http.get(`${API}/api/v1/admin/orgs/:orgId/members`, ({ request }) => {
        const after = new URL(request.url).searchParams.get("after");
        return HttpResponse.json(
          after
            ? page([
                {
                  ...first,
                  user_id: "b",
                  email: "member@example.com",
                  role: "member",
                },
              ])
            : page([first], "a"),
        );
      }),
    );
    renderWith(<AdminOrgMembersTab orgId={ORG} />);

    expect(await screen.findByText("owner@example.com")).toBeInTheDocument();
    expect(screen.getByText("Owner")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("member@example.com")).toBeInTheDocument();
  });
});

describe("AdminOrgFilesTab", () => {
  it("offers Open only on a clean file", async () => {
    server.use(
      http.get(`${API}/api/v1/admin/orgs/:orgId/files`, () =>
        HttpResponse.json(
          page([file("clean", "clean"), file("scan", "scanning")]),
        ),
      ),
    );
    renderWith(<AdminOrgFilesTab orgId={ORG} />);

    const rows = await screen.findAllByTestId("admin-org-file-row");
    const [clean, scanning] = rows;
    if (!clean || !scanning) throw new Error("expected two rows");
    expect(
      within(clean).getByRole("button", { name: "Open" }),
    ).toBeInTheDocument();
    expect(within(clean).getByText("Clean")).toBeInTheDocument();
    expect(within(scanning).queryByRole("button", { name: "Open" })).toBeNull();
    expect(within(scanning).getByText("Being scanned")).toBeInTheDocument();
  });

  it("opens the preview in a new tab cut off from this page", async () => {
    server.use(
      http.get(`${API}/api/v1/admin/orgs/:orgId/files`, () =>
        HttpResponse.json(page([file("clean", "clean")])),
      ),
      http.post(`${API}/api/v1/admin/orgs/:orgId/files/:fileId/preview`, () =>
        HttpResponse.json({
          data: {
            url: "https://storage.example/preview",
            expires_at: "2026-11-16T00:01:00Z",
            inline: true,
          },
          error: null,
          meta: { request_id: "r" },
        }),
      ),
    );
    const tab = {
      opener: {} as unknown,
      location: { href: "" },
      close: vi.fn(),
    };
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    renderWith(<AdminOrgFilesTab orgId={ORG} />);

    await userEvent.click(await screen.findByRole("button", { name: "Open" }));

    await waitFor(() => {
      expect(tab.location.href).toBe("https://storage.example/preview");
    });
    expect(tab.opener).toBeNull();
  });

  it("asks for a reason before removing, and sends it", async () => {
    let body: unknown = null;
    server.use(
      http.get(`${API}/api/v1/admin/orgs/:orgId/files`, () =>
        HttpResponse.json(page([file("doomed", "clean", "doomed.png")])),
      ),
      http.post(
        `${API}/api/v1/admin/orgs/:orgId/files/:fileId/delete`,
        async ({ request }) => {
          body = await request.json();
          return HttpResponse.json({
            data: null,
            error: null,
            meta: { request_id: "r" },
          });
        },
      ),
    );
    renderWith(<AdminOrgFilesTab orgId={ORG} />);

    await userEvent.click(
      await screen.findByRole("button", { name: "Remove" }),
    );
    const dialog = await screen.findByTestId("remove-file-dialog");
    const submit = within(dialog).getByRole("button", { name: "Remove" });
    expect(submit).toBeDisabled();
    await userEvent.type(
      within(dialog).getByLabelText("Reason"),
      "Reported as inappropriate",
    );
    await userEvent.click(submit);

    await waitFor(() => {
      expect(body).toEqual({ reason: "Reported as inappropriate" });
    });
  });
});
