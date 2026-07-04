import { delay, http, HttpResponse } from "msw";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";
import type { Meta, StoryObj } from "@storybook/react-vite";

import { FileUploader } from "./FileUploader";

const MOCK_UPLOAD_URL = "https://mock-storage.example.test/bucket/mock-key";

function envelope<T>(data: T) {
  return { data, error: null, meta: { request_id: "story", version: "1.0.0" } };
}

function presignHandler() {
  return http.post("*/api/v1/files/presign", () =>
    HttpResponse.json(
      envelope({ file_id: "story-file-id", upload_url: MOCK_UPLOAD_URL }),
    ),
  );
}

function confirmHandler() {
  return http.post("*/api/v1/files/:fileId/confirm", () =>
    HttpResponse.json(
      envelope({
        id: "story-file-id",
        filename: "quarterly-report.pdf",
        size_bytes: 245_000,
        content_type: "application/pdf",
        status: "ready",
        bucket: "default",
        created_at: "2026-01-01T00:00:00Z",
      }),
    ),
  );
}

function selectFile(canvasElement: HTMLElement, file: File) {
  const input = canvasElement.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("file input not found");
  return userEvent.upload(input, file);
}

const meta = {
  title: "Components/FileUploader",
  component: FileUploader,
  args: {
    onUploaded: fn(),
  },
} satisfies Meta<typeof FileUploader>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Idle: Story = {
  parameters: { msw: { handlers: [] } },
};

export const Uploading: Story = {
  parameters: {
    msw: {
      handlers: [
        presignHandler(),
        // Never resolves within the story's lifetime -- the component stays
        // parked mid-upload so the progress bar is what gets snapshotted.
        http.put(MOCK_UPLOAD_URL, async () => {
          await delay("infinite");
          return new HttpResponse(null, { status: 200 });
        }),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const file = new File(["a".repeat(1024)], "quarterly-report.pdf", {
      type: "application/pdf",
    });
    await selectFile(canvasElement, file);
    await waitFor(() =>
      expect(within(canvasElement).getByRole("progressbar")).toBeInTheDocument(),
    );
  },
};

export const Success: Story = {
  parameters: {
    msw: { handlers: [presignHandler(), confirmHandler(), http.put(MOCK_UPLOAD_URL, () => new HttpResponse(null, { status: 200 }))] },
  },
  play: async ({ canvasElement, args }) => {
    const file = new File(["a".repeat(1024)], "quarterly-report.pdf", {
      type: "application/pdf",
    });
    await selectFile(canvasElement, file);
    await waitFor(() => expect(args.onUploaded).toHaveBeenCalled());
  },
};

export const UploadError: Story = {
  parameters: {
    msw: {
      handlers: [
        presignHandler(),
        http.put(MOCK_UPLOAD_URL, () => new HttpResponse(null, { status: 500 })),
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const file = new File(["a".repeat(1024)], "quarterly-report.pdf", {
      type: "application/pdf",
    });
    await selectFile(canvasElement, file);
    await waitFor(() =>
      expect(within(canvasElement).getByRole("button", { name: /retry/i })).toBeInTheDocument(),
    );
  },
};

export const ValidationError: Story = {
  args: { maxSize: 1024 },
  parameters: { msw: { handlers: [] } },
  play: async ({ canvasElement }) => {
    const tooLarge = new File(["a".repeat(2048)], "huge-file.pdf", {
      type: "application/pdf",
    });
    await selectFile(canvasElement, tooLarge);
    await waitFor(() =>
      expect(within(canvasElement).getByText(/exceeds/i)).toBeInTheDocument(),
    );
  },
};
