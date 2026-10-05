import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiBaseUrl } from "@forge/api-client";
import { t } from "@forge/i18n";

import {
  envelope,
  errorEnvelope,
  fixtureOrg,
  makeStoredFile,
} from "../../test/appShellFixtures";
import { server } from "../../test/server";

const hooks = vi.hoisted(() => ({
  // The row menu's dropdown reads the text direction to place itself.
  useDirection: vi.fn(() => "ltr"),
  useOrg: vi.fn(),
  // Defaults to "yes" so every pre-existing test here keeps exercising the
  // owner's view of the page; the read-only cases below set it to false.
  usePermission: vi.fn(() => true),
  useModules: vi.fn(() => ({
    fileStorage: true,
    billing: false,
    notifications: false,
    featureFlags: false,
    gdpr: false,
    admin: false,
    oauth: false,
    totp2fa: false,
    multiOrg: false,
    usageMetering: false,
    outboundWebhooks: false,
    marketing: false,
    publicApi: false,
    enterpriseSso: false,
  })),
}));
vi.mock("@forge/hooks", () => hooks);

// Auto-succeed XHR PUTs so the embedded FileUploader renders without errors
class AutoSuccessXHR {
  readonly upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
  status = 200;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open = vi.fn();
  setRequestHeader = vi.fn();
  abort = vi.fn();
  send = vi.fn();
}

beforeEach(() => {
  vi.stubGlobal("XMLHttpRequest", AutoSuccessXHR);
});

const { FilesPage } = await import("./FilesPage");

const API = getApiBaseUrl();

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FilesPage />
    </QueryClientProvider>,
  );
}

