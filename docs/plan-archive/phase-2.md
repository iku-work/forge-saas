<!-- Archived from AGENTS.md on 2026-09-30. Task specs and their completion
notes, kept verbatim as the record of what each task asked for and what
was found doing it. The laws in AGENTS.md §0 still bind. -->

# Phase 2 — Platform features

> HTML ref: §"Build phases" → "Phase 2"
>
> **Prerequisite:** T-057 (Phase 1 end-to-end verification) is complete and green. Do not start Phase 2 until the Phase 1 acceptance flow runs without intervention.
>
> **Phase 2 acceptance criterion:** A user can (1) receive in-app + email + push notifications for member-invited and payment-failed events, (2) upload and download a file via presigned URLs with virus scanning, (3) see their plan's feature flags drive UI gating in real time, (4) experience the full billing lifecycle including dunning and trial-expiry warnings, (5) a superadmin can impersonate a user and override a plan, and (6) every `packages/ui` component renders in Storybook. Each acceptance path has integration tests.

---

## P2-A · Notifications

> HTML ref: §"Notifications" (entire section)
>
> Architectural rule: one `Notification` model, three delivery channels. The service decides channels from user preferences. Adding a new channel later (SMS, Slack) is one new delivery method on the service — no model changes.

### T-101 Notification + PushToken models + migration

> HTML ref: §"Notifications" → "Data model"
> needs: T-010, T-025

**Goal** Two new tables: `Notification` (the notification record itself) and `PushToken` (mobile device tokens for push delivery).

**Build**

- `backend/app/modules/notifications/models.py`:

  ```python
  class Notification(SQLModel, table=True):  # UUIDPKMixin + TimestampMixin + OrgScopedMixin
      user_id: UUID = Field(foreign_key="user.id", index=True)
      type: str = Field(index=True)          # "member_invited", "payment_failed", "weekly_summary"
      title: str
      body: str
      action_url: str | None = None
      channels: list[str] = Field(sa_column=Column(JSONB))  # ["email", "in_app", "push"]
      read_at: datetime | None = None
      email_sent_at: datetime | None = None
      push_sent_at: datetime | None = None
      # NOTE: org_id is on every notification — even system-wide ones get an org

  class PushToken(SQLModel, table=True):  # UUIDPKMixin + TimestampMixin + SoftDeleteMixin
      user_id: UUID = Field(foreign_key="user.id", index=True)
      token: str = Field(unique=True)        # Expo push token
      platform: Literal["ios", "android"]
      device_label: str | None = None
      last_used_at: datetime
  ```

- Composite index on `(user_id, read_at)` for the "unread count" query (extremely hot path)
- Composite index on `(org_id, user_id, created_at DESC, id DESC)` for cursor pagination
- Migration: `alembic revision --autogenerate -m "notifications_tables"`

**Tests**

- Migration round-trip
- `PushToken.token` unique constraint enforced
- Insertion of two notifications for same user → both queryable

**Done when**

- [ ] Migration applies both directions cleanly
- [ ] Indexes present (assert via `pg_indexes` query in test)
- [ ] `mypy --strict` clean

---

### T-101.5 Notification preferences on User

> HTML ref: §"Notifications" → "User preferences"
> needs: T-013, T-101

**Goal** `User.notification_preferences` JSONB already exists from T-013; this task defines the schema and migration to backfill defaults.

**Build**

- `backend/app/modules/notifications/preferences.py`:

  ```python
  DEFAULT_PREFERENCES: dict[str, dict[str, bool]] = {
      "member_invited":    {"email": True,  "in_app": True,  "push": False},
      "payment_failed":    {"email": True,  "in_app": True,  "push": True},
      "subscription_expiring": {"email": True, "in_app": True, "push": False},
      "weekly_summary":    {"email": True,  "in_app": False, "push": False},
      "file_uploaded":     {"email": False, "in_app": True,  "push": False},
  }

  def channels_for(prefs: dict, notification_type: str) -> list[str]:
      """Return enabled channels for this user + this notification type.
      Falls back to DEFAULT_PREFERENCES if the user has not customised."""
  ```

- Pydantic schema `NotificationPreferencesUpdate` for the settings page
- Idempotent backfill job `backfill_notification_preferences(ctx)` in `app/workers/jobs/`:
  - Iterate users with empty `notification_preferences`
  - Set to `DEFAULT_PREFERENCES`
  - Run via `make seed` and once on first deploy

**Tests**

- `channels_for({}, "payment_failed")` returns `["email", "in_app", "push"]` (defaults)
- User opts out of email for `payment_failed` → `channels_for(prefs, "payment_failed")` returns `["in_app", "push"]`
- Backfill idempotent: running twice produces no extra writes

**Done when**

- [ ] Backfill job tests pass
- [ ] All notification types from §"User preferences" represented

---

### T-102 NotificationService + email/in-app/push fan-out

> HTML ref: §"Notifications" → "Delivery channels"
> needs: T-035, T-101, T-101.5

**Goal** One service method enqueues a notification; channel fan-out happens inside the service driven by user preferences.

**Build**

- `backend/app/modules/notifications/repository.py`:
  - `NotificationRepository(session, org_id)` extends `BaseRepository[Notification]`
  - `list_for_user(user_id, after_cursor, limit, unread_only: bool = False) -> tuple[list[Notification], cursor]`
  - `mark_read(notification_id, user_id) -> Notification` — scoped to user_id, raises `NotFoundError` cross-user
  - `mark_all_read(user_id) -> int` — returns count updated
  - `unread_count(user_id) -> int` — cached in Redis with key `notif:unread:{user_id}`, 30 s TTL, invalidated on read/create
  - `PushTokenRepository(session)` (not org-scoped — user-scoped):
    - `register(user_id, token, platform, device_label) -> PushToken` (upsert by token; updates `last_used_at`)
    - `list_active_for_user(user_id) -> list[PushToken]`
    - `deactivate(token) -> None` — soft-delete (Expo returns "DeviceNotRegistered" for revoked tokens)

- `backend/app/modules/notifications/service.py`:
  - `NotificationService.send(self, *, user_id, org_id, type, title, body, action_url=None) -> Notification`:
    1. Load user preferences via `channels_for(user.notification_preferences, type)`
    2. Create `Notification` row with the resolved channels
    3. If `"email"` in channels → enqueue `send_email_job` with template `notification_<type>`
    4. If `"push"` in channels → enqueue `send_push_job(user_id, notification_id)`
    5. If `"in_app"` in channels → publish to Redis pub/sub channel `notif:user:{user_id}` (T-103 SSE consumer listens here)
    6. Return the `Notification` row
  - **Idempotency:** accepts optional `idempotency_key: str | None` — if provided, attempts to find an existing `Notification` with the same key in the past 24 h; if found, no-op. Prevents duplicate notifications from webhook retries.
  - All channel dispatch is fire-and-forget — service success does not depend on delivery success
  - Audit log entry on every send (`action="notification.sent"`, includes channels and type)

