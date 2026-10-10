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

- **Resend and revoke invitations.** (ticket 08) The pending-invitations list has no actions
  because there are no endpoints for them. Needs `POST /orgs/{id}/invitations/{inv}/resend` and
  `DELETE /orgs/{id}/invitations/{inv}`, then a "⋯" menu on invitation rows (Resend, Copy link,
  separator, Revoke).

## Frontend

- **Removing a member has no confirmation.** (pre-existing, kept in ticket 08) It acts at once.
  The design rules (F-009) ask for a confirm on actions that affect other users. Wrap it in the
  existing `ConfirmDialog`.
- **Leaving an organization.** (ticket 08) The old list showed Remove on your own row; the new one
  shows no actions on your own row (so you can't demote or remove yourself by accident). There
  is no other "Leave organization" path; add one deliberately (e.g. Organization settings) if
  members should be able to leave.

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
- **Look at the rail and the other settings pages by hand.** Ticket 14's screenshots cover the
  dashboard, Profile and Organization (desktop, phone, `ar`) and the e2e suites walk the rest,
  but nobody has looked at the collapsed rail's tooltips and animation, dark mode by eye, or
  Members/Billing/Files/API keys/Webhooks in a browser.
- **Lighthouse on marketing.** (ticket 02) Fails locally on Windows (chrome-launcher EPERM); CI
  must confirm LCP with the self-hosted font.
- **Review the machine translations.** Ticket 14 translated 84 keys into ar, de, es and fr and
  listed each in that locale's `__review.json`. A native speaker should review and empty those.

## Product questions

- **Checklist on phones.** Hints wrap to three or four lines beside the button at 375px. Consider
  putting the button under the text below ~30rem.

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

- **Members list names, emails, photos; search and count; Files uploader names.** Done after the PR: `user` on each membership, `q` (email only, since names are encrypted), `meta.total`, a session-only member avatar route, `uploader` on each file.
- **Signing in replaced a device's theme with the account's.** A device's explicit choice now wins over an account still on the default, and is saved to it.
- **Translations for new keys.** Machine-translated in ticket 14 and flagged for review (above).
- **Visual baselines.** Regenerated and passing since ticket 14.
- **Create-your-first-organization screen was still a card.** Uses `AuthLayout` since ticket 11.