/** Delete lives in the row's menu now (ticket 09): open it, choose Delete. */
async function openDeleteFromMenu(): Promise<void> {
  await userEvent.click(
    await screen.findByRole("button", { name: /^Actions for/ }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Delete" }),
  );
}

describe("FilesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hooks.usePermission.mockReturnValue(true);
    hooks.useOrg.mockReturnValue({
      org: fixtureOrg,
      orgs: [fixtureOrg],
      role: "owner",
      isLoading: false,
      switchOrg: vi.fn(),
      refreshOrgs: vi.fn(),
    });
  });

  it("renders the page title", async () => {
    server.use(
      http.get(`${API}/api/v1/files`, () => HttpResponse.json(envelope([]))),
    );

    renderPage();

    expect(
      await screen.findByRole("heading", { name: "Files" }),
    ).toBeInTheDocument();
  });

  it("shows an empty state that explains the feature, not just its absence", async () => {
    // F-010: the assertion is on the *explanation*, not only the heading --
    // "No files yet" alone is what this replaced.
    server.use(
      http.get(`${API}/api/v1/files`, () => HttpResponse.json(envelope([]))),
    );

    renderPage();

    const empty = await screen.findByTestId("empty-state");
    expect(empty).toHaveTextContent("No files yet");
    expect(empty).toHaveTextContent(/scanned for viruses/i);
  });

  it("renders the file list with filename and size", async () => {
    const file = makeStoredFile(1);
    server.use(
      http.get(`${API}/api/v1/files`, () =>
        HttpResponse.json(envelope([file])),
      ),
    );

    renderPage();

    expect(await screen.findByText(file.filename)).toBeInTheDocument();
  });

  it("asks before deleting, naming the file (F-009)", async () => {
    // Replaces a window.confirm() that said only "Delete this file?" -- and
    // that jsdom stubs as always-false, so the old path could not be
    // exercised at all.
    const file = makeStoredFile(1);
    server.use(
      http.get(`${API}/api/v1/files`, () =>
        HttpResponse.json(envelope([file])),
      ),
    );

    renderPage();
    await openDeleteFromMenu();

    const dialog = await screen.findByTestId("confirm-dialog");
    expect(dialog).toHaveTextContent(file.filename);
  });

  it("does not delete when the confirmation is cancelled", async () => {
    let deleteCalled = false;
    const file = makeStoredFile(1);
    server.use(
      http.get(`${API}/api/v1/files`, () =>
        HttpResponse.json(envelope([file])),
      ),
      http.delete(`${API}/api/v1/files/:id`, () => {
        deleteCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderPage();
    await openDeleteFromMenu();
    await userEvent.click(await screen.findByTestId("confirm-dialog-cancel"));

    await waitFor(() => {
      expect(screen.queryByTestId("confirm-dialog")).not.toBeInTheDocument();
    });
    expect(deleteCalled).toBe(false);
  });

  it("deletes once confirmed", async () => {
    const deleted: string[] = [];
    const file = makeStoredFile(1);
    server.use(
      http.get(`${API}/api/v1/files`, () =>
        HttpResponse.json(envelope([file])),
      ),
      http.delete(`${API}/api/v1/files/:id`, ({ params }) => {
        deleted.push(String(params.id));
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderPage();
    await openDeleteFromMenu();
    await userEvent.click(await screen.findByTestId("confirm-dialog-confirm"));

    await waitFor(() => {
      expect(deleted).toEqual([file.id]);
    });
  });

  it("shows an error alert when the files request fails", async () => {
    server.use(
      http.get(`${API}/api/v1/files`, () =>
        HttpResponse.json(errorEnvelope("network down"), { status: 500 }),
      ),
    );

    renderPage();

    expect(
      await screen.findByText(t("errors.internal_error", {}, "en")),
    ).toBeInTheDocument();
  });

  it("shows the drop zone for uploading", async () => {
    server.use(
      http.get(`${API}/api/v1/files`, () => HttpResponse.json(envelope([]))),
    );

    renderPage();

    expect(
      await screen.findByRole("button", { name: /drop files here/i }),
    ).toBeInTheDocument();
  });

  // --- what the page tells the truth about -------------------------------
  //
  // Repo finding 12: a MEMBER saw an uploader and a delete button that the
  // API answers with 403, a download button that 409s on an unscanned file,
  // and nothing at all when either failed -- both handlers discarded their
  // errors.

  it("hides the uploader and delete from someone who cannot manage files", async () => {
    hooks.usePermission.mockReturnValue(false);
    const file = makeStoredFile(1);
    server.use(
      http.get(`${API}/api/v1/files`, () =>
        HttpResponse.json(envelope([file])),
      ),
    );

    renderPage();

    expect(await screen.findByText(file.filename)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Actions for/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Drop files here")).not.toBeInTheDocument();
    // Read access is real access: download stays.
    expect(screen.getByRole("button", { name: "Download" })).toBeEnabled();
  });

  it("points a read-only member somewhere useful when there is nothing here", async () => {
    hooks.usePermission.mockReturnValue(false);
    server.use(
      http.get(`${API}/api/v1/files`, () => HttpResponse.json(envelope([]))),
    );

    renderPage();

    const empty = await screen.findByTestId("empty-state");
    // Not "use the uploader above" -- there is no uploader above for them.
    expect(empty).toHaveTextContent(/ask an admin/i);
  });

  it("says a file is being scanned rather than calling it ready", async () => {
    const file = makeStoredFile(1, { virus_scan_result: null });
    server.use(
      http.get(`${API}/api/v1/files`, () =>
        HttpResponse.json(envelope([file])),
      ),
    );

    renderPage();

    // `status` is already "ready" at this point -- the scan runs after the
    // upload is confirmed -- so showing it verbatim promised a download
    // that scan_gate refuses.
    expect(await screen.findByText("Scanning")).toBeInTheDocument();
    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
  });

  it("notices a pending scan finish while the page stays open, then stops asking (R7)", async () => {
    // Nothing used to tell the page a scan had finished: one invalidation
    // on confirm, then "Scanning" for as long as the tab stayed open.
    let requests = 0;
    const pending = makeStoredFile(1, {
      virus_scan_result: null,
      updated_at: new Date().toISOString(),
    });
    server.use(
      http.get(`${API}/api/v1/files`, () => {
        requests += 1;
        const file =
          requests === 1 ? pending : { ...pending, virus_scan_result: "clean" };
        return HttpResponse.json(envelope([file]));
      }),
    );

    renderPage();

    expect(await screen.findByText("Scanning")).toBeInTheDocument();
    await waitFor(
      () => {
        expect(screen.getByRole("button", { name: "Download" })).toBeEnabled();
      },
      { timeout: 6_000 },
    );
    const settled = requests;
    // Past the next poll interval: with nothing pending, nothing polls.
    await new Promise((resolve) => setTimeout(resolve, 2_500));
    expect(requests).toBe(settled);
  }, 15_000);

  it("says when a scan is taking longer than usual instead of polling forever", async () => {
    let requests = 0;
    const stuck = makeStoredFile(1, {
      virus_scan_result: null,
      updated_at: new Date(Date.now() - 10 * 60_000).toISOString(),
    });
    server.use(
      http.get(`${API}/api/v1/files`, () => {
        requests += 1;
        return HttpResponse.json(envelope([stuck]));
      }),
    );

    renderPage();

    expect(await screen.findByTestId("scan-taking-long")).toHaveTextContent(
      t("files.page.scan_taking_long"),
    );
    await new Promise((resolve) => setTimeout(resolve, 2_500));
    expect(requests).toBe(1);
  });

  it("does not offer to download an infected file", async () => {
    const file = makeStoredFile(1, {
      status: "infected",
      virus_scan_result: "Eicar-Test-Signature",
    });
    server.use(
      http.get(`${API}/api/v1/files`, () =>
        HttpResponse.json(envelope([file])),
      ),
    );

    renderPage();

    expect(await screen.findByText("Infected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download" })).toBeDisabled();
  });

  it("surfaces a failed download instead of swallowing it", async () => {
    const file = makeStoredFile(1);
    server.use(
      http.get(`${API}/api/v1/files`, () =>
        HttpResponse.json(envelope([file])),
      ),
      http.get(`${API}/api/v1/files/${file.id}/download`, () =>
        // Spelled out rather than via errorEnvelope(): the point of the
        // test is that the *specific* reason reaches the user, and that
        // travels in `message_key`. errorEnvelope() carries only `code`,
        // which every 409 in the product shares.
        HttpResponse.json(
          {
            data: null,
            error: {
              code: "conflict",
              message_key: "errors.file_scan_pending",
              message: "This file is still being scanned for malware.",
            },
            meta: { request_id: "req-1" },
          },
          { status: 409 },
        ),
      ),
    );
    const user = userEvent.setup();

    renderPage();
    await user.click(await screen.findByRole("button", { name: "Download" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      t("errors.file_scan_pending"),
    );
  });

  it("surfaces a failed delete instead of swallowing it", async () => {
    const file = makeStoredFile(1);
    server.use(
      http.get(`${API}/api/v1/files`, () =>
        HttpResponse.json(envelope([file])),
      ),
      http.delete(`${API}/api/v1/files/${file.id}`, () =>
        HttpResponse.json(
          errorEnvelope("You do not have permission.", "permission_denied"),
          { status: 403 },
        ),
      ),
    );
    const user = userEvent.setup();

    renderPage();
    await openDeleteFromMenu();
    await user.click(
      await screen.findByRole("button", { name: t("files.action.delete") }),
    );

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
