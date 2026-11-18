# Mobile: upgrade, close the gaps, prove it on simulators

Status: ready-for-agent

Written 2026-11-18 on the Windows machine, for a Claude Code session on the
user's Mac (Xcode and Android Studio installed, versions possibly old). That
session starts without the conversation that produced this. Everything it
needs is here or in the files it names.

## Why

The template's verdict on 2026-11-18: web and infrastructure are ready, and
mobile is the weakest part. It was never reviewed in depth, its Maestro flows
have never run, and it pins Expo SDK 52, whose `@expo/cli` pulls `tar` 6.
`.trivyignore.rego` accepts that only until **2027-01-01**. From then on, CI's
`security` job fails for this repo and every product made from it.

## Ground rules

- Read `AGENTS.md` §0 first. §0.11 (every user-facing string through `t()`)
  and §0.12 (supply-chain advisories, before any `pnpm install`) bite hardest
  here.
- One PR per step below, each green in CI before the next starts. The user
  merges; commit and push only to your own branches. Never name a branch
  `dependabot`.
- Stage files by name, never `git add -A`.
- The Windows session runs a pentest against staging at the same time. Don't
  change staging or its settings.
- If a decision is not covered here, ask the user. Don't guess.

## Order of work

### 0. Toolchain check (no PR)

Report the versions of macOS, Xcode, the iOS simulator runtimes, Android
Studio and its emulator images, Node, pnpm, Docker and Maestro. Then report
what the target Expo SDK needs. Update only what that SDK requires, and ask
the user before any update that needs their password or a restart.

### 1. Expo SDK upgrade

- Go from SDK 52 to the latest stable SDK, **one major at a time**:
  `npx expo install expo@^<next> --fix`, then `npx expo-doctor`, read that
  SDK's upgrade notes, fix, test, and only then the next major.
- Done when:
  - `@expo/cli` no longer pulls `tar` 6;
  - the `tar`, `image-size` and Expo-related entries can come out of
    `pnpm-workspace.yaml`'s `auditConfig`, `.trivyignore.yaml` and
    `.trivyignore.rego` (each entry says what ends it);
  - `pnpm --filter mobile lint type-check test` passes;
  - the app builds and opens on both simulators.
- `patches/expo-localization@16.0.1.patch` patches the iOS module. Read it,
  and check whether the new SDK's `expo-localization` still needs it before
  porting or dropping it.
- In the same PR or right after it, get `@react-navigation/native` to 7.5.1
  or later. It arrives through `expo-router`, not as a direct dependency, so
  the new SDK may already bring it; check with `pnpm why`. That drops
  `query-string` and with it the patched `decode-uri-component`. Then delete:
  - `patches/decode-uri-component@0.2.2.patch` and its `patchedDependencies`
    entry;
  - `apps/mobile/src/test/decodeUriComponentPatch.test.ts`;
  - the CVE-2026-118664 entry in `.trivyignore.yaml`;
  - GHSA-qr28-fpjm-wq7f and GHSA-vcc3-ghjq-m6fr in `auditConfig`.

  It changes deep-link parsing, so step 2's deep-link flow must pass after it.
- CI's `security` job installs with a one-week minimum release age, stricter
  than local. Re-resolve the lockfile with
  `--config.minimum-release-age=10080` before pushing new dependencies.

### 2. Run the Maestro flows for the first time

- `apps/mobile/.maestro/` (`login`, `logout`, `deep-link`) was written in July
  2026 and has **never run**. Expect the selectors, the `runScript` globals
  and the `retry` syntax to need fixes. `apps/mobile/README.md` has the setup.
- They need the local backend stack (`docker compose up -d` at the repo root,
  then `make seed`). `deep-link` reads its invitation email from MailHog.
- Run on the iOS Simulator and an Android emulator. Physical devices and real
  push delivery are out of scope (decided 2026-07-04).

### 3. Verify on a simulator what was never run

Each of these has unit tests but has never run on a device. Fix whatever
fails:

- **File upload.** `src/lib/uploads.ts` posts an S3 POST policy: `upload_url`
  is the bucket root, every signed value is in `fields`, the file part goes
  last, and **no `headers`** (on Android an explicit Content-Type strips the
  multipart boundary). Upload a file and check it reaches `ready` after the
  virus scan.
- **Avatar** upload and removal (`settings.tsx`).
- **Turnstile** (`src/components/CaptchaCheck.tsx`, a WebView on the API's
  origin) on sign-up, magic link and forgot password. Use Cloudflare's
  always-pass test keys locally (site key `1x00000000000000000000AA`).
- **Impersonation handoff:** scan the web admin's QR, then confirm the phone
  is in the impersonated session and that Cancel ends it (T-261,
  `docs/runbooks/mobile-impersonation.md`).
- **Deep links:** verify-email, reset-password, invitation, OAuth callback.

### 4. Close the feature gaps with web

Known missing on mobile:

- the in-app **Invitations** list (`GET /api/v1/me/invitations`, accept and
  decline by id, as on web's `/invitations`);
- the **feedback** form (`POST /api/v1/feedback`, as in web's sidebar);
- the **upload-limit hint** (`GET /api/v1/files/limits`, shown before upload,
  as in `packages/ui/src/components/FileUploader.tsx`).

First list every other web feature the app lacks, such as org switching,
member management, billing, sessions, 2FA settings and account deletion, and
**ask the user which belong in the template's mobile app**. The template app
is a starting point, not a copy of web.

### 5. Mobile security pass

Check each, and write up the findings in `docs/` in the shape of
`docs/production-readiness-review-2026-11-02.md`:

- Tokens only in `expo-secure-store`, never AsyncStorage (§0.6). Nothing
  sensitive in logs or Sentry breadcrumbs.
- Deep-link handlers accept only the expected hosts and paths, and treat every
  parameter as untrusted.
- WebViews (Turnstile) cannot be steered to other origins and expose no
  native bridge.
- Session lifecycle: refresh rotation, sign-out clears secure storage, and an
  impersonated session ends where it should.
- Release builds ship no dev menu, no debug endpoints, no cleartext HTTP.

## Already known, don't rediscover

- My notes from July said the upload client sent a PUT. It was fixed in PR #8
  (2026-09-09) and has not run on a device since.
- Settings is reached from the dashboard's Settings button, added in T-222.
- Push needs EAS and an Apple Developer account. Out of scope.

## Done when

Steps 1 to 5 are merged, both simulators pass every Maestro flow, the expiring
ignore entries are gone, and the security write-up is in `docs/`.
