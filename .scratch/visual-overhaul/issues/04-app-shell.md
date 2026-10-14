# 04: New app shell: sidebar and inset panel, no top header

**What to build:** the signed-in app has no top header any more. The sidebar (same
colour as the page, no border) holds the Organization switcher, a Search button with
its shortcut, the navigation, and at the bottom the notification bell and the user
menu. Page content sits in a raised white panel with rounded corners. The breadcrumb
sits in the panel's top row. Settings shows its own navigation with "Back to app".
Phones keep the drawer.

**Blocked by:** 01.

**Status:** done

## Read first

1. `apps/web/src/layouts/AppLayout.tsx`, **in pieces**:
   - search for `function SidebarNav` and read that function;
   - search for `data-testid="app-shell"` and read from there to the end of the file
     (~150 lines: the aside, the header with the Sheet drawer, palette trigger, bell, UserMenu, main).
   - search for `function UserMenu` and `function OrgSwitcher` and skim them.
2. `apps/web/src/lib/navigation.ts`: `PRIMARY_NAV`, `SETTINGS_NAV`, `useVisibleNavItems()`.
3. `apps/web/src/layouts/AppLayout.test.tsx` (to see what tests rely on).
4. The prototype's `.sidebar`, `.side-row`, `.main`, `.panel`, `.panel-top` CSS.

## Design (from the prototype; values are at the 112.5% root)

```
┌─────────────┬──────────────────────────────────────┐
│ ◆ Acme   ⌄  │ ╭──────────────────────────────────╮ │
│ ⌕ Search ⌘K │ │ Settings / Profile   (breadcrumb)│ │
│             │ │                                  │ │
│ Dashboard   │ │  page content (PageHeader etc.)  │ │
│ Files       │ │                                  │ │
│ Settings    │ │                                  │ │
│             │ │                                  │ │
│ (TU) Test 🔔│ ╰──────────────────────────────────╯ │
└─────────────┴──────────────────────────────────────┘
```

- Sidebar: width 15.5rem, padding 0.75rem 0.625rem, `bg-background`, **no border**,
  sticky full height. Rows: height 2rem, radius `rounded-md`, icon 1rem, label
  `text-sm font-medium text-foreground-secondary`; hover `bg-foreground/5 text-foreground`;
  active (`aria-current="page"`): `bg-primary-soft text-foreground` with the icon in `text-primary`.
- Organization switcher: first row, height 2.5rem, a 1.375rem square monogram in
  `bg-primary text-primary-foreground rounded-sm`, the Organization name `font-semibold`,
  a chevrons-up-down icon at the end.
- Search button: a sidebar row with the search icon, label, and the shortcut hint
  (`Ctrl K` / `⌘K`, whatever the current palette trigger shows) in a small bordered
  `kbd` at the end, **same font as the UI, not monospace**. It opens the existing command palette.
- Group labels (settings only): `text-xs font-medium text-muted-foreground`, sentence case, not uppercase.
- Footer (bottom of sidebar): the user row (avatar, name, email in `text-xs text-muted-foreground`,
  truncated) opening the existing user menu **upwards** (`side="top"`), then the bell as
  an icon button. Unread count is a number badge in `bg-primary text-primary-foreground`.
- Main area: padding `0.5rem 0.5rem 0.5rem 0` (logical: `pe-2 py-2`); the panel is
  `bg-card rounded-xl shadow-panel min-h-[calc(100dvh-1rem)]`.
