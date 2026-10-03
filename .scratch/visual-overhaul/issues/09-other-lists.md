# 09: The other lists onto the shared list style

**What to build:** Files, API keys, Webhooks (endpoints and the delivery log), Invoices
(on the Billing page) and the Reports page header use the same page header and list look
as Members. After this ticket no page in the app hand-writes its title or wraps a list
in a card.

**Blocked by:** 07, 08.

**Status:** ready-for-agent

## Read first

1. `apps/web/src/features/settings/MembersSettingsPage.tsx`, the finished example from
   ticket 08: search `PageHeader` and `DataListRow` and read those parts only.
2. `packages/ui/src/components/data-list.tsx`, `status-pill.tsx`, `table.tsx`.
3. Then **one page at a time** (convert, test, commit, next):
   `FilesPage.tsx`, `ApiKeysPage.tsx`, `WebhooksPage.tsx`, the invoices part of
   `BillingSettingsPage.tsx`, the reports route (`apps/web/src/routes/$locale._app.reports.tsx`).

## Conversion rules

- `PageHeader` replaces the hand-written `<h1>` (keep `data-testid="page-title"` via the
  component's default). The page's main action ("Upload file", "Create API key",
  "Add endpoint") goes in `actions` as the primary button.
- Lists: virtualized lists use `DataListRow`; plain lists use the restyled `Table`. Remove
  any `Card` around a list. Add a `DataListToolbar` only where a filter already exists or
  the list can get long (Files: yes; API keys, webhooks: count only).
- Status values (file scan state, key active/revoked/expired, webhook delivery
  success/failed/pending, invoice paid/open) use `StatusPill`. Map: success/active/paid →
  `active`; pending/scanning/open → `pending`; failed/revoked → `neutral` with the word
  saying what happened. Add a `danger` tone to `StatusPill` (dot `bg-destructive`) and use
  it for failed/infected; text is still always present.
- Machine values (API key prefixes, webhook signing secrets, IDs, URLs) use `font-mono`
  (the system mono stack from ticket 01). Nothing else uses mono.
- Row actions go in the "⋯" menu, destructive ones last after a separator, keeping
  existing confirm dialogs. File deletion keeps its confirmation (it schedules a hard delete).
- Empty states: keep each page's existing `EmptyState` content; just make sure it sits
  inside the page (no card).
- Keep every `data-testid`.

## Verify (after each page)

```bash
pnpm --filter web exec vitest run src/features/files
pnpm --filter web exec vitest run src/features/settings/ApiKeysPage.test.tsx
pnpm --filter web exec vitest run src/features/settings/WebhooksPage.test.tsx
pnpm --filter web exec vitest run src/features/settings/BillingSettingsPage.test.tsx
pnpm --filter web type-check && pnpm --filter web lint
```

Finally run `pnpm --filter web test`, then:

```bash
grep -rn '<h1' apps/web/src --include=*.tsx | grep -v test
```

The only remaining `<h1>`s should be in auth pages (ticket 11), admin pages (ticket 12),
the maintenance screen and the not-found page. List any others in the ticket's Notes and convert them.

## Acceptance

- [ ] Files, API keys, Webhooks, Invoices, Reports use `PageHeader` and the shared list look.
- [ ] `StatusPill` has a `danger` tone; statuses always carry a word.
- [ ] Machine values in mono; nothing else.
- [ ] No list wrapped in a card; test ids kept; web tests, lint, type-check pass.
