import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn(),
  setItemAsync: vi.fn(),
  deleteItemAsync: vi.fn(),
}));
vi.mock("expo-secure-store", () => secureStore);

vi.mock("expo-document-picker", () => ({ getDocumentAsync: vi.fn() }));
vi.mock("expo-file-system", () => ({
  createUploadTask: vi.fn(),
  FileSystemUploadType: { BINARY_CONTENT: 0, MULTIPART: 1 },
  getInfoAsync: vi.fn(),
}));
vi.mock("expo-image-picker", () => ({
  launchCameraAsync: vi.fn(),
  launchImageLibraryAsync: vi.fn(),
  requestCameraPermissionsAsync: vi.fn(),
  requestMediaLibraryPermissionsAsync: vi.fn(),
}));

const openURL = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
vi.mock("react-native", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-native")>();
  return { ...actual, Linking: { ...actual.Linking, openURL } };
});

const mocks = vi.hoisted(() => ({
  listMyOrgsApiV1OrgsGet: vi.fn(),
  listFilesApiV1FilesGet: vi.fn(),
  deleteFileApiV1FilesFileIdDelete: vi.fn(),
  downloadFileApiV1FilesFileIdDownloadGet: vi.fn(),
}));
vi.mock("@forge/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@forge/api-client")>();
  return {
    ...actual,
    listMyOrgsApiV1OrgsGet: mocks.listMyOrgsApiV1OrgsGet,
    listFilesApiV1FilesGet: mocks.listFilesApiV1FilesGet,
    deleteFileApiV1FilesFileIdDelete: mocks.deleteFileApiV1FilesFileIdDelete,
    downloadFileApiV1FilesFileIdDownloadGet:
      mocks.downloadFileApiV1FilesFileIdDownloadGet,
  };
});

const uploadsMocks = vi.hoisted(() => ({ pickAndUpload: vi.fn() }));
vi.mock("../../lib/uploads", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/uploads")>();
  return { ...actual, pickAndUpload: uploadsMocks.pickAndUpload };
});

const { UploadPermissionError } = await import("../../lib/uploads");
const { FilesScreen } = await import("./FilesScreen");

const ORG = { id: "org-1", name: "Acme", slug: "acme" };

const EXISTING_FILE = {
  id: "file-1",
  filename: "invoice.pdf",
  content_type: "application/pdf",
  size_bytes: 2048,
  status: "ready",
  bucket: "default",
  uploaded_by: "user-1",
  created_at: "2026-01-01T00:00:00Z",
};

const NEW_FILE = {
  id: "file-2",
  filename: "photo.jpg",
  content_type: "image/jpeg",
  size_bytes: 4096,
  status: "ready",
  bucket: "default",
  uploaded_by: "user-1",
  created_at: "2026-01-02T00:00:00Z",
};

function renderScreen() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <FilesScreen />
    </QueryClientProvider>,
  );
}

describe("FilesScreen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listMyOrgsApiV1OrgsGet.mockResolvedValue({
      data: { data: [ORG], error: null, meta: { request_id: "r1" } },
    });
    mocks.listFilesApiV1FilesGet.mockResolvedValue({
      data: {
        data: [EXISTING_FILE],
        error: null,
        meta: { request_id: "r2", next_cursor: null },
      },
    });
    mocks.deleteFileApiV1FilesFileIdDelete.mockResolvedValue({
      data: { data: null, error: null },
    });
    mocks.downloadFileApiV1FilesFileIdDownloadGet.mockResolvedValue({
      data: {
        data: {
          url: "https://cdn.example.com/invoice.pdf",
          expires_at: "2026-01-01T01:00:00Z",
        },
        error: null,
      },
    });
  });

  it("lists files and refreshes after a successful upload via the picker sheet", async () => {
    uploadsMocks.pickAndUpload.mockImplementation(() => ({
      result: Promise.resolve(NEW_FILE),
      cancel: vi.fn(),
    }));
    mocks.listFilesApiV1FilesGet
      .mockResolvedValueOnce({
        data: {
          data: [EXISTING_FILE],
          error: null,
          meta: { request_id: "r2", next_cursor: null },
        },
      })
      .mockResolvedValue({
        data: {
          data: [NEW_FILE, EXISTING_FILE],
          error: null,
          meta: { request_id: "r3", next_cursor: null },
        },
      });

    const user = userEvent.setup();
    renderScreen();

    expect(await screen.findByText("invoice.pdf")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Upload" }));
    await user.click(screen.getByRole("button", { name: "Take photo" }));

    expect(uploadsMocks.pickAndUpload).toHaveBeenCalledWith({
      source: "camera",
    });
    await waitFor(() => {
      expect(screen.getByText("photo.jpg")).toBeTruthy();
    });
  });

  it("shows a permission-denied message when the picker source is denied", async () => {
    uploadsMocks.pickAndUpload.mockImplementation(() => ({
      result: Promise.reject(new UploadPermissionError("camera")),
      cancel: vi.fn(),
    }));

    const user = userEvent.setup();
    renderScreen();

    await screen.findByText("invoice.pdf");
    await user.click(screen.getByRole("button", { name: "Upload" }));
    await user.click(screen.getByRole("button", { name: "Take photo" }));

    expect(
      await screen.findByText("Permission is required to continue."),
    ).toBeTruthy();
  });

  it("deletes a file and refreshes the list", async () => {
    const user = userEvent.setup();
    renderScreen();

    await screen.findByText("invoice.pdf");
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(mocks.deleteFileApiV1FilesFileIdDelete).toHaveBeenCalledWith(
        expect.objectContaining({ path: { file_id: "file-1" } }),
      );
    });
  });

  it("opens the presigned download URL when Download is tapped", async () => {
    const user = userEvent.setup();
    renderScreen();

    await screen.findByText("invoice.pdf");
    await user.click(screen.getByRole("button", { name: "Download" }));

    await waitFor(() => {
      expect(
        mocks.downloadFileApiV1FilesFileIdDownloadGet,
      ).toHaveBeenCalledWith(
        expect.objectContaining({ path: { file_id: "file-1" } }),
      );
      expect(openURL).toHaveBeenCalledWith(
        "https://cdn.example.com/invoice.pdf",
      );
    });
  });

  it("shows the empty state when there are no files", async () => {
    mocks.listFilesApiV1FilesGet.mockResolvedValue({
      data: {
        data: [],
        error: null,
        meta: { request_id: "r4", next_cursor: null },
      },
    });

    renderScreen();

    expect(
      await screen.findByText("No files yet. Upload one to get started."),
    ).toBeTruthy();
  });
});
