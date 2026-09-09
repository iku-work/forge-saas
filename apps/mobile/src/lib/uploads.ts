/**
 * Mobile upload orchestration (T-115): picker (camera / library / document)
 * -> presign -> POST-policy upload -> confirm, the same three-step flow
 * packages/ui/src/components/FileUploader.tsx implements for web. Progress
 * comes from FileSystem.createUploadTask's callback -- the RN equivalent of
 * the XHR upload.onprogress event the web uploader uses, since RN has no
 * XHR upload-progress support on all platforms.
 *
 * A StoredFile row is created server-side the moment presign resolves
 * (status="pending", see backend/app/modules/storage/service.py
 * presign_upload) -- if the picked file never finishes uploading (the user
 * cancels, the app is killed mid-transfer), that row would otherwise be
 * orphaned forever with no confirm ever following it. cancel() below deletes
 * it explicitly once it exists; before that point there is nothing server-side
 * to clean up, so cancel() is a no-op.
 */
import {
  getDocumentAsync,
  type DocumentPickerResult,
} from "expo-document-picker";
import {
  createUploadTask,
  FileSystemUploadType,
  getInfoAsync,
  type UploadTask,
} from "expo-file-system";
import {
  launchCameraAsync,
  launchImageLibraryAsync,
  requestCameraPermissionsAsync,
  requestMediaLibraryPermissionsAsync,
  type ImagePickerResult,
} from "expo-image-picker";

import {
  confirmUploadApiV1FilesFileIdConfirmPost,
  deleteFileApiV1FilesFileIdDelete,
  presignUploadApiV1FilesPresignPost,
  type StoredFilePublic,
} from "@forge/api-client";

import { client } from "./api";

export type UploadSource = "camera" | "library" | "document";

export interface PickAndUploadOptions {
  source: UploadSource;
  bucket?: string;
  onProgress?: (fraction: number) => void;
}

export interface UploadHandle {
  /** Resolves to the confirmed file, or `null` if the user canceled the picker or the upload was aborted via cancel(). */
  result: Promise<StoredFilePublic | null>;
  /** Aborts an in-flight upload and deletes the pending StoredFile row it created. No-op once settled, and a no-op before the upload actually starts (the OS picker UI can't be dismissed programmatically). */
  cancel: () => Promise<void>;
}

export class UploadPermissionError extends Error {
  constructor(public readonly source: UploadSource) {
    super(`Permission was denied for the ${source} source.`);
    this.name = "UploadPermissionError";
  }
}

interface PickedAsset {
  uri: string;
  name: string;
  size: number;
  mimeType: string;
}

const DEFAULT_MIME_TYPE = "application/octet-stream";
const FALLBACK_IMAGE_MIME_TYPE = "image/jpeg";

function extensionFor(mimeType: string): string {
  const subtype = mimeType.split("/")[1] ?? "bin";
  return subtype.split("+")[0] ?? "bin"; // strips xml-suffixed types like svg+xml
}

async function resolveSize(uri: string, knownSize?: number): Promise<number> {
  if (typeof knownSize === "number") {
    return knownSize;
  }
  const info = await getInfoAsync(uri, { size: true });
  return info.exists ? info.size : 0;
}

async function pickFromCamera(): Promise<PickedAsset | null> {
  const permission = await requestCameraPermissionsAsync();
  if (!permission.granted) {
    throw new UploadPermissionError("camera");
  }
  return assetFromImagePicker(
    await launchCameraAsync({ mediaTypes: ["images"], quality: 0.8 }),
  );
}

async function pickFromLibrary(): Promise<PickedAsset | null> {
  const permission = await requestMediaLibraryPermissionsAsync();
  if (!permission.granted) {
    throw new UploadPermissionError("library");
  }
  return assetFromImagePicker(
    await launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.8 }),
  );
}

async function pickFromDocument(): Promise<PickedAsset | null> {
  return assetFromDocumentPicker(
    await getDocumentAsync({ copyToCacheDirectory: true }),
  );
}

async function assetFromImagePicker(
  result: ImagePickerResult,
): Promise<PickedAsset | null> {
  if (result.canceled) {
    return null;
  }
  const asset = result.assets[0];
  if (!asset) {
    return null;
  }
  const mimeType = asset.mimeType ?? FALLBACK_IMAGE_MIME_TYPE;
  const name =
    asset.fileName ?? `photo-${String(Date.now())}.${extensionFor(mimeType)}`;
  const size = await resolveSize(asset.uri, asset.fileSize);
  return { uri: asset.uri, name, size, mimeType };
}

async function assetFromDocumentPicker(
  result: DocumentPickerResult,
): Promise<PickedAsset | null> {
  if (result.canceled) {
    return null;
  }
  const asset = result.assets[0];
  if (!asset) {
    return null;
  }
  const mimeType = asset.mimeType ?? DEFAULT_MIME_TYPE;
  const size = await resolveSize(asset.uri, asset.size);
  return { uri: asset.uri, name: asset.name, size, mimeType };
}

