import { beforeEach, describe, expect, it, vi } from "vitest";

const secureStore = vi.hoisted(() => ({
  getItemAsync: vi.fn().mockResolvedValue(null),
  setItemAsync: vi.fn().mockResolvedValue(undefined),
  deleteItemAsync: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("expo-secure-store", () => secureStore);

const documentPicker = vi.hoisted(() => ({
  getDocumentAsync: vi.fn(),
}));
vi.mock("expo-document-picker", () => documentPicker);

const fileSystem = vi.hoisted(() => ({
  createUploadTask: vi.fn(),
  FileSystemUploadType: { BINARY_CONTENT: 0, MULTIPART: 1 },
  getInfoAsync: vi.fn(),
}));
vi.mock("expo-file-system", () => fileSystem);

const imagePicker = vi.hoisted(() => ({
  launchCameraAsync: vi.fn(),
  launchImageLibraryAsync: vi.fn(),
  requestCameraPermissionsAsync: vi.fn(),
  requestMediaLibraryPermissionsAsync: vi.fn(),
}));
vi.mock("expo-image-picker", () => imagePicker);

const mocks = vi.hoisted(() => ({
  presignUploadApiV1FilesPresignPost: vi.fn(),
  confirmUploadApiV1FilesFileIdConfirmPost: vi.fn(),
  deleteFileApiV1FilesFileIdDelete: vi.fn(),
}));
vi.mock("@forge/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@forge/api-client")>();
  return {
    ...actual,
    presignUploadApiV1FilesPresignPost:
      mocks.presignUploadApiV1FilesPresignPost,
    confirmUploadApiV1FilesFileIdConfirmPost:
      mocks.confirmUploadApiV1FilesFileIdConfirmPost,
    deleteFileApiV1FilesFileIdDelete: mocks.deleteFileApiV1FilesFileIdDelete,
  };
});

const { pickAndUpload, UploadPermissionError } = await import("./uploads");

const GRANTED = {
  granted: true,
  status: "granted",
  expires: "never",
  canAskAgain: true,
} as const;
const DENIED = {
  granted: false,
  status: "denied",
  expires: "never",
  canAskAgain: true,
} as const;

// A real POST policy. `upload_url` is the bucket root with no object key in
// it -- that is what generate_presigned_post returns -- and the key travels
// as a signed field. `fields: {}` was the previous fixture, and an empty
// policy cannot tell an upload that forwards the fields from one that drops
// them: the defect below shipped underneath it.
const POLICY_FIELDS = {
  key: "org/default/file-1/photo.jpg",
  policy: "eyJleHBpcmF0aW9uIjoi",
  "x-amz-signature": "deadbeef",
  "Content-Type": "image/jpeg",
  "x-amz-server-side-encryption": "AES256",
};

const PRESIGN_RESPONSE = {
  data: {
    data: {
      file_id: "file-1",
      upload_url: "https://storage.example.com/forge",
      fields: POLICY_FIELDS,
      key: "org/default/file-1/photo.jpg",
      expires_at: "2026-01-01T00:00:00Z",
    },
    error: null,
  },
};

const CONFIRMED_FILE = {
  id: "file-1",
  filename: "photo.jpg",
  content_type: "image/jpeg",
  size_bytes: 1024,
  status: "ready",
  bucket: "default",
  uploaded_by: "user-1",
  created_at: "2026-01-01T00:00:00Z",
};

function makeUploadTask(overrides: { uploadResult?: unknown } = {}) {
  const uploadAsync = vi
    .fn()
    .mockResolvedValue(
      overrides.uploadResult ?? {
        status: 200,
        headers: {},
        mimeType: null,
        body: "",
      },
    );
  const cancelAsync = vi.fn().mockResolvedValue(undefined);
  return { uploadAsync, cancelAsync };
}

describe("lib/uploads", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    imagePicker.requestCameraPermissionsAsync.mockResolvedValue(GRANTED);
    imagePicker.requestMediaLibraryPermissionsAsync.mockResolvedValue(GRANTED);
    mocks.presignUploadApiV1FilesPresignPost.mockResolvedValue(
      PRESIGN_RESPONSE,
    );
    mocks.confirmUploadApiV1FilesFileIdConfirmPost.mockResolvedValue({
      data: { data: CONFIRMED_FILE, error: null },
    });
    mocks.deleteFileApiV1FilesFileIdDelete.mockResolvedValue({
      data: { data: null, error: null },
    });
  });

  it("runs presign -> POST-policy upload -> confirm for a camera capture", async () => {
    imagePicker.launchCameraAsync.mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: "file://photo.jpg",
          fileName: "photo.jpg",
          fileSize: 1024,
          mimeType: "image/jpeg",
        },
      ],
    });
    const task = makeUploadTask();
    fileSystem.createUploadTask.mockReturnValue(task);

    const handle = pickAndUpload({ source: "camera" });
    const file = await handle.result;

    expect(mocks.presignUploadApiV1FilesPresignPost).toHaveBeenCalledWith(
      expect.objectContaining({
        body: {
          filename: "photo.jpg",
          content_type: "image/jpeg",
          size_bytes: 1024,
        },
      }),
    );
    // The exact options, not objectContaining: what this has to pin is as
    // much what is absent as what is present. An explicit Content-Type header
    // is added to the request builder on Android and wins over the multipart
    // body's, stripping the boundary -- so `headers` being gone is the
    // assertion, and objectContaining cannot make it.
    expect(fileSystem.createUploadTask).toHaveBeenCalledWith(
      "https://storage.example.com/forge",
      "file://photo.jpg",
      {
        httpMethod: "POST",
        uploadType: fileSystem.FileSystemUploadType.MULTIPART,
        fieldName: "file",
        mimeType: "image/jpeg",
        parameters: POLICY_FIELDS,
      },
      expect.any(Function),
    );
    expect(mocks.confirmUploadApiV1FilesFileIdConfirmPost).toHaveBeenCalledWith(
      expect.objectContaining({ path: { file_id: "file-1" } }),
    );
    expect(file).toEqual(CONFIRMED_FILE);
  });

  it("passes the bucket through to presign when provided", async () => {
    imagePicker.launchImageLibraryAsync.mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: "file://avatar.jpg",
          fileName: "avatar.jpg",
          fileSize: 512,
          mimeType: "image/jpeg",
        },
      ],
    });
    fileSystem.createUploadTask.mockReturnValue(makeUploadTask());

    await pickAndUpload({ source: "library", bucket: "avatars" }).result;

    expect(mocks.presignUploadApiV1FilesPresignPost).toHaveBeenCalledWith(
      expect.objectContaining({
        body: {
          filename: "avatar.jpg",
          content_type: "image/jpeg",
          size_bytes: 512,
          bucket: "avatars",
        },
      }),
    );
  });

  it("uploads a document picked from the document source", async () => {
    documentPicker.getDocumentAsync.mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: "file://report.pdf",
          name: "report.pdf",
          size: 2048,
          mimeType: "application/pdf",
        },
      ],
    });
    fileSystem.createUploadTask.mockReturnValue(makeUploadTask());

    const file = await pickAndUpload({ source: "document" }).result;

    expect(mocks.presignUploadApiV1FilesPresignPost).toHaveBeenCalledWith(
      expect.objectContaining({
        body: {
          filename: "report.pdf",
          content_type: "application/pdf",
          size_bytes: 2048,
        },
      }),
    );
    expect(file).toEqual(CONFIRMED_FILE);
  });

  it("resolves null without presigning when the picker is canceled", async () => {
    imagePicker.launchImageLibraryAsync.mockResolvedValue({
      canceled: true,
      assets: null,
    });

    const file = await pickAndUpload({ source: "library" }).result;

    expect(file).toBeNull();
    expect(mocks.presignUploadApiV1FilesPresignPost).not.toHaveBeenCalled();
  });

  it("rejects with UploadPermissionError when camera permission is denied", async () => {
    imagePicker.requestCameraPermissionsAsync.mockResolvedValue(DENIED);

    await expect(
      pickAndUpload({ source: "camera" }).result,
    ).rejects.toBeInstanceOf(UploadPermissionError);
    expect(imagePicker.launchCameraAsync).not.toHaveBeenCalled();
    expect(mocks.presignUploadApiV1FilesPresignPost).not.toHaveBeenCalled();
  });

  it("reports upload progress as a 0..1 fraction", async () => {
    documentPicker.getDocumentAsync.mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: "file://report.pdf",
          name: "report.pdf",
          size: 100,
          mimeType: "application/pdf",
        },
      ],
    });
    type ProgressCallback = (data: {
      totalBytesSent: number;
      totalBytesExpectedToSend: number;
    }) => void;
    let progressCallback: ProgressCallback | undefined;
    fileSystem.createUploadTask.mockImplementation(
      (
        _url: string,
        _uri: string,
        _options: unknown,
        callback?: ProgressCallback,
      ) => {
        progressCallback = callback;
        return makeUploadTask();
      },
    );
    const onProgress = vi.fn();

    const handle = pickAndUpload({ source: "document", onProgress });
    await vi.waitFor(() => {
      expect(progressCallback).toBeDefined();
    });
    progressCallback?.({ totalBytesSent: 50, totalBytesExpectedToSend: 100 });
    await handle.result;

    expect(onProgress).toHaveBeenCalledWith(0.5);
  });

  it("deletes the pending StoredFile and resolves null when canceled mid-upload", async () => {
    documentPicker.getDocumentAsync.mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: "file://report.pdf",
          name: "report.pdf",
          size: 100,
          mimeType: "application/pdf",
        },
      ],
    });
    let resolveUpload: ((value: unknown) => void) | undefined;
    const uploadAsync = vi.fn(
      () =>
        new Promise((resolve) => {
          resolveUpload = resolve;
        }),
    );
    const cancelAsync = vi.fn().mockImplementation(() => {
      resolveUpload?.(undefined);
    });
    fileSystem.createUploadTask.mockReturnValue({ uploadAsync, cancelAsync });

    const handle = pickAndUpload({ source: "document" });
    await vi.waitFor(() => {
      expect(uploadAsync).toHaveBeenCalled();
    });
    await handle.cancel();
    const file = await handle.result;

    expect(cancelAsync).toHaveBeenCalled();
    expect(mocks.deleteFileApiV1FilesFileIdDelete).toHaveBeenCalledWith(
      expect.objectContaining({ path: { file_id: "file-1" } }),
    );
    expect(
      mocks.confirmUploadApiV1FilesFileIdConfirmPost,
    ).not.toHaveBeenCalled();
    expect(file).toBeNull();
  });

  it("does nothing when cancel is called after the upload already settled", async () => {
    imagePicker.launchCameraAsync.mockResolvedValue({
      canceled: false,
      assets: [
        {
          uri: "file://photo.jpg",
          fileName: "photo.jpg",
          fileSize: 1024,
          mimeType: "image/jpeg",
        },
      ],
    });
    fileSystem.createUploadTask.mockReturnValue(makeUploadTask());

    const handle = pickAndUpload({ source: "camera" });
    await handle.result;
    await handle.cancel();

    expect(mocks.deleteFileApiV1FilesFileIdDelete).not.toHaveBeenCalled();
  });
});
