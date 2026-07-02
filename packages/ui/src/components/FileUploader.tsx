import { useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  CheckCircle,
  Loader2,
  RotateCcw,
  Upload,
  X,
} from "lucide-react";

import {
  confirmUploadApiV1FilesFileIdConfirmPost,
  presignUploadApiV1FilesPresignPost,
} from "@forge/api-client";
import type { StoredFilePublic } from "@forge/api-client";
import { cn, formatBytes } from "@forge/utils";

import { Button } from "./button";

export interface FileUploaderProps {
  bucket?: string;
  accept?: string;
  maxSize?: number;
  onUploaded: (file: StoredFilePublic) => void;
}

type UploadStatus = "queued" | "uploading" | "done" | "error";

interface UploadItem {
  localId: string;
  file: File;
  status: UploadStatus;
  progress: number;
  error?: string;
  retryable?: boolean;
  xhr?: XMLHttpRequest;
}

const MAX_CONCURRENT = 3;

function matchesAccept(file: File, accept: string): boolean {
  return accept.split(",").some((pattern) => {
    const p = pattern.trim();
    if (p === file.type) return true;
    if (p.endsWith("/*")) return file.type.startsWith(p.slice(0, -1));
    if (p.startsWith("."))
      return file.name.toLowerCase().endsWith(p.toLowerCase());
    return false;
  });
}

function StatusIcon({ status }: { status: UploadStatus }) {
  if (status === "uploading")
    return (
      <Loader2
        className="size-4 shrink-0 animate-spin text-primary"
        aria-hidden="true"
      />
    );
  if (status === "done")
    return (
      <CheckCircle
        className="size-4 shrink-0 text-green-500"
        aria-hidden="true"
      />
    );
  if (status === "error")
    return (
      <AlertCircle
        className="size-4 shrink-0 text-destructive"
        aria-hidden="true"
      />
    );
  return (
    <Upload
      className="size-4 shrink-0 text-muted-foreground"
      aria-hidden="true"
    />
  );
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
      className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted"
    >
      <div
        className="h-full bg-primary transition-all"
        style={{ width: `${value}%` }}
      />
    </div>
  );
}

