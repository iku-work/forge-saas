# Visual overhaul: follow-ups

Things the overhaul found but deliberately did not do, so they are not lost. Each entry says
where it came from and what "done" would look like. Add to this file whenever a ticket defers
something; move an entry to the bottom section when it is resolved.

## Backend

- **Remove a profile photo.** (ticket 06) The Profile page has "Upload photo" but no "Remove":
  `PATCH /api/v1/auth/me` accepts a new `avatar_file_id` but there is no way to clear it.
  Needs: an explicit clear (e.g. `avatar_file_id: null` allowed and handled, or
  `DELETE /api/v1/auth/me/avatar`), what happens to the stored file, and a web "Remove" button
  in the Photo row.
- **Organization logo upload.** (pre-existing, kept in ticket 07) The Organization page's logo
  row shows a disabled "Upload logo" with "coming soon". Needs a storage path for org logos
  (same shape as avatars) and the button wired up.

- **Members list has no names, emails or photos.** (ticket 08) `GET /orgs/{id}/members` returns
  `MembershipPublic` (ids, role, invited_by, created_at) only, so other members show as a short
  user id. Needs a user summary on each membership (full_name, email, has_uploaded_avatar),
  fetched through the auth service in one batch (§0.1 cross-module rule; T-243's query budget
  for list endpoints), then the Members row shows avatar + name + email like the design.
  The same gap shows on **Files** (ticket 09): the uploader column can only show two letters of
  the uploader's user id. Whatever summary the members list gets, `StoredFilePublic` should
  carry for `uploaded_by`.
- **Resend and revoke invitations.** (ticket 08) The pending-invitations list has no actions
  because there are no endpoints for them. Needs `POST /orgs/{id}/invitations/{inv}/resend` and
  `DELETE /orgs/{id}/invitations/{inv}`, then a "⋯" menu on invitation rows (Resend, Copy link,
  separator, Revoke).

## Frontend

- **Member search and count.** (ticket 08) Skipped: with no names to search and a paginated list,
  a client-side filter or a "N members" count would mislead. Add both once the backend gap above
  is closed (a server-side `q` parameter for search, a total in the list meta for the count).
- **Removing a member has no confirmation.** (pre-existing, kept in ticket 08) It acts at once.
  The design rules (F-009) ask for a confirm on actions that affect other users. Wrap it in the
  existing `ConfirmDialog`.
- **Leaving an organization.** (ticket 08) The old list showed Remove on your own row; the new one
  shows no actions on your own row (so you can't demote or remove yourself by accident). There
  is no other "Leave organization" path; add one deliberately (e.g. Organization settings) if
  members should be able to leave.

- **Translations for new keys.** Tickets 04–10 added English keys (sidebar groups, "Back to
  app", collapse/expand, section titles and hints, page descriptions, row-menu labels, member
  and invoice status words, the dashboard welcome and checklist, the auth pages' Terms/Privacy, "Admin console", the marketing hero's sample content and
  "Read the guides"). de, es, fr, fi and ar
  fall back to English until translated via the translator workflow
  (`docs/runbooks/translations.md`). Ticket 03's copy changes *were* translated.
- **Notifications Save is always enabled.** (ticket 07) Unlike other sections, on purpose:
  saving persists the shown defaults and heals legacy rows. If that healing moves to the
  backend (normalise on read), the page could adopt "Save only after a change".
- **Create-organization button is h-9 on the first-organization screen.** (ticket 11)
  `CreateOrgForm` is shared with the switcher's dialog, so its button kept the default size
  while the auth pages' buttons are h-10. Give the form a size prop if the 4px matters.
- **Invoice statuses outside Stripe's five.** (ticket 09) draft/open/paid/uncollectible/void
  are translated and toned; anything else shows Stripe's raw word with a neutral pill.
- **Dashboard after setup is empty.** (ticket 10) Once every checklist step is done the dashboard
  shows only its header and "Your organization is set up." By design for now; real dashboard
  content (recent activity, usage) is product work.
- **"Choose a plan" stays open during a trial.** (ticket 10) Done means `status === "active"`.
  If a product has a free plan that is also "active", it ticks immediately; if trials should
  count, change the rule in `useGettingStarted.ts`.
- **Admin: raw status words.** (pre-existing, kept in ticket 12) Subscription statuses,
  audit severities and member roles on admin pages show the API's word (`past_due`,
  `elevated`, `owner`) inside the new pills. Translate them like the invoice statuses.
- **Admin: no rail, no ⌘K.** (ticket 12) The admin sidebar does not collapse and the command
  palette is app-only. Add both if superadmins spend long sessions there.
- **Admin: native selects.** (pre-existing) The users/organizations filters and the banner type
  are plain `<select>`s styled by hand; there is no Select primitive in `@forge/ui` yet.
- **Marketing headings on other pages.** (ticket 13) Only the landing page uses the condensed
  display type; the features/pricing/guides/blog/legal titles and prose headings still use the
  plain weight. Bring them onto `type-title` if they should match.
- **Marketing dark mode.** (pre-existing) The site has none; the tokens support it.
- **"Save" vs "Save changes".** (ticket 06) Buttons kept "Save" so existing translations stay
  valid. Decide one wording and apply it everywhere at once.

## Verification still owed

- **Look at the admin console (ticket 12) in a running app.** Needs Docker and a superadmin
  (`make seed` creates one). Check the marker in light and dark, the drawer at phone width,
  the impersonation banner above the shell, and the product settings rows.
- **Look at tickets 04–10 in a running app.** None of the shell, rail, or settings pages has been
  seen in a browser yet: login needs the backend (Docker Desktop was stopped). Check desktop,
  < ~860px wide (drawer), `ar`, dark mode, the rail's tooltips and animation.
- **Lighthouse on marketing.** (ticket 02) Fails locally on Windows (chrome-launcher EPERM); CI
  must confirm LCP with the self-hosted font.
- **Visual baselines.** Expected red until ticket 14 regenerates them.

## Tooling and tests

- **`NotificationFeedVirtual` arrow-key test is load-sensitive.** Failed twice in full runs while
  other work ran, passed alone and on rerun. Pre-existing timing weak spot.
- **Pre-commit hooks are not installed in the worktree.** Run
  `C:\Users\IK\Documents\saas-template\backend\.venv\Scripts\pre-commit.exe run --files <files>`
  by hand, or install hooks in the worktree.
- **Web tests regenerate `apps/web/src/routeTree.gen.ts`.** Revert it before committing.
- **The `dev` launch entry (main checkout's `.claude/launch.json`) fails:** `turbo run dev`
  starts 11 persistent tasks against turbo's default concurrency of 10. Use the
  `web-visual-overhaul` entry instead, or add `"concurrency": 12` (or more) to `turbo.json` if
  the whole monorepo should start together.
- **Windows-only Astro build flake.** Back-to-back builds sometimes fail renaming
  `.astro/content-assets.mjs` (EPERM); `apps/marketing/tests/helpers.ts` retries that exact error
  on Windows. Revisit if Astro fixes the rename.

## Resolved

- **Create-your-first-organization screen was still a card.** Uses `AuthLayout` since ticket 11.
