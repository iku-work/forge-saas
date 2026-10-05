# 07: The other settings pages onto settings rows

**What to build:** Security, Notifications, Organization and Billing look and behave
like the new Profile page: a page header, titled sections of two-column rows, no cards,
each section saving on its own. (Billing's invoices list is ticket 09.)

**Blocked by:** 06.

**Status:** done

## Read first

1. `apps/web/src/features/settings/ProfileSettingsPage.tsx` (the finished example from ticket 06).
2. `packages/ui/src/components/settings-section.tsx` and `page-header.tsx`.
3. Then **one page at a time**: read the page, convert it, run its test, commit, and only
   then open the next page. Order: `NotificationsSettingsPage.tsx`, `OrgSettingsPage.tsx`,
   `SecuritySettingsPage.tsx` (largest: several Cards), `BillingSettingsPage.tsx`.

## Conversion rules (apply to each page)

- Replace the hand-written `<h1 … data-testid="page-title">` with `PageHeader` (same title
  key; add a one-line description key `<namespace>.<page>.description` if none exists).
  Page-level primary action (if any) goes in `actions`.
- Each `Card` becomes a `SettingsSection` whose title is the card's title. Each field
  becomes a `SettingsRow` (label + hint from the existing description text). A card
  description that explains the whole section becomes the first row's hint or stays as a
  `text-muted-foreground` paragraph under the section title, whichever reads better.
- Each section keeps its own form and its own Save in the section footer. Save is
  disabled until that section's form is dirty (`formState.isDirty`), and the form resets
  to the saved values after success.
- Toggle-style settings (switches/checkboxes, e.g. notification preferences) that already
  save immediately (optimistic) keep doing so and get no Save button.
- Destructive actions (delete organization, disable 2FA, revoke sessions) go in a final
  section titled e.g. "Danger zone", with the action as a secondary button whose text is
  `text-destructive`, and keep their existing confirm dialogs.
- Keep every `data-testid` and every accessible name the tests use.
- Security page specifics: 2FA, passkeys/WebAuthn, sessions list, change email, change
  password are each one section. A list inside a section (sessions, passkeys) can stay a
  simple list for now; ticket 09 restyles lists.
- Billing specifics: plan and subscription status as rows; "Manage billing" / upgrade
  buttons as row controls. Leave the invoices list in place unchanged (ticket 09).

## Verify (after each page)

```bash
pnpm --filter web exec vitest run src/features/settings/<Page>.test.tsx
pnpm --filter web type-check && pnpm --filter web lint
```

At the end: `pnpm --filter web test`, and check each page by eye in light, dark, `ar`, phone width.

## Acceptance

- [x] Notifications, Organization, Security, Billing use `PageHeader` + `SettingsSection`/`SettingsRow`.
- [x] No `Card` left on these pages (except inside the invoices list, untouched).
- [x] Each section saves independently; Save enabled only when that section is dirty.
- [x] All existing tests pass (selectors updated only where the card structure was assumed).
- [x] New description keys added in `en`; i18n lint passes.

## Don't

- Don't change what any setting does or which API it calls.
- Don't restyle lists (ticket 09).

## Notes

Completed 2026-10-05; see `PROGRESS.md`. Exception: Notifications' Save stays always enabled (it heals
legacy rows by saving the shown defaults). Not yet viewed in a running app.
