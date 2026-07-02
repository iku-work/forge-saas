import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";

import { getApiBaseUrl } from "@forge/api-client";
import type { StoredFilePublic } from "@forge/api-client";

import { envelope, makeStoredFile } from "../../test/appShellFixtures";
import { server } from "../../test/server";

// Auto-succeed XHR PUTs (S3 presigned upload) -- jsdom has no real network
// stack, and MSW v2 intercepts fetch but not raw XMLHttpRequest in jsdom.
// Replace the global with a minimal stand-in that resolves onload in a
// microtask so the upload promise can chain through to the confirm step.
class AutoSuccessXHR {
  readonly upload = { onprogress: null as ((e: ProgressEvent) => void) | null };
  status = 200;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open = vi.fn();
  setRequestHeader = vi.fn();
  abort = vi.fn().mockImplementation(() => {
    void Promise.resolve().then(() => this.onabort?.());
  });
  send = vi.fn().mockImplementation(() => {
    void Promise.resolve().then(() => {
      const e = new ProgressEvent("progress", {
        loaded: 100,
        total: 100,
        lengthComputable: true,
      });
      this.upload.onprogress?.(e);
      this.onload?.();
    });
  });
}

beforeEach(() => {
  vi.stubGlobal("XMLHttpRequest", AutoSuccessXHR);
});

const API = getApiBaseUrl();
const storedFile: StoredFilePublic = makeStoredFile(1);

const presignResponse = {
  file_id: storedFile.id,
  upload_url: "https://s3.example.test/upload",
  fields: {},
  key: "files/test",
  expires_at: "2026-12-31T00:00:00Z",
};

const { FileUploader } = await import("@forge/ui");

// The file input is `aria-hidden`/`sr-only` with no accessible name (the
// drop-zone label is on its wrapping <div>), so it can't be found via a
// role/label query -- a real runtime check here (rather than a `!` or `as`
// assertion) satisfies both no-non-null-assertion and
// non-nullable-type-assertion-style at once.
function getFileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) {
    throw new Error('input[type="file"] not found');
  }
  return input;
}

describe("FileUploader", () => {
  beforeEach(() => {
    server.use(
      http.post(`${API}/api/v1/files/presign`, () =>
        HttpResponse.json(envelope(presignResponse)),
      ),
      http.post(`${API}/api/v1/files/:id/confirm`, () =>
        HttpResponse.json(envelope(storedFile)),
      ),
    );
  });

  it("renders the drop zone with an accessible label", () => {
    render(<FileUploader onUploaded={vi.fn()} />);
    expect(
      screen.getByRole("button", { name: /drop files here/i }),
    ).toBeInTheDocument();
  });

  it("shows a type-error when an accepted MIME is not matched", async () => {
    render(<FileUploader accept="image/*" onUploaded={vi.fn()} />);
    const input = getFileInput();

    const file = new File(["hello"], "doc.pdf", { type: "application/pdf" });
    // Bypass userEvent's own accept-filter so the component's matchesAccept logic runs
    const user = userEvent.setup({ applyAccept: false });
    await user.upload(input, file);

    expect(
      await screen.findByText("File type not allowed."),
    ).toBeInTheDocument();
  });

  it("shows a size-error when the file exceeds maxSize", async () => {
    render(<FileUploader maxSize={100} onUploaded={vi.fn()} />);
    const input = getFileInput();

    const file = new File(["x".repeat(200)], "big.txt", { type: "text/plain" });
    await userEvent.upload(input, file);

    expect(await screen.findByText(/exceeds/i)).toBeInTheDocument();
  });

  it("runs the full presign → XHR → confirm flow and calls onUploaded", async () => {
    const onUploaded = vi.fn();
    render(<FileUploader onUploaded={onUploaded} />);
    const input = getFileInput();

    const file = new File(["content"], "test.txt", { type: "text/plain" });
    await userEvent.upload(input, file);

    await waitFor(() => {
      expect(onUploaded).toHaveBeenCalledWith(storedFile);
    });
  });

  it("shows an upload error when presign fails", async () => {
    server.use(
      http.post(`${API}/api/v1/files/presign`, () =>
        HttpResponse.json(
          {
            data: null,
            error: { code: "internal_error", message: "err" },
            meta: { request_id: "r1" },
          },
          { status: 500 },
        ),
      ),
    );

    render(<FileUploader onUploaded={vi.fn()} />);
    const input = getFileInput();

    const file = new File(["content"], "test.txt", { type: "text/plain" });
    await userEvent.upload(input, file);

    expect(await screen.findByText("Upload failed.")).toBeInTheDocument();
  });

  it("queues up to 3 concurrent uploads", async () => {
    const onUploaded = vi.fn();
    render(<FileUploader onUploaded={onUploaded} />);
    const input = getFileInput();

    const files = [
      new File(["a"], "a.txt", { type: "text/plain" }),
      new File(["b"], "b.txt", { type: "text/plain" }),
      new File(["c"], "c.txt", { type: "text/plain" }),
      new File(["d"], "d.txt", { type: "text/plain" }),
    ];
    await userEvent.upload(input, files);

    await waitFor(() => {
      expect(onUploaded).toHaveBeenCalledTimes(4);
    });
  });
});