**Tests**

- User with email-only preference → only `send_email_job` enqueued, no push job
- User with push enabled but no `PushToken` → push job runs but no-ops gracefully
- Cross-tenant: `mark_read` for a notification owned by org B from a session in org A → `NotFoundError`
- Idempotency: two calls with same `idempotency_key` within 24h → one `Notification` row created
- `unread_count` cache hit on second call (assert no DB query via query-count fixture)
- 100% coverage on `NotificationService`

**Done when**

- [ ] All fan-out tests pass
- [ ] Idempotency test passes
- [ ] 100% coverage on `service.py`

---

### T-103 SSE in-app notification stream

> HTML ref: §"Notifications" → "In-app (SSE)"
> needs: T-102

**Goal** A single SSE endpoint streams notifications to the user's open browser tabs in real time.

**Build**

- `backend/app/modules/notifications/sse.py`:
  - `GET /api/v1/notifications/stream` (auth required via JWT):
    - Subscribes to Redis pub/sub channel `notif:user:{user_id}`
    - Yields SSE events: `event: notification\ndata: <json>\n\n`
    - Sends a `: ping` comment every 30 s to keep the connection alive (browsers + load balancers drop idle connections)
    - On client disconnect, unsubscribes cleanly (use `asyncio.CancelledError` handler)
    - Per-user concurrency cap of 5 connections (a Redis counter; reject 6th with 429)
    - Total connection cap from settings `SSE_MAX_CONNECTIONS=10000` to bound memory
  - `GET /api/v1/notifications` — cursor-paginated list (uses repo's `list_for_user`)
  - `PATCH /api/v1/notifications/{id}/read` — mark single read
  - `POST /api/v1/notifications/read-all` — mark all read
  - `GET /api/v1/notifications/unread-count` — returns `{ count: N }` (cached)
  - `PATCH /api/v1/me/notification-preferences` — update preferences

**Tests**

- SSE: publish to Redis channel → connected client receives the event within 1 s
- Reconnect: after disconnect, no orphan subscription remains (assert Redis pubsub count)
- 6th connection from same user → 429
- Mark-read invalidates the unread-count cache
- Rate-limited: 100/min on PATCH endpoints

**Done when**

- [ ] SSE test green using an in-process Redis publisher
- [ ] Connection cap test passes
- [ ] All endpoints in standard envelope format

---

### T-104 Web notification center

> HTML ref: §"React web app" → "Notification center + SSE feed"
> needs: T-103, T-044

**Goal** Bell icon in the topbar with unread count, dropdown feed, mark-read, mark-all-read, and the SSE subscription.

**Build**

- `apps/web/src/features/notifications/`:
  - `NotificationBell.tsx`: lucide-react `Bell` icon + numeric badge; opens the dropdown
  - `NotificationFeed.tsx`: virtualized list of last 50 notifications (TanStack Virtual); infinite scroll for older via cursor; each row: title, body, relative time (`formatDistanceToNow`), action button if `action_url` set
  - `EmptyState`, `LoadingState`, `ErrorState` components (per Phase 1 four-states rule)
- `apps/web/src/lib/sse.ts`: typed EventSource wrapper
  - Auto-reconnect with exponential backoff (1s, 2s, 4s, 8s, max 30s)
  - On reconnect, invalidate the notifications query so any events missed during disconnect are fetched
  - Pause when document hidden (Page Visibility API) — saves a connection for inactive tabs
- `@forge/hooks/useNotifications.ts`:
  - Returns `{ notifications, unreadCount, markRead, markAllRead, isConnected }`
  - Uses TanStack Query for the initial fetch and for cursor pagination
  - SSE events trigger `queryClient.setQueryData` to insert new notifications optimistically + invalidate unread count
- Settings page tab "Notifications": editable preferences matrix (per type × per channel checkboxes) using `PATCH /api/v1/me/notification-preferences`
- Toast on new notification (sonner) — only when document is visible

**Tests** (Vitest + RTL + MSW)

- Bell renders unread count
- New notification via mocked SSE event appears at top
- Mark-read clears badge; mark-all-read clears the list visually
- A11y: `axe-core` clean on the dropdown
- Preferences page: toggle email off → API called with correct payload

**Done when**

- [ ] All tests pass
- [ ] Bell + dropdown work end-to-end against the local backend
- [ ] No JSX in `@forge/hooks` (lint check)

---

### T-105 Mobile push token registration + handler

> HTML ref: §"React Native" → "Push notifications"
> needs: T-102, T-047

**Goal** Mobile app registers an Expo push token on first authenticated launch and handles received pushes.

**Build**

- `apps/mobile/src/lib/push.ts`:
  - `async function registerPushToken(): Promise<void>`:
    - Request permission via `expo-notifications`
    - If denied, exit (no error — user choice)
    - Get token via `Notifications.getExpoPushTokenAsync({ projectId })`
    - POST to `/api/v1/notifications/push-tokens` with `{ token, platform, device_label: Device.modelName }`
    - Store last-registered token in `expo-secure-store` to skip re-registration if unchanged
  - `setupNotificationHandlers()`:
    - Foreground: show in-app banner via custom component (Expo's default handler is suppressed)
    - Tap on background notification: read `data.action_url` → navigate via Expo Router
    - Token refresh listener: re-register on token change
- Call `registerPushToken()` in the `(app)/_layout.tsx` mount effect (only when user is authenticated)
- `apps/mobile/src/features/notifications/`: in-app notification list screen mirroring the web feed

**Tests**

- Mock `expo-notifications`: token retrieval → POST called with correct payload
- Permission denied → no POST, no error
- Tap navigation: notification with `action_url: "/_app/settings/billing"` → router pushes the route
- Token refresh: simulated event → second POST with new token

**Done when**

- [ ] All push tests pass
- [ ] Manual smoke: physical iOS + Android device receives a test push from the backend

---

### T-106 Email templates package (React Email)

> HTML ref: §"Notifications" → "Email (Resend)"
> needs: T-035

**Goal** Replace the Jinja2 templates from T-035 with type-safe React Email components, one per notification type. Better preview tooling, shared design tokens with the marketing site (Phase 4).

**Build**

- New workspace package `packages/email-templates`:
  - Deps: `@react-email/components`, `@react-email/render`
  - One component per template, all in `src/templates/`:
    - `VerifyEmail.tsx`, `PasswordReset.tsx`, `MagicLink.tsx`, `Invitation.tsx`, `Welcome.tsx` (existing — migrated from Jinja)
    - `MemberInvited.tsx`, `PaymentFailed.tsx`, `SubscriptionExpiring.tsx`, `TrialEnding.tsx`, `FileUploaded.tsx` (new for Phase 2)
  - Shared `BaseLayout.tsx`: header, footer, brand colors from `packages/ui/tokens`
  - Render utility: `renderEmail(template: string, props: object) -> { html: string, text: string }`
- Backend integration:
  - `app/workers/jobs/email.py` updated: instead of Jinja, call a small Node subprocess (via `nodejs` package in the worker image) that runs `pnpm --filter @forge/email-templates render <name> <json>` and returns rendered HTML
  - Cache rendered output per `(template, props_hash)` for 1 h to avoid re-rendering identical emails
- Storybook entries (under T-150) for every template
- Preview server: `pnpm --filter @forge/email-templates dev` serves rendered previews at `localhost:3030` with hot reload

**Tests**

- Each template renders without error for example props
- HTML output passes a basic email-client compatibility check (no flex/grid, table-based layout, inline styles)
- Plain-text fallback present and non-empty

**Done when**

- [ ] All 10 templates render
- [ ] Email worker switched from Jinja to React Email
- [ ] Old Jinja templates deleted (no dead code)
- [ ] Preview server documented in module README

---

## P2-B · File storage

> HTML ref: §"File storage" (entire section)
>
> Architectural rule: **files never proxy through the API**. Clients upload directly to object storage via presigned URLs. The API only manages metadata.

### T-110 StoredFile model + migration

> HTML ref: §"File storage" → "StoredFile model"
> needs: T-010, T-025

**Goal** Single table tracking all file metadata, with virus-scan status and soft delete.

**Build**

- `backend/app/modules/storage/models.py`:
  ```python
  class StoredFile(SQLModel, table=True):  # UUIDPKMixin + TimestampMixin + SoftDeleteMixin + OrgScopedMixin
      uploaded_by: UUID = Field(foreign_key="user.id", index=True)
      key: str = Field(unique=True, index=True)   # storage object key (org_id-prefixed)
      filename: str
      content_type: str = Field(index=True)
      size_bytes: int
      checksum_sha256: str | None = None          # set after confirm
      status: Literal["pending", "ready", "infected", "failed", "deleted"] = "pending"
      virus_scan_result: str | None = None
      # categorisation for plan-quota counting
      bucket: str = "default"                     # logical bucket: "default", "avatars", "exports", ...
  ```
- Composite index `(org_id, status, created_at DESC, id DESC)` for cursor lists
- Composite index `(org_id, uploaded_by)` for "files uploaded by me"
- Migration: `alembic revision --autogenerate -m "stored_files"`

**Tests**

- Migration round-trip
- `key` unique enforced across orgs (key includes org_id prefix so collisions impossible)

**Done when**

- [ ] Migration clean both directions
- [ ] Indexes verified

---

### T-111 StorageBackend protocol + S3 / R2 / MinIO implementations

> HTML ref: §"File storage" → "Storage backend interface"
> needs: T-003

**Goal** A single `StorageBackend` interface with three implementations selected by `settings.STORAGE_BACKEND`.

**Build**

- `backend/app/modules/storage/backend.py`:

  ```python
  @dataclass(frozen=True)
  class PresignedUpload:
      url: str
      fields: dict[str, str]       # POST form fields for the upload
      key: str
      expires_at: datetime

  class StorageBackend(Protocol):
      async def presign_upload(
          self, key: str, content_type: str, max_bytes: int, expires_in: int = 600
      ) -> PresignedUpload: ...
      async def presign_download(
          self, key: str, expires_in: int = 3600, *, filename: str | None = None
      ) -> str: ...
      async def head(self, key: str) -> dict | None: ...   # None if not found
      async def delete(self, key: str) -> None: ...
      async def exists(self, key: str) -> bool: ...
  ```

- Implementations in `backend/app/modules/storage/backends/`:
  - `s3.py` — `S3Backend(bucket, region)` using `aioboto3`
  - `r2.py` — extends `S3Backend` with R2's endpoint and signature config (R2 is S3-compatible but needs `signature_version="s3v4"` and `endpoint_url` from settings)
  - `minio.py` — same as S3 but with `endpoint_url=settings.AWS_ENDPOINT_URL` and `addressing_style="path"`
- Presigned upload **enforces server-side constraints**:
  - `Content-Length` range condition: `["content-length-range", 0, max_bytes]`
  - `Content-Type` equality
  - Server-side encryption header required (`x-amz-server-side-encryption: AES256`) on S3/R2
- Factory `get_storage_backend() -> StorageBackend` selected by `settings.STORAGE_BACKEND`, cached at module level
- Object keys are namespaced: `<org_id>/<bucket>/<uuid>/<filename>` — makes per-tenant access policies straightforward in S3/R2

**Tests** (using MinIO via testcontainers)

- Round-trip presign upload → PUT bytes → `head` returns metadata
- `delete` removes the object; subsequent `exists` returns False
- Presigned URL expires (use `freezegun` to advance time)
- Oversized PUT (over `max_bytes`) → S3 rejects with 400
- Wrong content-type → S3 rejects

**Presigned URL security requirements** — these are non-negotiable and must each have a dedicated test asserting the wire format, not just the happy path:

- [ ] **The `content-length-range` policy condition is signed into the URL**, not enforced only by application logic. Tampering with the form field after the URL is generated must cause S3 to reject the upload. Test: generate URL with `max_bytes=1024`; attempt PUT of 2048 bytes; assert S3 returns 400.
- [ ] **The `Content-Type` condition is signed as an exact match**, never a wildcard or prefix. `image/jpeg` is acceptable; `image/*` is forbidden. Test: generate URL for `image/jpeg`; attempt PUT with `Content-Type: application/octet-stream`; assert S3 returns 400.
- [ ] **The `x-amz-server-side-encryption: AES256` header is signed**, not optional. An upload attempt without this header must be rejected by S3. Test: generate URL; strip the encryption header from the PUT; assert S3 returns 400.
- [ ] **The exact object key is bound to the signature** — the client cannot upload to any key other than the one your service generated. Test: generate URL for key `org-a/files/uuid-1/x.jpg`; attempt PUT to `org-b/files/uuid-2/y.jpg`; assert S3 returns 403.
- [ ] **The expiry is short and configurable**, defaulting to 600 seconds (10 min) and never exceeding 3600 (1 h) for uploads. Test: `freezegun` to 11 min in the future; attempt PUT; assert S3 returns 403.

**Done when**

- [ ] Backend tests green against real MinIO
- [ ] All three implementations share the same test suite via fixture parametrisation
- [ ] All five presigned URL security tests above pass on every backend implementation
- [ ] 100% coverage on `backend.py` and each implementation

---

### T-112 Presign + confirm endpoints (plan-gated)

> HTML ref: §"File storage" → "Upload flow"
> needs: T-110, T-111, T-031

**Goal** Two endpoints that orchestrate the three-step upload flow; storage quota enforced from plan features.

**Build**

- `backend/app/modules/storage/repository.py`:
  - `StoredFileRepository(session, org_id)` extends `BaseRepository`
  - `total_bytes_for_org(self) -> int` — sum of `size_bytes` where `status="ready"` (cached 60s in Redis with explicit invalidation on file create/delete)
  - `count_files_for_org(self) -> int`
- `backend/app/modules/storage/service.py`:
  - `StorageService.presign_upload(self, user, *, filename, content_type, size_bytes, bucket="default") -> dict`:
    1. Validate content_type against allowlist (default: images, PDFs, common docs; configurable per bucket)
    2. Validate `size_bytes <= plan.features.max_upload_mb * 1_000_000` (per-file cap)
    3. Validate `total_bytes_for_org + size_bytes <= plan.features.storage_gb * 1_000_000_000` (org quota)
    4. Generate `key = f"{org_id}/{bucket}/{uuid4()}/{slugify(filename)}"`
    5. Create `StoredFile` row with `status="pending"`
    6. Call `backend.presign_upload(key, content_type, max_bytes=size_bytes + 1024)`
    7. Audit log: `file.presigned`
    8. Return `{ file_id, upload_url, fields, key, expires_at }`
  - `StorageService.confirm_upload(self, user, file_id) -> StoredFile`:
    1. Load `StoredFile` (org-scoped — cross-tenant impossible)
    2. Reject if `status != "pending"` (idempotent: re-confirming a `ready` file returns the file unchanged)
    3. `backend.head(key)` — verify file exists, get `Content-Length` and ETag
    4. Reject if actual size > claimed `size_bytes` by more than 1 KB tolerance
    5. Set `status="ready"`, `checksum_sha256` (from ETag if S3 single-part)
    6. Enqueue `virus_scan_job(file_id)`
    7. Audit log: `file.uploaded`; emit `Notification(type="file_uploaded")` if user prefers
- `backend/app/modules/storage/router.py`:
  - `POST /api/v1/files/presign` — request body `{ filename, content_type, size_bytes, bucket? }` → presigned upload
  - `POST /api/v1/files/{id}/confirm` — confirm after upload completes
  - `GET /api/v1/files` — cursor list (org-scoped, optionally filtered by bucket)
  - `GET /api/v1/files/{id}/download` — returns `{ url, expires_at }` via `presign_download`; requires `FILES_READ`
  - `DELETE /api/v1/files/{id}` — soft-delete row, enqueue hard delete from storage (with 7d delay for accidental-delete recovery); requires `FILES_MANAGE`
- All endpoints use `require_feature("storage_gb")` to deny on free plans without storage

**Tests**

- Cross-tenant: org B cannot confirm or download a file owned by org A → 404
- Quota: presign request that would exceed `plan.features.storage_gb` → 402 `PlanLimitError`
- Per-file size cap: oversized request → 402
- Confirm idempotent: confirming a `ready` file twice → 200 both times
- Confirm rejects if actual size != claimed size
- Content-type allowlist enforced (forbid `application/x-executable`)
- Permission: MEMBER can read files, only FILES_MANAGE can delete
- 100% coverage on `service.py`

**Orchestration-layer security requirements** — these belong to the service that calls the storage backend, not the backend itself. Each must have a dedicated test:

- [ ] **Object keys are generated server-side with a UUID component**, never accepted from the client. A presign request with a client-supplied `key` parameter must ignore it or reject the request. Test: POST presign with `{"key": "evil-path/file.jpg"}`; assert the returned key matches `{org_id}/{bucket}/{uuid4}/{filename}` pattern, not the client's input.
- [ ] **Filename is treated as a display-only string with no authorisation weight.** It is `slugify`'d for the storage key but the UUID component is what makes the key unique and safe. Test: two users in the same org upload `report.pdf`; both succeed with different keys (different UUIDs).
- [ ] **Confirm step verifies file existence in storage** via `backend.head(key)`, not by trusting the client to report success. Test: skip the actual PUT, then call confirm; assert it returns an error and the row stays `status="pending"`.
- [ ] **Confirm step verifies actual file size** matches the claimed `size_bytes` within a small tolerance (1 KB for header overhead). Test: claim 1024 bytes; upload 2048 bytes; assert confirm returns 422 and the storage object is deleted.
- [ ] **Cross-tenant presigned URL reuse is impossible** — a presigned URL generated for org A's prefix cannot be used to upload into org B's prefix even by a member of org B with their own valid session. Test: extract URL from org A's presign response; authenticate as a user in org B; attempt PUT; assert S3 rejects via signature mismatch on key.

**Done when**

- [ ] All tests green
- [ ] Cross-tenant isolation sweep (T-049) extended to include file endpoints
- [ ] All five orchestration-layer security tests above pass
- [ ] Audit log entries verified for presign, upload, delete

---

### T-113 Virus scan job

> HTML ref: §"File storage" → "Upload flow" (virus_scan_job step)
> needs: T-112, T-034

**Goal** Async virus scan that updates `StoredFile.status` to `"infected"` or `"ready"`.

**Build**

- `backend/app/workers/jobs/virus_scan.py`:
  - `virus_scan(ctx, *, file_id: UUID) -> None`:
    1. Load `StoredFile`; abort if `status != "ready"` (already scanned or deleted)
    2. Generate a presigned **download** URL valid for the scan window
    3. Call the configured scanner (see below)
    4. On clean: update `virus_scan_result="clean"`, leave `status="ready"`
    5. On infected: set `status="infected"`, `virus_scan_result=<signature>`; **delete the underlying object immediately** from storage; emit `Notification(type="file_infected")` to uploader + AuditEvent
    6. On scanner error: log + retry up to 3× with exponential backoff; final failure sets `status="failed"`, audit-logged
- Pluggable scanner interface (Protocol) with two implementations:
  - `ClamAVScanner`: connects to `clamd` over Unix socket / TCP; for self-hosted
  - `VirusTotalScanner`: stub using VT API for SaaS workflows (rate-limited, file-hash first)
  - Selection via `settings.VIRUS_SCANNER: Literal["clamav", "virustotal", "noop"] = "noop"`
  - `"noop"` is the default for dev — marks every file `"clean"` immediately
- Add `clamav` service to `docker-compose.prod.yml` (optional, behind a profile)

**Tests**

- Mock scanner returns "clean" → file stays `status="ready"`, `virus_scan_result="clean"`
- Mock scanner returns "infected" → file `status="infected"`, object deleted from storage, notification sent
- Scanner exception → retried 3× then `status="failed"`
- 100% coverage on `virus_scan.py`

**Done when**

- [ ] All scanner outcomes tested
- [ ] Infected files are removed from storage (not just flagged)

---

### T-114 Web file upload component

> HTML ref: §"File storage" (Web feature implied)
> needs: T-112, T-041

**Goal** Reusable dropzone component in `@forge/ui` that handles the full three-step flow.

**Build**

- `packages/ui/src/components/FileUploader.tsx`:
  - Props: `bucket?, accept?, maxSize?, onUploaded(file: StoredFileDTO) => void`
  - Drag-and-drop zone + click-to-browse
  - For each file: call `/files/presign` → PUT to `upload_url` with progress → call `/files/{id}/confirm`
  - Per-file progress bar (XHR `upload.onprogress`)
  - Cancellable: pending uploads can be aborted; in-flight calls cancellation token
  - Concurrency: max 3 simultaneous uploads (FIFO queue for the rest)
  - Error states: validation rejection (size/type), upload failure (network), confirm failure (size mismatch)
  - A11y: keyboard-accessible (Tab focus, Enter to open picker, screen-reader announcements of progress)
- `apps/web/src/features/files/`:
  - `FilesPage` — list of org's files (uses cursor pagination from `useCursorPagination` shared hook)
  - File row: filename, size (formatBytes), uploader avatar, status badge, download / delete actions
  - Empty / loading / error states
- Avatar upload in Settings → Profile uses the same uploader with `bucket="avatars"` and `accept="image/*"`

**Tests** (Vitest + RTL + MSW)

- Drop file → presign called with correct content-type and size
- Successful upload triggers `onUploaded` callback
- Validation error (file too large) → reject before presign called
- Progress events update the bar
- A11y: axe-core clean on file picker open

**Done when**

- [ ] FileUploader storybook entry exists (T-150)
- [ ] Avatar upload works end-to-end
- [ ] Files page renders, downloads, deletes

---

### T-115 Mobile file picker

> needs: T-112, T-047

**Goal** Mobile app can upload from camera roll, document picker, or in-app camera.

**Build**

- `apps/mobile/src/lib/uploads.ts`:
  - `pickAndUpload(options: { source: "camera" | "library" | "document", bucket?: string })` — orchestrates picker + presign + PUT + confirm
  - Permissions handled via `expo-image-picker` / `expo-document-picker`
  - Progress via `FileSystem.createUploadTask` (Expo's task supports progress callbacks)
- `apps/mobile/src/features/files/`:
  - List screen mirroring web
  - Upload button opens a sheet with three options: camera, library, document
- Avatar upload in Profile settings: source = library, bucket = "avatars"

**Tests**

- Mocked picker returning a fake URI → presign + PUT + confirm sequence runs
- Cancellation: aborting partway leaves no orphaned `StoredFile` row (call `DELETE /files/{id}` on abort)

**Done when**

- [ ] All three pick sources work on iOS + Android simulators

---

## P2-C · Feature flags

> HTML ref: §"Best practices" → "DB-backed flags + plan features dict"
>
> Architectural rule: two-layer flags. Plan features come from `Plan.features` (set by us, gated by billing). Per-org overrides come from `OrgFeatureFlag` (set by us via admin, or by the org's owner if exposed in settings).

### T-120 FeatureFlagService with Redis cache

> HTML ref: §"Multi-tenancy" → "Feature flags" | §"Billing" → "Plan-gating dependency"
> needs: T-025, T-031

**Goal** A single source of truth for "is feature X enabled for org Y" that consolidates plan features and per-org overrides.

**Build**

- `backend/app/modules/feature_flags/repository.py`:
  - `OrgFeatureFlagRepository(session, org_id)` extends `BaseRepository`
  - `get_overrides(self) -> dict[str, bool]`
  - `set_override(self, flag_name: str, enabled: bool) -> OrgFeatureFlag` (upsert)
  - `delete_override(self, flag_name: str) -> None`
- `backend/app/modules/feature_flags/service.py`:
  - `FeatureFlagService.is_enabled(self, org, flag: str) -> bool`:
    1. Cache lookup: `Redis GET feature_flags:{org_id}:{flag}` (60 s TTL)
    2. On miss: load org's plan + overrides; result = `bool(plan.features.get(flag)) AND override` (override defaults True if absent, can be explicitly disabled)
    3. Cache result; return
  - `FeatureFlagService.all_enabled(self, org) -> dict[str, bool]`:
    1. Cache key `feature_flags:{org_id}:all` (60 s TTL)
    2. On miss: compute the resolved flag map (plan features ∩ overrides) and cache
  - `FeatureFlagService.invalidate(self, org_id) -> None`:
    - Deletes both per-flag keys and the `:all` key
    - Called from: subscription updates, override changes, plan changes
- Replace the inline `require_feature` from T-036 to delegate to this service (better caching, central truth)
- `backend/app/modules/feature_flags/router.py`:
  - `GET /api/v1/orgs/{org_id}/feature-flags` — returns `{ flags: { name: enabled } }`, requires membership

**Tests**

- Flag enabled in plan + no override → True
- Flag enabled in plan + override=False → False (override wins)
- Flag absent from plan → False (override cannot enable it)
- Cache hit on second call (assert no DB query)
- Invalidation on subscription update test: change plan → cached `all_enabled` invalidated
- 100% coverage on service

**Done when**

- [ ] All resolution paths tested
- [ ] `require_feature` migrated to use the service
- [ ] Cache invalidation hooks added to billing webhook handlers (T-030) and override endpoints

---

### T-121 useFeatureFlag hook

> HTML ref: §"React web app" → "Key hooks"
> needs: T-120, T-038

**Goal** Frontend hook reads the resolved flag map once per session via TanStack Query; UI gates with one-line checks.

**Build**

- `packages/hooks/src/useFeatureFlag.ts`:

  ```typescript
  export function useFeatureFlags() {
    const { org } = useOrg();
    return useQuery({
      queryKey: ["feature-flags", org.id],
      queryFn: () => apiClient.featureFlags.list({ orgId: org.id }),
      staleTime: 60_000,
    });
  }

  export function useFeatureFlag(flag: string): boolean {
    const { data } = useFeatureFlags();
    return Boolean(data?.flags?.[flag]);
  }
  ```

- No JSX in `packages/hooks` (enforced by lint)
- On org switch (via `useOrg`), the query key changes so the cache is automatically separated per-org

**Tests**

- Mock API returns `{ flags: { advanced_reports: true } }` → `useFeatureFlag("advanced_reports")` returns true
- Org switch causes refetch
- Default (loading) returns false (deny by default)

**Done when**

- [ ] Tests pass
- [ ] Web app uses `useFeatureFlag` to hide a real navigation item (e.g. "Advanced reports") when disabled

---

### T-122 Admin endpoint to toggle flags

> needs: T-120, T-140

**Goal** Superadmin can override flags per org (without modifying the user's plan) for support and beta-testing.

**Build**

- Endpoints (under T-140's admin auth guard):
  - `GET /api/v1/admin/orgs/{org_id}/feature-flags` — current resolved map + per-flag override state
  - `PUT /api/v1/admin/orgs/{org_id}/feature-flags/{flag}` — body `{ enabled: bool }` → upserts override
  - `DELETE /api/v1/admin/orgs/{org_id}/feature-flags/{flag}` — removes the override (revert to plan default)
- Every mutation writes an `AuditEvent` (`action="admin.feature_flag.set"`, includes flag name and value)
- Mutation invalidates the flag cache for that org

**Tests**

- Non-superadmin → 403
- Set override → `useFeatureFlag` reflects within `staleTime`
- Delete override → resolved map reverts to plan default
- Audit event written

**Done when**

- [ ] Permission and audit tests pass
- [ ] Admin UI in T-143 wires to these endpoints

---

## P2-D · Billing completeness

> HTML ref: §"Billing" → "What's included" (dunning, trial, usage metering, invoice history)

### T-130 Dunning email sequence

> HTML ref: §"Billing" → "Dunning"
> needs: T-030, T-035

**Goal** On `invoice.payment_failed`, schedule a sequence of reminder emails at 1 day, 3 days, and 7 days. Stop the sequence when payment succeeds or subscription is canceled.

**Build**

- `backend/app/modules/billing/dunning.py`:
  - `DunningService.start(subscription_id, invoice_id) -> None`:
    1. Create `DunningCampaign` row: `{ subscription_id, invoice_id, started_at, status: "active" }`
    2. Enqueue three ARQ jobs with delays of 1d, 3d, 7d (`arq.queue.enqueue_job(..., _defer_by=timedelta(days=N))`)
    3. Each job stores its arq job_id on the campaign so cancel can revoke them
  - `DunningService.cancel(subscription_id) -> None`:
    - Mark campaign `status="canceled"`, revoke all queued ARQ jobs by id
  - `DunningService.complete(subscription_id) -> None`:
    - Same as cancel but `status="completed"`
- `DunningCampaign` model (UUIDPKMixin + TimestampMixin + OrgScopedMixin via subscription):
  - `subscription_id, invoice_id, status, scheduled_job_ids: list[str] (JSONB), started_at, ended_at`
  - Migration in same alembic revision as a stripe_event_received_at column addition
- Webhook handler (T-030) updated:
  - `invoice.payment_failed` → `DunningService.start(...)`
  - `invoice.payment_succeeded` → `DunningService.complete(...)`
  - `customer.subscription.deleted` → `DunningService.cancel(...)`
- Email job per step:
  - Day 1: "Your payment failed — please update your card" (gentle, link to billing portal)
  - Day 3: "Reminder — your subscription will be suspended in 4 days"
  - Day 7: "Subscription suspended" — set sub status to `canceled` if still `past_due`
- Idempotent at every step: re-running same step does not re-send (check audit log for the previous send)

**Tests**

- `payment_failed` webhook → campaign created, 3 jobs scheduled
- `payment_succeeded` cancels remaining jobs
- Day-7 step on still-`past_due` sub transitions to `canceled`
- Re-trigger payment_failed while a campaign is active → previous campaign canceled, new one started
- 100% coverage on `dunning.py`

**Done when**

- [ ] All dunning paths tested
- [ ] Audit events for every email sent
- [ ] Cancel revokes queued ARQ jobs (verify via Redis inspection)

---

### T-131 Trial expiry warning emails

> needs: T-034, T-042, T-035

**Goal** Daily scheduled job sends "your trial ends in 3 days" and "1 day" emails to org owners.

**Build**

- `backend/app/workers/scheduler.py` (extend the APScheduler instance from T-042):
  - Job: `notify_expiring_trials` — daily at 09:00 UTC
- `backend/app/workers/jobs/billing.py`:
  - `notify_expiring_trials(ctx) -> None`:
    1. Query subscriptions where `status="trialing"` AND `trial_end` is between 3 days and 3 days + 1 hour from now → send "3-day warning"
    2. Query where `trial_end` is between 1 day and 1 day + 1 hour → send "1-day warning"
    3. For each: enqueue `send_email_job` with `TrialEnding.tsx` template addressed to org OWNER memberships
    4. Idempotent: stores a sent marker `audit_event` of action `trial.warning_sent_3d` / `_1d` per subscription; skips if already sent
- Sub-job: `expire_trials_to_inactive` — daily at 10:00 UTC:
  - For subs where `trial_end < now` AND `status="trialing"` AND no payment method: transition to `canceled`, send "trial expired" email

**Tests** (use `freezegun`)

- Sub with `trial_end = now + 3d2h` → no email
- Sub with `trial_end = now + 3d0.5h` → 3d warning sent
- Re-running scheduler same day → no duplicate (audit marker check)
- Expired-trial transition test

**Done when**

- [ ] Time-window tests green
- [ ] Idempotency test green
- [ ] Manual end-to-end smoke confirms email delivered to MailHog

---

### T-132 UsageRecord model + report_usage job skeleton

> HTML ref: §"Billing" → "Usage metering"
> needs: T-029, T-034

**Goal** A skeleton for usage-based billing — model + ARQ job stub — so adding a metered feature later is a one-day task, not a one-week one.

**Build**

- `backend/app/modules/billing/usage_models.py`:
  ```python
  class UsageRecord(SQLModel, table=True):  # UUIDPKMixin + TimestampMixin + OrgScopedMixin
      metric: str = Field(index=True)        # "api_calls", "ai_tokens", "exports"
      quantity: int                          # always non-negative integer (multiply for fractional)
      occurred_at: datetime = Field(index=True)
      reported_to_stripe_at: datetime | None = None
      stripe_usage_record_id: str | None = None
      idempotency_key: str | None = Field(default=None, unique=True, index=True)
      # idempotency: dedup at write time via the unique constraint
  ```
- `backend/app/modules/billing/usage_service.py`:
  - `UsageService.record(self, *, metric, quantity, idempotency_key=None) -> UsageRecord`:
    - Optionally use idempotency_key to skip duplicates (rolling 24h)
  - `UsageService.aggregate(self, *, metric, since, until) -> int`
- `backend/app/workers/jobs/usage.py`:
  - `report_usage_to_stripe(ctx) -> None` — scheduled every 15 min:
    - Find unreported records, group by `(org_id, metric)`, sum
    - Call `stripe.SubscriptionItem.create_usage_record(...)` for each `(subscription_item, metric)` (mapping config in `Plan.features.usage_metric_to_stripe_item`)
    - Mark records reported
- Helper dependency `def record_usage(metric: str, quantity: int = 1) -> Callable` — usable as a FastAPI dependency on any metered route

**Tests**

- Duplicate idempotency_key within 24h → second insert rejected (unique constraint)
- Aggregation respects time window
- Mock Stripe: usage records reported and marked
- 100% coverage on `usage_service.py`

**Done when**

- [ ] All usage tests green
- [ ] Module README documents how to add a new metric

---

### T-133 Invoice history endpoint + UI

> HTML ref: §"Billing" → "Invoice history"
> needs: T-031

**Goal** Show users their past invoices without storing them ourselves (fetch from Stripe, cache for performance).

**Build**

- `backend/app/modules/billing/invoices.py`:
  - `InvoiceService.list_for_org(self, *, after_cursor=None, limit=20) -> tuple[list[InvoiceDTO], cursor]`:
    - Cache key `invoices:{org_id}:{cursor or "first"}` with 5 min TTL
    - On miss: `stripe.Invoice.list(customer=org.stripe_customer_id, starting_after=cursor, limit=limit)`
    - Returns DTOs: `{ id, number, amount_paid, currency, status, hosted_invoice_url, invoice_pdf, period_start, period_end, created_at }`
  - `InvoiceService.invalidate(self) -> None` — called by webhook handler on `invoice.*` events
- `GET /api/v1/billing/invoices` — cursor-paginated; requires `BILLING_READ`
- Web: Settings → Billing → "Invoices" tab:
  - Table with date, amount, status, "Download PDF" link to `invoice_pdf` URL (Stripe-hosted, no PCI scope)
  - Empty state for orgs without an active subscription

**Tests**

- Mock Stripe list → DTOs correctly shaped
- Cache invalidated by webhook
- BILLING_READ permission gates the endpoint
- Cross-tenant isolation (org A cannot see org B's invoices)

**Done when**

- [ ] All endpoint tests green
- [ ] UI renders against mocked + real Stripe

---

## P2-E · Admin panel

> HTML ref: §"Admin panel" stub in Phase 2

### T-140 is_superadmin guard

> needs: T-021

**Goal** A dedicated dependency separate from RBAC. Superadmin is a platform-level role (the ForgeSaaS operators), distinct from org-level roles.

**Build**

- `backend/app/core/dependencies.py` (extend):
  - `require_superadmin(user: User = Depends(get_current_user)) -> User`:
    - Raises `PermissionDeniedError` if `user.is_superadmin is False`
    - Logs every superadmin access with structured fields (path, query, request_id, actor_id, target if extractable)
- All admin routes live under `/api/v1/admin/`
- Admin routes are explicitly excluded from the OpenAPI schema shown to non-admin tokens (`include_in_schema=False` for the router, or schema filtered by token introspection)
- A separate Loguru sink writes admin actions to `admin-audit.log` in addition to the normal log stream

**Tests**

- Non-superadmin → 403
- Superadmin → 200
- Access logged with `admin_action=true` field
- Admin endpoints not in OpenAPI schema for non-admin tokens

**Done when**

- [ ] Permission test passes
- [ ] Separate admin log sink verified

---

### T-141 Admin endpoints — list orgs, list users, manual plan override

> needs: T-140, T-025, T-031

**Goal** Read-mostly endpoints for support operations.

**Build**

- `backend/app/modules/admin/router.py` — all under `/api/v1/admin/`:

  | Method | Path                      | Purpose                                                                                                                                                 |
  | ------ | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | GET    | `/orgs`                   | Cursor list with filters: `?q=` (slug or name), `?plan=`, `?status=`                                                                                    |
  | GET    | `/orgs/{id}`              | Full org detail including subscription, member count, file count, last activity                                                                         |
  | GET    | `/users`                  | Cursor list with filters: `?q=` (email), `?active=`                                                                                                     |
  | GET    | `/users/{id}`             | Full user detail including all memberships, sessions, audit summary                                                                                     |
  | PUT    | `/orgs/{id}/plan`         | Body: `{ plan_slug: str, billing_interval: ..., free_override: bool, expires_at: datetime? }` — Force-assign a plan. Records audit event with full diff |
  | POST   | `/orgs/{id}/extend-trial` | Body: `{ days: int }` — Extends trial by N days                                                                                                         |
  | POST   | `/orgs/{id}/comp-credit`  | Body: `{ amount_cents: int, reason: str }` — Issues Stripe coupon                                                                                       |

- All write operations record `AuditEvent` with `action="admin.<op>"`, `actor_id=superadmin.id`, full diff
- Audit log entries for admin actions get a special `severity="elevated"` tag so they can be alerted on

**Tests**

- Non-superadmin → 403 for every endpoint
- Plan override updates org and writes audit event with diff
- Trial extension updates `subscription.trial_end`; webhook reconciliation respects manual extensions
- Comp credit creates a Stripe coupon via mocked Stripe; audit logged

**Done when**

- [ ] All admin endpoints tested
- [ ] Diff captured in every audit event

---

### T-142 User impersonation

> needs: T-140, T-028

**Goal** Superadmin can act as a user for support — issues a short-lived access token scoped to one user, fully audited, with a banner so it's impossible to forget you're impersonating.

**Build**

- `backend/app/modules/admin/impersonation.py`:
  - `POST /api/v1/admin/users/{user_id}/impersonate` — body: `{ reason: str (required, min 10 chars), duration_minutes: int (max 60) }`
    - Validates reason (free-form but stored verbatim)
    - Mints a special access token with claim `impersonator_id=<superadmin.id>` and `imp_exp=<short-lived>` (max 60 min, hard cap)
    - **Crucial:** these tokens cannot be refreshed (no refresh token issued)
    - Cannot impersonate other superadmins (defence-in-depth)
    - Audit event: `admin.impersonate.start`, severity=elevated, payload includes reason
  - `POST /api/v1/admin/impersonate/end` — invalidates the current impersonation token (writes a deny entry to Redis blocklist for `jti`)
- JWT verification (T-014) updated to:
  - Reject tokens whose `jti` is in the Redis blocklist
  - On every request with `impersonator_id` present, attach `impersonator_id` to logs + audit events for all actions taken
- Frontend: when access token contains `impersonator_id`, the app shell shows a fixed top banner: "Impersonating <email> — [End impersonation]". Banner is non-dismissable.

**Tests**

- Impersonate non-superadmin user → token issued, banner shows in frontend
- Impersonate another superadmin → 403
- Token refresh attempt with impersonation token → 401
- Hard 60-minute cap enforced (token rejected after expiry even if not explicitly ended)
- Every action during impersonation has `impersonator_id` in its audit event
- Ending impersonation: token rejected on next use
- Missing reason → 422

**Done when**

- [ ] All security boundary tests green
- [ ] Audit trail complete
- [ ] Banner visible in web UI

---

### T-143 Admin web UI

> needs: T-141, T-142, T-122

**Goal** Separate web surface for admin operations — never mixed with the user-facing app.

**Build**

- `apps/web/src/routes/_admin/` — separate layout (no org switcher, distinct header color, "ADMIN" badge), separate guard requiring `user.is_superadmin`
- Pages:
  - `/_admin/` — dashboard: counts (orgs, users, MRR estimate from Stripe), recent signups, recent payment failures
  - `/_admin/orgs` — searchable list with quick filters
  - `/_admin/orgs/$id` — detail view; tabs for Subscription, Members, Files, Audit, Feature flags
  - `/_admin/users` — searchable list
  - `/_admin/users/$id` — detail; impersonate button (modal asks for reason, duration)
  - `/_admin/audit` — global audit feed, filterable by actor, action, date
- All admin pages have a visible "Admin" badge in the header to prevent confusion with user-facing pages
- Admin routes never appear in the user-app sidebar (separate router group)

**Tests** (Vitest + RTL)

- Non-superadmin navigating to `/_admin/` → redirect to user app
- Impersonate flow: click → modal → submit → access token replaced → banner appears → "End impersonation" returns to admin
- Search debounce and pagination work

**Done when**

- [ ] All admin pages render
- [ ] Impersonation flow works end-to-end against the local backend
- [ ] Visual distinction (color, badge) clear

---

## P2-F · Storybook + design system

> HTML ref: §"Build phases" → "Phase 2" → "Storybook for packages/ui components"

### T-150 Storybook for packages/ui

> needs: T-041, T-046 (FileUploader), T-106 (email templates)

**Goal** Every public component in `packages/ui` and every email template in `packages/email-templates` has a Storybook story. New components are not merged without a story.

**Build**

- `packages/ui/.storybook/`:
  - `main.ts` with Vite framework, MDX support, a11y addon, viewport addon, interactions addon
  - `preview.tsx` wraps every story in: ThemeProvider (light/dark toggle), `@tanstack/react-query` test provider, Tailwind globals
- Stories for every component from T-041:
  - `Button.stories.tsx` — variants (default, destructive, outline, ghost), sizes, loading + disabled
  - `Input.stories.tsx` — with label, with error, disabled
  - `Form.stories.tsx` — full form with Zod schema
  - `Dialog.stories.tsx`, `Tabs.stories.tsx`, `Table.stories.tsx`, `Badge.stories.tsx`, `Avatar.stories.tsx`, `DropdownMenu.stories.tsx`, `Skeleton.stories.tsx`, `Alert.stories.tsx`, `Separator.stories.tsx`, `Tooltip.stories.tsx`
- Stories for Phase 2 additions:
  - `FileUploader.stories.tsx` — idle, uploading, success, error states (mocked API via MSW)
  - `NotificationBell.stories.tsx` — empty, with unread, dropdown open
- Email template stories under `packages/email-templates/.storybook/` — each template rendered with example props
- Accessibility: every story automatically tested with axe in CI (a11y addon error mode)
- Visual regression: chromatic config in CI (Phase 3 wires the actual snapshots; Phase 2 just configures the project)

**Tests**

- `pnpm --filter @forge/ui storybook:test` runs Storybook test-runner; every story renders without throwing; every interaction passes
- A11y violations in any story → test fails
- CI: `storybook:build` outputs static files; published to a preview URL per PR

**Done when**

- [ ] Every component in `packages/ui/src/components/` has a story
- [ ] Every email template has a story
- [ ] axe-core in test-runner passes on all stories
- [ ] Static Storybook builds in CI

---

## §Phase-2 verification

### T-160 Phase 2 acceptance runbook

> needs: every Phase 2 task

**Goal** Document and execute the Phase 2 acceptance flow. This is the gate to Phase 3.

**Build**

- `docs/runbooks/phase2-verification.md` documenting exact steps:
  1. `make dev` — all services up; `make seed` — superadmin + demo plans (now includes a "Pro" plan with feature flags `advanced_reports`, `file_storage`)
  2. **Notifications happy path:** as user A, invite user B → user B receives email (MailHog) + in-app notification appears in user A's bell within 1 s (via SSE)
  3. **Push:** install mobile app on simulator, log in, accept push permission → register a push token → trigger an admin-side test push → notification received
  4. **File storage:** upload a file via web dropzone → file appears in list with status "ready" → download via signed URL works
  5. **File storage quota:** attempt to upload a file that would exceed plan quota → 402 with clear message
  6. **Virus scan:** with `VIRUS_SCANNER=noop`, status transitions to "ready" + virus_scan_result="clean"
  7. **Feature flags:** as superadmin, set override `advanced_reports=false` for the user's org → within 60 s the corresponding nav item disappears in the user's UI
  8. **Dunning:** trigger a Stripe test event `invoice.payment_failed` via Stripe CLI → DunningCampaign created → first email sent (visible in MailHog)
  9. **Trial expiry:** with `freezegun`-style time advance via a test endpoint (admin-only), set `trial_end` to 3 days from now → run scheduler manually → 3-day warning email sent
  10. **Invoice history:** Settings → Billing → Invoices shows past invoices; download link opens Stripe-hosted PDF
  11. **Impersonation:** superadmin impersonates a user → UI shows red banner with "Impersonating <email>" → ending returns to admin
  12. **Storybook:** `pnpm --filter @forge/ui storybook` opens, all components render, a11y errors zero

**Phase 2 complete when ALL of:**

- [ ] Runbook executes without intervention
- [ ] `pytest --cov` reports ≥85% overall, 100% on `auth/`, `billing/`, `tenancy/`, `gdpr/`, `notifications/`, `storage/`, `feature_flags/`, `admin/`
- [ ] CI jobs (lint, test, security, build) green on `main`
- [ ] Cross-tenant isolation sweep (T-049) extended to cover every new Phase 2 endpoint
- [ ] Schemathesis (T-050) clean on the new endpoints
- [ ] Storybook test-runner passes; all stories a11y-clean
- [ ] Import-linter and Semgrep rules pass
- [ ] Launch checklist sections from `forgesaas-plan.html` covering notifications + files + admin all checked