export function FileUploader({
  bucket,
  accept,
  maxSize,
  onUploaded,
}: FileUploaderProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<UploadItem[]>([]);
  const [isDragActive, setIsDragActive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const runningRef = useRef(0);
  const pendingRef = useRef<Array<{ localId: string; file: File }>>([]);
  const onUploadedRef = useRef(onUploaded);
  onUploadedRef.current = onUploaded;

  function patchItem(localId: string, patch: Partial<UploadItem>) {
    setItems((prev) =>
      prev.map((it) => (it.localId === localId ? { ...it, ...patch } : it)),
    );
  }

  async function uploadOne(localId: string, file: File): Promise<void> {
    patchItem(localId, { status: "uploading", progress: 0 });

    let presignResult: { file_id: string; upload_url: string } | null = null;
    try {
      const res = await presignUploadApiV1FilesPresignPost({
        throwOnError: true,
        body: {
          filename: file.name,
          content_type: file.type || "application/octet-stream",
          size_bytes: file.size,
          ...(bucket ? { bucket } : {}),
        },
      });
      const d = res.data?.data;
      if (d) presignResult = { file_id: d.file_id, upload_url: d.upload_url };
    } catch {
      // fall through to null check below
    }
    if (!presignResult) {
      patchItem(localId, {
        status: "error",
        error: t("files.uploader.error_upload"),
        retryable: true,
      });
      return;
    }
    const { file_id: fileId, upload_url: uploadUrl } = presignResult;

    try {
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        patchItem(localId, { xhr });
        xhr.open("PUT", uploadUrl);
        xhr.setRequestHeader(
          "Content-Type",
          file.type || "application/octet-stream",
        );
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            patchItem(localId, {
              progress: Math.round((e.loaded / e.total) * 100),
            });
          }
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) resolve();
          else reject(new Error(`HTTP ${xhr.status}`));
        };
        xhr.onerror = () => reject(new Error("network"));
        xhr.onabort = () => reject(new Error("abort"));
        xhr.send(file);
      });
    } catch (e) {
      if (e instanceof Error && e.message === "abort") {
        setItems((prev) => prev.filter((it) => it.localId !== localId));
      } else {
        patchItem(localId, {
          status: "error",
          error: t("files.uploader.error_upload"),
          retryable: true,
          xhr: undefined,
        });
      }
      return;
    }

    try {
      const res = await confirmUploadApiV1FilesFileIdConfirmPost({
        throwOnError: true,
        path: { file_id: fileId },
      });
      const stored = res.data?.data;
      if (!stored) throw new Error("empty confirm");
      patchItem(localId, { status: "done", progress: 100, xhr: undefined });
      onUploadedRef.current(stored);
    } catch {
      patchItem(localId, {
        status: "error",
        error: t("files.uploader.error_upload"),
        retryable: true,
        xhr: undefined,
      });
    }
  }

  function drain() {
    while (
      runningRef.current < MAX_CONCURRENT &&
      pendingRef.current.length > 0
    ) {
      const entry = pendingRef.current.shift()!;
      runningRef.current++;
      void uploadOne(entry.localId, entry.file).finally(() => {
        runningRef.current--;
        drain();
      });
    }
  }

  function enqueue(files: File[]) {
    const newItems: UploadItem[] = [];
    for (const file of files) {
      const localId = crypto.randomUUID();
      if (accept && !matchesAccept(file, accept)) {
        newItems.push({
          localId,
          file,
          status: "error",
          progress: 0,
          error: t("files.uploader.error_type"),
          retryable: false,
        });
        continue;
      }
      if (maxSize && file.size > maxSize) {
        newItems.push({
          localId,
          file,
          status: "error",
          progress: 0,
          error: t("files.uploader.error_size", { max: formatBytes(maxSize) }),
          retryable: false,
        });
        continue;
      }
      newItems.push({ localId, file, status: "queued", progress: 0 });
      pendingRef.current.push({ localId, file });
    }
    setItems((prev) => [...prev, ...newItems]);
    drain();
  }

  function cancel(item: UploadItem) {
    if (item.xhr) {
      item.xhr.abort();
    } else if (item.status === "queued") {
      pendingRef.current = pendingRef.current.filter(
        (p) => p.localId !== item.localId,
      );
      setItems((prev) => prev.filter((it) => it.localId !== item.localId));
    }
  }

  function retry(item: UploadItem) {
    patchItem(item.localId, {
      status: "queued",
      error: undefined,
      progress: 0,
    });
    pendingRef.current.push({ localId: item.localId, file: item.file });
    drain();
  }

  function removeItem(localId: string) {
    setItems((prev) => prev.filter((it) => it.localId !== localId));
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragActive(true);
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragActive(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setIsDragActive(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) enqueue(files);
  }

  function handleFileChange(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (files.length > 0) enqueue(files);
    e.target.value = "";
  }

  return (
    <div className="space-y-3">
      <div
        role="button"
        tabIndex={0}
        aria-label={t("files.uploader.drop_here")}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-8 text-center transition-colors",
          isDragActive
            ? "border-primary bg-primary/5"
            : "border-muted-foreground/25 hover:border-primary/50",
        )}
      >
        <Upload className="size-8 text-muted-foreground" aria-hidden="true" />
        <p className="text-sm font-medium">
          {isDragActive
            ? t("files.uploader.drag_active")
            : t("files.uploader.drop_here")}
        </p>
        <p className="text-xs text-muted-foreground">
          <span className="text-primary underline">
            {t("files.uploader.browse")}
          </span>{" "}
          {t("files.uploader.hint")}
        </p>
        <input
          ref={inputRef}
          type="file"
          className="sr-only"
          multiple
          accept={accept}
          onChange={handleFileChange}
          aria-hidden="true"
          tabIndex={-1}
        />
      </div>

      {items.length > 0 && (
        <ul className="space-y-2" aria-label="upload queue">
          {items.map((item) => (
            <li
              key={item.localId}
              className="flex items-center gap-3 rounded-md border p-3"
            >
              <StatusIcon status={item.status} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{item.file.name}</p>
                <p className="text-xs text-muted-foreground">
                  {formatBytes(item.file.size)}
                </p>
                {item.error && (
                  <p className="mt-0.5 text-xs text-destructive">
                    {item.error}
                  </p>
                )}
                {item.status === "uploading" && (
                  <ProgressBar value={item.progress} />
                )}
              </div>
              <div className="flex items-center gap-1">
                {item.status === "error" && item.retryable && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label={t("files.uploader.retry")}
                    onClick={() => retry(item)}
                  >
                    <RotateCcw className="size-3.5" aria-hidden="true" />
                  </Button>
                )}
                {(item.status === "queued" || item.status === "uploading") && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label={t("files.uploader.cancel")}
                    onClick={() => cancel(item)}
                  >
                    <X className="size-3.5" aria-hidden="true" />
                  </Button>
                )}
                {(item.status === "done" || item.status === "error") && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label={t("common.button.remove")}
                    onClick={() => removeItem(item.localId)}
                  >
                    <X className="size-3.5" aria-hidden="true" />
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
