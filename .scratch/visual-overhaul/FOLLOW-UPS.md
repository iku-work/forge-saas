# Visual overhaul: follow-ups

Things the overhaul found but deliberately did not do, so they are not lost. Each entry says
where it came from and what "done" would look like. Add to this file whenever a ticket defers
something; move an entry to the bottom section when it is resolved.

## Backend

## Verification still owed

- **Look at the admin console (ticket 12) in a running app.** Needs Docker and a superadmin
  (`make seed` creates one). Check the marker in light and dark, the drawer at phone width,
  the impersonation banner above the shell, and the product settings rows.
- **Look at the rail and the other settings pages by hand.** Ticket 14's screenshots cover the
  dashboard, Profile and Organization (desktop, phone, `ar`) and the e2e suites walk the rest,
  but nobody has looked at the collapsed rail's tooltips and animation, dark mode by eye, or
  Members/Billing/Files/API keys/Webhooks in a browser.
- **Lighthouse on marketing.** (ticket 02) Fails locally on Windows (chrome-launcher EPERM); CI
  must confirm LCP with the self-hosted font.
- **Review the machine translations.** Ticket 14 translated 84 keys into ar, de, es and fr and
  listed each in that locale's `__review.json`. A native speaker should review and empty those.

## Product questions

## Tooling and tests

- **`NotificationFeedVirtual` arrow-key test is load-sensitive.** Failed twice in full runs while
  other work ran, passed alone and on rerun. Pre-existing timing weak spot.
- **Pre-commit hooks are not installed in the worktree.** Run
  `C:\Users\IK\Documents\saas-template\backend\.venv\Scripts\pre-commit.exe run --files <files>`
  by hand, or install hooks in the worktree.
- **Web tests regenerate `apps/web/src/routeTree.gen.ts`.** Revert it before committing.
- **`apps/web/e2e` does not type-check.** `tsc -p apps/web/e2e/tsconfig.json` reports six
  `reducedMotion` errors in `test.use()` (pre-existing; Playwright 1.61 types). CI never runs it.
- **Mobile tests time out under a full parallel `pnpm test`.** Use `--concurrency=2`
  (already in the memory notes; load, not a bug).
- **Storybook's test runner finds no stories in this worktree.** Jest searches from the main
  checkout and skips the hidden `.claude/` folder. Run it with `--index-json` against the built
  Storybook instead (77 tests pass that way).
- **`pnpm add --filter <pkg>` can leave other packages' `.bin` unlinked.** Run
  `pnpm install --offline --frozen-lockfile` afterwards.
- **The `dev` launch entry (main checkout's `.claude/launch.json`) fails:** `turbo run dev`
  starts 11 persistent tasks against turbo's default concurrency of 10. Use the
  `web-visual-overhaul` entry instead, or add `"concurrency": 12` (or more) to `turbo.json` if
  the whole monorepo should start together.
- **Windows-only Astro build flake.** Back-to-back builds sometimes fail renaming
  `.astro/content-assets.mjs` (EPERM); `apps/marketing/tests/helpers.ts` retries that exact error
  on Windows. Revisit if Astro fixes the rename.

## Resolved

- **Replaced and removed avatars (and logos) stayed in storage.** A daily `retire_unused_images` job (05:00 UTC) soft-deletes avatar/logo files nothing points at after a day's grace and hands them to `hard_delete_stored_file`.
- **Dashboard after setup was empty.** It now shows Members (count) and Plan (status, renewal) figures and the five newest files, from the checklist's own queries. More (recent activity, usage) is product work.
- **Admin: no rail, no ⌘K.** The admin sidebar collapses with `[` (same per-device setting as the app), has the search button, and ⌘K opens the palette over the admin pages plus Back to app. `RailTooltip` moved to `components/`, `ADMIN_NAV_ITEMS` to `lib/navigation.ts`, and `CommandPalette` takes an optional page list.
- **Hand-styled native selects.** A `Select` in `@forge/ui` (a native `<select>` styled like Input, with a chevron; no new dependency) replaces all six; the organizations status filter now uses the translated labels too.
- **Marketing headings.** One `type-display` class (condensed, the landing's treatment at page-title size) on every page title; prose headings share the width.
- **Marketing dark mode.** CSS only (the site ships no JS): `data-theme="system"` on marketing's `<html>` opts into a copy of the `.dark` tokens under `prefers-color-scheme: dark`; `tests/dark-tokens.test.ts` keeps the copy identical (mutation-checked). The app never sets the attribute.
- **Create-organization button height.** `CreateOrgForm` takes `buttonSize`; the first-organization screen passes `lg`.
- **Admin raw status words.** Subscription states and audit severities translated (`features/admin/statusLabels.ts`, raw word only for an unknown value); member roles reuse the members page's labels.
- **Checklist on phones.** `Checklist` is a container; below 28rem each step's button sits under its text.
- **Notifications Save was always enabled.** The API now returns the resolved matrix (`preferences.effective`: defaults filled, required channels on), so nothing needs saving to heal, and Save waits for a change.
- **"Save" vs "Save changes".** Decided 2026-10-10: keep "Save".
- **"Choose a plan" during a trial.** Decided 2026-10-10: only a paid, active plan ticks it.
- **Invoice statuses outside Stripe's five.** Stripe has only those five; the raw-word fallback is the intended safety net, nothing to do.
- **Organization logo upload.** `organization.logo_object_key` (core migration b7c3e9a1d4f2), a `logos` bucket (JPEG/PNG/WebP), `logo_file_id` on PATCH /orgs/{id}, scan-gated GET /orgs/{id}/logo; the Organization page uploads and removes it and the switcher shows it.
- **Leaving an organization.** `POST /orgs/{id}/leave` (any member; the last owner is told to hand ownership on) and a Leave section, confirmed, at the bottom of Organization settings.
- **Resend and revoke invitations.** New endpoints (resend issues a fresh link and expiry; revoke deletes); a "⋯" menu on each pending invitation, revoke confirmed. "Copy link" skipped: only a hash of the token is stored.
- **Removing a member had no confirmation.** It is confirmed now, naming the member.
- **Remove a profile photo.** The API already cleared on `avatar_file_id: null`; Profile now has a Remove button.
- **Members list names, emails, photos; search and count; Files uploader names.** Done after the PR: `user` on each membership, `q` (email only, since names are encrypted), `meta.total`, a session-only member avatar route, `uploader` on each file.
- **Signing in replaced a device's theme with the account's.** A device's explicit choice now wins over an account still on the default, and is saved to it.
- **Translations for new keys.** Machine-translated in ticket 14 and flagged for review (above).
- **Visual baselines.** Regenerated and passing since ticket 14.
- **Create-your-first-organization screen was still a card.** Uses `AuthLayout` since ticket 11.