async function pickAsset(source: UploadSource): Promise<PickedAsset | null> {
  if (source === "camera") {
    return pickFromCamera();
  }
  if (source === "library") {
    return pickFromLibrary();
  }
  return pickFromDocument();
}

type RunState =
  | { phase: "idle" }
  | { phase: "uploading"; fileId: string; task: UploadTask }
  | { phase: "settled" };

async function deletePendingFile(fileId: string): Promise<void> {
  try {
    await deleteFileApiV1FilesFileIdDelete({
      client,
      path: { file_id: fileId },
    });
  } catch {
    // Best effort -- the row is harmless if this fails (it's just a
    // never-confirmed "pending" file) and the caller already got the
    // canceled outcome it asked for.
  }
}

export function pickAndUpload(options: PickAndUploadOptions): UploadHandle {
  // TypeScript's flow analysis has no model of cancel() (a separate closure
  // invoked later, asynchronously, by the caller) mutating this in between
  // -- a plain `let`, or even a captured object property, gets narrowed to
  // the literal `false` it started as at every `if` below. Reading it back
  // out through a function call is opaque to that analysis, so the flag
  // stays `boolean` instead of collapsing to a literal.
  const cancelState = { canceled: false };
  const isCanceled = (): boolean => cancelState.canceled;
  let state: RunState = { phase: "idle" };

  async function run(): Promise<StoredFilePublic | null> {
    const asset = await pickAsset(options.source);
    if (!asset || isCanceled()) {
      state = { phase: "settled" };
      return null;
    }

    const { data: presignEnvelope } = await presignUploadApiV1FilesPresignPost({
      client,
      throwOnError: true,
      body: {
        filename: asset.name,
        content_type: asset.mimeType,
        size_bytes: asset.size,
        ...(options.bucket ? { bucket: options.bucket } : {}),
      },
    });
    const presigned = presignEnvelope.data;
    if (!presigned) {
      throw new Error("Presign response did not include upload details.");
    }
    const fileId = presigned.file_id;

    if (isCanceled()) {
      await deletePendingFile(fileId);
      state = { phase: "settled" };
      return null;
    }

    const task = createUploadTask(
      presigned.upload_url,
      asset.uri,
      // An S3 POST-policy upload, not a PUT. The server signs a *policy*
      // (backend/app/modules/storage/backends/s3.py calls
      // generate_presigned_post), so `upload_url` is the bucket root with no
      // object key in it and every signed value, the key included, travels as
      // a form field. PUTting the binary at that URL writes to the bucket
      // itself, which S3 and MinIO answer 400.
      //
      // No `headers` here on purpose. Both platforms set
      // `multipart/form-data` with the boundary they generated, and on
      // Android an explicit Content-Type is added to the request builder and
      // wins over the body's -- stripping the boundary and making the upload
      // unparseable. The object's own content type is already a signed field.
      //
      // S3 also requires the file part last. expo does that on both
      // platforms: ios/NetworkingHelpers.swift's createMultipartBody writes
      // every parameter before the file part, and Android's
      // FileSystemModule.createRequestBody adds the parameters in a loop
      // before addFormDataPart for the file.
      {
        httpMethod: "POST",
        uploadType: FileSystemUploadType.MULTIPART,
        fieldName: "file",
        mimeType: asset.mimeType,
        parameters: presigned.fields,
      },
      (data) => {
        if (data.totalBytesExpectedToSend > 0) {
          options.onProgress?.(
            data.totalBytesSent / data.totalBytesExpectedToSend,
          );
        }
      },
    );
    state = { phase: "uploading", fileId, task };

    // Cancellation resolves uploadAsync() with `undefined` rather than
    // rejecting -- see expo-file-system's FileSystemCancellableNetworkTask.
    const uploadResult = await task.uploadAsync();

    if (isCanceled() || !uploadResult) {
      await deletePendingFile(fileId);
      state = { phase: "settled" };
      return null;
    }

    if (uploadResult.status < 200 || uploadResult.status >= 300) {
      state = { phase: "settled" };
      throw new Error(
        `Upload failed with status ${String(uploadResult.status)}.`,
      );
    }

    const { data: confirmEnvelope } =
      await confirmUploadApiV1FilesFileIdConfirmPost({
        client,
        throwOnError: true,
        path: { file_id: fileId },
      });
    state = { phase: "settled" };
    const stored = confirmEnvelope.data;
    if (!stored) {
      throw new Error("Confirm response did not include the stored file.");
    }
    return stored;
  }

  const result = run();

  async function cancel(): Promise<void> {
    if (isCanceled() || state.phase === "settled") {
      return;
    }
    cancelState.canceled = true;
    if (state.phase === "uploading") {
      await state.task.cancelAsync();
    }
  }

  return { result, cancel };
}
