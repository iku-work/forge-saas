# Visual overhaul: follow-ups

Things the overhaul found but deliberately did not do, so they are not lost. Each entry says
where it came from and what "done" would look like. Add to this file whenever a ticket defers
something; move an entry to the bottom section when it is resolved.

## Backend

## Verification still owed

- **Admin console, partly seen (2026-10-10).** Desktop light screenshots from the Docker stack
  show the shell, marker, expanded sidebar, collapsed rail and the admin palette working. Every
  admin page body stops at the mandatory-2FA notice for the seeded superadmin, so the product
  settings rows, the impersonation banner, dark mode and the phone drawer are still unseen:
  enrol TOTP on the seeded admin first. Members/Billing/Files/API keys/Webhooks are walked by the
  e2e suites (26 passed) but not looked at by eye.
- **Lighthouse on marketing.** (ticket 02) Fails locally on Windows (chrome-launcher EPERM); CI
  must confirm LCP with the self-hosted font.
- **Review the machine translations.** Ticket 14 translated 84 keys into ar, de, es and fr and
  listed each in that locale's `__review.json`. A native speaker should review and empty those.

## Product questions

## Tooling and tests

- **Windows-only Astro build flake.** Back-to-back builds sometimes fail renaming
  `.astro/content-assets.mjs` (EPERM); `apps/marketing/tests/helpers.ts` retries that exact error
  on Windows. Revisit if Astro fixes the rename.

## Resolved

- **Tooling (after the PR).** `routeTree.gen.ts` pinned to LF in .gitattributes (tests no longer leave it modified); `apps/web/e2e` type-checks (reducedMotion moved under `contextOptions`, where Playwright 1.61 reads it -- top-level it was likely ignored -- and `WIDTHS` is a const tuple); mobile's `hookTimeout` is 30s, so a full parallel `pnpm test` passes without `--concurrency=2`; `storybook:test` always uses `--index-json` (works from any checkout); `pnpm dev` runs with `--concurrency=16` for its 11 persistent tasks. The NotificationFeedVirtual flake was already mitigated on main (3405a36c, 45s budget) and has not recurred. Environment-only notes (pre-commit hooks not installed in a worktree; `pnpm add --filter` leaving `.bin` unlinked) live in the memory notes instead.
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