- Panel top row: height 3.25rem, padding-inline 1.75rem, `text-xs text-muted-foreground`,
  contains the breadcrumb (today's page title logic, `pageTitleFor`, can produce it)
  and, on phones, the drawer menu button.

## Steps

1. **Create data-free shell components in `@forge/ui`**, file `packages/ui/src/components/app-shell.tsx`.
   Export small presentational pieces; they take everything as props/children and
   never fetch or translate:
   - `AppShell({ sidebar, children, className })`: the two-column grid. Use **container
     queries**, not viewport breakpoints: the root gets `@container/shell`, and the
     two-column layout applies at `@3xl/shell:` (≥48rem container width). Below that,
     the sidebar is hidden and the panel takes the whole width. Tailwind v4 has container
     queries built in (`@container`, `@md:` variants); no plugin needed.
   - `ShellSidebar({ header, footer, children })`: the column with header slot (switcher +
     search), nav area (children, scrollable), footer slot pinned to the bottom.
   - `ShellNavGroup({ label?, children })`, `ShellNavItem({ icon, label, active, asChild, ... })`:
     a row; when `asChild` is true, render the child (a router `Link`) via Radix `Slot`
     (already used by `button.tsx`), so the app keeps its own links.
   - `ShellPanel({ top, children })`: the raised panel with the top row.
   - `ShellSearchButton({ label, shortcut, onClick })`.
   Add `app-shell.stories.tsx` showing an expanded shell with sample nav, in both themes.
   Export everything from `packages/ui/src/index.ts`. Build `@forge/ui`.

2. **Rewire `AppLayout`** to use them:
   - Delete the `<header>` element. Move into the sidebar: the palette trigger (keep
     `data-testid="palette-trigger"`), `NotificationBell` and `UserMenu` (keep their test ids).
   - The phone drawer (`Sheet`, `data-testid="nav-drawer-trigger"`) stays, but its trigger
     moves into the panel's top row and only shows when the sidebar is hidden.
     Since the sidebar now hides by **container** width, show the trigger with the same
     container query (`@3xl/shell:hidden`). Inside the drawer render the same sidebar
     content as the desktop sidebar (today's code already shares `SidebarNav`; keep that).
   - Keep `data-testid="app-shell"`, the `SkipLink`, `GlobalBanner`, `main` element, the
     `CommandPalette`, `ShortcutsDialog` and `Toaster` exactly as they are.
   - Settings mode (`inSettings`): first item "Back to app" (arrow-left icon) linking to
     the dashboard; then a group labelled with a new key `common.nav.group_account`
     ("Your account") for profile/security/notifications, and a group labelled with the
     current Organization's name for the rest. Look at how `SETTINGS_NAV` items are
     defined and split them by item, not by hardcoding routes in JSX: add a `group`
     field (`"account" | "organization"`) to `NavItem` in `navigation.ts` and set it on each settings item.
   - Breadcrumb: in the panel's top row, show "Settings / <Page>" in settings,
     otherwise the page name. Reuse `pageTitleFor`.

3. **Notification popover and user menu** open from the bottom-left now: set the popover
   content `side="top" align="start"` (bell) and the dropdown `side="top" align="start"` (user menu).

4. **Translations:** new keys (`common.nav.back_to_app`, `common.nav.group_account`)
   in `packages/i18n/locales/en/common.json`; rebuild `@forge/i18n`.

5. **Tests:**
   - Run `AppLayout*.test.tsx` (all files matching `src/layouts/AppLayout*.test.tsx`).
     They check nav gating by module and permission; they should still pass. If one
     looks for a `banner`/`header` role, change it to look in the sidebar.
   - Add to `AppLayout.test.tsx`: in settings, a "Back to app" link exists and points to
     the dashboard; settings items appear under the two group labels.
   - E2E (can't run without the Docker stack; just update): in
     `apps/web/e2e/mobile-layout.spec.ts` and `touch-targets.spec.ts`, fix any selector
     that relied on the removed header. Test ids above are unchanged, so most need nothing.

## Verify

```bash
pnpm --filter @forge/ui type-check && pnpm --filter @forge/ui build
pnpm --filter web type-check && pnpm --filter web lint
pnpm --filter web exec vitest run src/layouts
pnpm --filter web test
```

Then `pnpm --filter web dev`: check desktop (sidebar + panel), narrow the window below
~800px (sidebar hides, menu button appears in the panel top row, drawer opens), the
`ar` locale (sidebar on the right), dark mode, and that the bell and user menu open upwards.

## Acceptance

- [x] No top header; search, bell and user menu are in the sidebar.
- [x] Content is in the raised panel; breadcrumb in the panel's top row.
- [x] Shell components live in `@forge/ui`, take no data, use container queries; story added.
- [x] Settings mode has "Back to app" and two sentence-case groups.
- [x] Phone drawer still works; RTL mirrors.
- [x] All test ids preserved; web tests, lint, type-check pass.

## Don't

- Don't implement collapsing (ticket 05).
- Don't change page contents (tickets 06+).
- Don't call `t()` inside `@forge/ui`; pass translated strings in as props.

## Notes

Completed 2026-10-05; see `PROGRESS.md`. Not yet viewed in a running app (needs the Docker backend).
The e2e specs needed no selector changes: no spec relied on the header, and every test id is preserved
(`sidebar-wordmark` now only exists when multi-org is off).
