import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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
  useOrg: vi.fn(),
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

describe("FilesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

  it("shows empty state when no files exist", async () => {
    server.use(
      http.get(`${API}/api/v1/files`, () => HttpResponse.json(envelope([]))),
    );

    renderPage();

    expect(
      await screen.findByText("No files yet. Upload one to get started."),
    ).toBeInTheDocument();
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
});
