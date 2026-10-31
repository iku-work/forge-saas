import { useRef, useState } from "react";
import type { ChangeEvent, DragEvent, ReactNode } from "react";
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
  /**
   * "dropzone" (default): the large dashed target. "button": one secondary
   * button for a settings row (the profile photo), still accepting a file
   * dropped onto it. Pass `buttonLabel` with it.
   */
  variant?: "dropzone" | "button";
  buttonLabel?: ReactNode;
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
        className="size-4 shrink-0 text-success"
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

function ProgressBar({ value, label }: { value: number; label: string }) {
  return (
    <div
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
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
  variant = "dropzone",
  buttonLabel,
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

    let presignResult: {
      file_id: string;
      upload_url: string;
      fields: Record<string, string>;
    } | null = null;
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
      if (d)
        presignResult = {
          file_id: d.file_id,
          upload_url: d.upload_url,
          fields: d.fields,
        };
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
    const { file_id: fileId, upload_url: uploadUrl, fields } = presignResult;

    // An S3 POST-policy upload, not a PUT. The server signs a *policy*
    // (backends/s3.py calls generate_presigned_post), which means `upload_url`
    // is the bucket root -- there is no object key in it -- and every signed
    // value, the key included, travels as a form field. PUTting the body
    // straight at that URL is a write to the bucket itself, which S3 and
    // MinIO both answer 400.
    //
    // Two ordering rules come from the S3 POST API, and both are silent when
    // broken: the file part must be appended LAST, after every field, and the
    // request must NOT set Content-Type by hand -- the browser has to write
    // `multipart/form-data` with its own boundary. The object's own
    // Content-Type is already one of the signed fields.
    //
    // tests/load/lib/workloads.js and backend/tests/test_storage_backend.py
    // drive this same flow against a real S3-API backend and agree.
    const form = new FormData();
    for (const [name, value] of Object.entries(fields))
      form.append(name, value);
    form.append("file", file);

    try {
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        patchItem(localId, { xhr });
        xhr.open("POST", uploadUrl);
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
        xhr.send(form);
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
      {variant === "button" ? (
        // Drag-and-drop still works on the wrapper; the button is the
        // keyboard and pointer path (WCAG 2.5.7: dragging is never the only way).
        <div
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={cn(
            "inline-flex rounded-md",
            isDragActive &&
              "ring-2 ring-primary ring-offset-2 ring-offset-background",
          )}
        >
          <Button
            type="button"
            variant="outline"
            onClick={() => inputRef.current?.click()}
          >
            <Upload aria-hidden="true" />
            {buttonLabel}
          </Button>
        </div>
      ) : (
        <div
          role="button"
          tabIndex={0}
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
            <span className="text-primary underline decoration-primary/35 underline-offset-[3px]">
              {t("files.uploader.browse")}
            </span>{" "}
            {t("files.uploader.hint")}
          </p>
        </div>
      )}
      {/* A sibling, not a child, of the role="button" dropzone above --
          axe's nested-interactive rule flags any genuinely-focusable native
          control nested inside another interactive element, regardless of
          aria-hidden/tabIndex=-1 on the descendant (those don't remove it
          from the accessibility tree the way the rule cares about). */}
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
                  <ProgressBar
                    value={item.progress}
                    label={t("files.uploader.uploading")}
                  />
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
