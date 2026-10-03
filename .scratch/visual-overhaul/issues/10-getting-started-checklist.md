# 10: Getting started checklist on the dashboard

**What to build:** a new Organization's dashboard shows "Get started": a short list of
steps with a progress count, each step with one button that takes you to do it. Steps
tick themselves off from real data. Steps for modules the product doesn't have, or that
the member isn't allowed to do, are not shown. When every visible step is done, the
checklist is gone.

**Blocked by:** 06.

**Status:** ready-for-agent

## Read first

1. `apps/web/src/routes/$locale._app.index.tsx` (the dashboard route, ~70 lines).
2. `apps/web/src/features/DashboardEmptyState.test.tsx` and `DashboardEmptyStateNoFiles.test.tsx`
   (how tests mock modules; one file per module configuration, keep that pattern).
3. `apps/web/src/lib/navigation.ts`, lines 1–60 and `useVisibleNavItems` (how module and
   permission gates are resolved; reuse `usePermission` / `useModules` the same way).
4. Prototype CSS: `.progress`, `.track`, `.step`, `.tick`, `.step-title`, `.step-hint`, `.done-label`, `.aside-note`.

## Steps and their "done" signals

Use the **same query functions and query keys** the real pages use, so the cache is shared
and finishing a step elsewhere updates the dashboard:

| Step (en title) | Shown when | Done when | Button |
|---|---|---|---|
| Complete your profile | always | the user has a name (from `useAuth()`); and a photo too if `modules.fileStorage` | "Edit profile" → `/$locale/settings/profile` |
| Upload your first file | `modules.fileStorage` | files list (`listFilesApiV1FilesGet…`, as `FilesPage` uses it) has ≥ 1 item | "Upload file" → `/$locale/files` |
| Invite a member | permission `Permission.MEMBERS_MANAGE` | members list has > 1 member, or invitations list (`listInvitationsApiV1OrgsOrgIdInvitationsGet…`) has ≥ 1 | "Invite member" → `/$locale/settings/members` |
| Choose a plan | `modules.billing` and permission `Permission.BILLING_MANAGE` | subscription (`getSubscriptionApiV1BillingSubscriptionGet`) is on a paid plan and not trialing (read the response type in `@forge/api-client` to pick the right fields) | "See plans" → `/$locale/settings/billing` |

Find the members list function the Members page uses (search `Api[Vv]1` in
`MembersSettingsPage.tsx`).

The first open step's button is the primary button; the others are secondary. Done
steps show a filled check, a muted struck-through title, and the word "Done" instead of a button.

## Steps

1. **`@forge/ui`:** `checklist.tsx` with `Checklist({ title, progressLabel, done, total, children })`
   (title row with the progress text and a 7.5rem×0.25rem bar; the bar has
   `role="progressbar"` with `aria-valuenow/min/max` and `aria-label` = progressLabel)
   and `ChecklistStep({ title, hint, done, doneLabel, action })` (24px circle tick:
   open = `ring-[1.5px] ring-border-strong`, done = `bg-primary text-primary-foreground` with a check icon).
   Steps are an ordered list (`<ol>`), each step an `<li>`. Story with mixed states. Export, build.

2. **Dashboard route:** replace `EmptyState` with:
   - `PageHeader` title "Welcome to {org}" (ICU, the current Organization's name) and
     description "A few things to finish before your organization is ready."
   - The `Checklist` built from the table above. While its queries are loading, render a
     skeleton of the same shape (use `Skeleton` from `@forge/ui`), not a spinner.
   - Under it, a muted note: "Press {shortcut} to jump to any page or action." with the
     same shortcut text the sidebar search button shows.
   - If every visible step is done: no checklist. Render the page header with the description
     "Your organization is set up." and nothing else (later work can add dashboard content).
   - Keep `data-testid="page-title"`; give the checklist `data-testid="getting-started"`.

3. **Translations** (en, `common.json` under `dashboard.*`): title, description, progress
   label ICU (`{done} of {total} done`), each step's title, hint and button, "Done",
   the shortcut note, the all-done description. Remove the old `dashboard.empty_*` keys
   only after nothing references them (grep). Rebuild `@forge/i18n`.

## Tests (one file per module configuration, like the existing pair)

Rename/replace `DashboardEmptyState.test.tsx` → `DashboardChecklist.test.tsx` (file storage
on, billing on) and `DashboardEmptyStateNoFiles.test.tsx` → `DashboardChecklistNoFiles.test.tsx`
(file storage off). Mock API responses the way the existing tests do. Cover:
- a fresh Organization (no files, one member, trial) shows every allowed step, 0 or 1 done;
- uploading-file step is absent when file storage is off;
- invite and plan steps are absent for a member without `members:manage` / `billing:manage`;
- a step with data (e.g. one file) shows "Done" and no button;
- with all steps done, `getting-started` is not rendered;
- the progress bar's `aria-valuenow` matches the done count.

## Verify

```bash
pnpm --filter @forge/ui build
pnpm --filter web exec vitest run src/features/DashboardChecklist
pnpm --filter web type-check && pnpm --filter web lint && pnpm --filter web test
```

## Acceptance

- [ ] `Checklist`/`ChecklistStep` in `@forge/ui` with a story.
- [ ] Dashboard shows the checklist with real "done" signals and module/permission gating.
- [ ] Loading shows a skeleton; all-done hides the checklist.
- [ ] Tests per module configuration pass; lint and type-check clean.

## Don't

- Don't add a backend field or endpoint; everything comes from existing queries.
- Don't store "dismissed" state.
