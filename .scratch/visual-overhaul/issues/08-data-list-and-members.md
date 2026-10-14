# 08: Shared list style, proven on the Members page

**What to build:** one list look used everywhere: full-width hairline rows, muted
column labels, a filter box and a count above, status as a small pill with a dot and a
word, and row actions in a trailing "⋯" menu with destructive actions separated and in
red. The Members page (members plus pending invitations) uses it, keeping its existing
virtualized scrolling.

**Blocked by:** 06.

**Status:** done

## Read first

1. `apps/web/src/features/settings/MembersSettingsPage.tsx`: read lines 1–60 (imports),
   then search `<VirtualList` and read ~120 lines from there (the row rendering, the role
   dropdown, remove action).
2. `apps/web/src/features/settings/PendingInvitationsCard.tsx`.
3. `apps/web/src/components/VirtualList.tsx`, lines 1–60 only (props; note that rows are
   `role="listitem"` inside a `role="list"`, chosen on purpose for virtualization; keep it).
4. `packages/ui/src/components/table.tsx` (restyled here for non-virtual lists).
5. Prototype CSS: `.toolbar`, `.list`, `.person`, `.pill`, `.actions`, `.menu-pop`.

## Design

- No outer card. Column labels row: `text-[0.78rem] font-medium text-muted-foreground`,
  sentence case, `py-2 px-3 border-b border-border`.
- Rows: `px-3 py-3 border-b border-border`, hover `bg-primary-wash`. First column has an
  avatar (2rem) + name (`font-medium`) over email (`text-[0.8125rem] text-muted-foreground`).
  The current user's row shows a muted "You" after the name and no actions menu.
- Secondary columns (dates etc.) `text-muted-foreground whitespace-nowrap`.
- StatusPill: `inline-flex items-center gap-1.5 rounded-full bg-surface-sunken px-2 py-0.5 text-xs font-medium text-foreground-secondary`,
  with a 0.375rem dot before the text: filled green (`bg-[--success]`; add a `--success`
  token: light `#1f9d6b`, dark `#4cc79a`) for active, a hollow ring (`ring-[1.5px] ring-muted-foreground`,
  transparent fill) for pending. Text always present ("Active", "Invited").
- Toolbar above the list: a search input (`max-w-[17.5rem] h-8`, `type="search"`, labelled)
  and, at the end, a count in `text-[0.8125rem] text-muted-foreground`
  ("6 members, 1 invitation", ICU plural).
- Row actions: an icon-only ghost button (lucide `MoreHorizontal`, `aria-label` "Actions
  for <name>") opening the existing `DropdownMenu` aligned to the end: non-destructive items,
  a separator, then destructive items with `text-destructive`.

## Steps

1. **`@forge/ui`:**
   - Restyle `table.tsx` to the design above (for ordinary, non-virtual lists).
   - New `data-list.tsx` for grid-based lists (works inside `VirtualList`):
     `DataListToolbar({ search, meta })`, `DataListColumns({ template, children })` (the
     label row, `aria-hidden="true"` because each row carries its own labels for screen
     readers), `DataListRow({ template, children, ...props })` (a grid row using the same
     `grid-template-columns` string), `RowActionsButton` (the ⋯ trigger, forwards ref so
     it works as a `DropdownMenuTrigger asChild`).
   - New `status-pill.tsx`: `StatusPill({ tone: "active" | "pending" | "neutral", children })`.
   - Stories for `data-list` and `status-pill`. Export all. Build.

2. **Members page:** `PageHeader` (title, description "People who can sign in to your
   organization, and what they can do.", action "Invite member" primary button, which opens
   whatever invite UI exists today). Then the toolbar (filter by name/email, client-side over
   loaded rows is acceptable; note the limitation in a comment if the list is paginated),
   then the list: columns Member, Role, Status, Joined, (actions). Keep `VirtualList`, its
   props and its `itemTestId`; render each row with `DataListRow`.
   - Role: plain text for members you can't manage; for those you can, the existing role
     dropdown, but triggered from the "⋯" menu's "Change role" item rather than a badge.
     If that changes the flow too much, keep the badge-dropdown but restyle the badge as a
     subtle text button. Keep test ids.
   - Pending invitations: render them in the **same list** after members (status "Invited",
     date "Sent <date>", actions: Resend, Copy link, separator, Revoke) if the data is
     available on this page; otherwise keep `PendingInvitationsCard` as a second list with
     the same styles and no card. Keep its tests passing.

3. **Translations** (en): toolbar label/placeholder, count (ICU plural), column labels,
   "You", "Actions for {name}", status words. Rebuild `@forge/i18n`.

## Tests

Existing: `MembersSettingsPage.test.tsx`, `MembersSettingsPageVirtual.test.tsx`,
`PendingInvitationsCard.test.tsx` must pass (update selectors only where the old badge or
card was assumed). Add:
- the actions menu for another member opens with focus on the first item and shows
  "Remove from organization" (or the existing remove wording) after a separator;
- the current user's row has no actions button;
- typing in the filter narrows the visible rows.

## Verify

```bash
pnpm --filter @forge/ui build
pnpm --filter web exec vitest run src/features/settings/Members
pnpm --filter web exec vitest run src/features/settings/PendingInvitationsCard.test.tsx
pnpm --filter web type-check && pnpm --filter web lint && pnpm --filter web test
```

## Acceptance

- [x] `table.tsx` restyled; `data-list.tsx` and `status-pill.tsx` added with stories.
- [x] Members page: page header, toolbar, list rows, status pills, row menus.
- [x] Virtualization kept (`role="list"`/`listitem`), test ids kept.
- [x] Status never relies on colour alone.
- [x] Tests pass; lint and type-check clean.

## Notes

Completed 2026-10-05; see `PROGRESS.md`. Deviations, all logged in `FOLLOW-UPS.md`: no filter/count toolbar
(no names to search; paginated count would mislead), members shown by short id (API has no names),
invitations have no actions (no endpoints), removal still has no confirmation (pre-existing).
