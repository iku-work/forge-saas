# 05: Collapsible sidebar (icon rail)

**What to build:** a member can collapse the sidebar into a narrow icon rail and expand
it again, with a button at the top-left of the panel or by pressing `[`. The choice is
remembered on this device. Rail items show their name in a tooltip and keep it for
screen readers.

**Blocked by:** 04.

**Status:** ready-for-agent

## Read first

1. `packages/ui/src/components/app-shell.tsx` (from ticket 04).
2. `apps/web/src/features/shortcuts/registry.ts` (whole file, short) and
   `apps/web/src/features/shortcuts/useGlobalShortcuts.ts`: how shortcuts are declared
   and when they must not fire (text fields).
3. `apps/web/src/lib/theme.ts`, lines 1–60: the pattern for reading a stored preference
   safely (try/catch around storage).
4. In `AppLayout.tsx`, search `AppShell` to find where ticket 04 mounted it.

## Design

- Expanded: as in ticket 04. Collapsed: sidebar width 4rem; rows become 2.5rem squares
  centred; labels, the shortcut hint, the Organization name, the chevron and the user's
  name/email are hidden; group labels become a 1.5rem-wide hairline divider; the footer
  stacks (user avatar above the bell). The panel grows to fill the space.
- Width change animates with `transition-[grid-template-columns] duration-[var(--duration-base)]`
  (reduced motion already makes it instant).
- Toggle button: icon-only (lucide `PanelLeft`), in the panel's top row before the
  breadcrumb, `aria-label` "Collapse sidebar" / "Expand sidebar", `aria-expanded`,
  and a tooltip showing the label plus `[`. Hidden when the sidebar is in drawer mode (phones).
- Tooltips: every rail item uses the existing `Tooltip` component (side `end`) with the
  item's label. The item keeps its accessible name (put the label in an `sr-only` span
  when collapsed, not `display:none` on the only text).

## Steps

1. **`@forge/ui`:** add a `collapsed?: boolean` prop to `AppShell`, `ShellSidebar` and
   `ShellNavItem` (or provide it through a small React context inside `app-shell.tsx`
   so items don't each need the prop; your choice, keep it simple). Implement the
   collapsed styles above. Update the story with a collapsed variant. Build `@forge/ui`.

2. **Persistence hook** in the web app, `apps/web/src/lib/useSidebarCollapsed.ts`:
   - Storage key `forge.sidebar` with values `"collapsed"` / `"expanded"`.
   - Read once on mount, inside try/catch (storage can throw); default expanded.
   - Return `[collapsed, setCollapsed, toggle]`; writing also goes through try/catch.
   - Per device only: no API call, no backend field.

3. **Shortcut:** add an entry to `registry.ts`, id `toggle-sidebar`, keys `["["]`,
   with a translated description key (e.g. `common.shortcuts.toggle_sidebar` =
   "Collapse or expand the sidebar"; check the key naming of existing entries and
   follow it). Wire its handler in `AppLayout` to `toggle`. The registry already
   powers the `?` cheat sheet, so the new shortcut appears there automatically.
   Confirm the shortcut hook ignores key presses inside inputs, textareas, selects
   and contenteditable (it should already; add the guard if not).

4. **AppLayout:** pass `collapsed` to `AppShell`; render the toggle button in the panel top row.

5. **Translations:** `common.nav.collapse_sidebar`, `common.nav.expand_sidebar`, the
   shortcut description. Rebuild `@forge/i18n`.

## Tests (Vitest, `apps/web/src/layouts/AppLayoutSidebarCollapse.test.tsx`)

Copy the render setup from `AppLayout.test.tsx`. Test behaviour only:
- clicking "Collapse sidebar" collapses (the button's label flips to "Expand sidebar",
  `aria-expanded` is false), and clicking again expands;
- pressing `[` toggles;
- pressing `[` while focus is in a text input does **not** toggle;
- after collapsing, unmounting and rendering again, it is still collapsed (storage);
- when collapsed, each nav link is still found by `getByRole("link", { name: "Files" })`.

Clear `localStorage` in `beforeEach`.

## Verify

```bash
pnpm --filter @forge/ui build
pnpm --filter web exec vitest run src/layouts/AppLayoutSidebarCollapse.test.tsx
pnpm --filter web exec vitest run src/features/shortcuts
pnpm --filter web type-check && pnpm --filter web lint && pnpm --filter web test
```

Manual: collapse, reload (stays collapsed), hover items (tooltips), keyboard Tab through
the rail (focus rings, tooltips on focus), `ar` locale (rail on the right), `?` shows the shortcut.

## Acceptance

- [ ] Rail collapses/expands via button and `[`; `[` ignored in text fields.
- [ ] State persists per device in localStorage, read safely.
- [ ] Rail items have tooltips and accessible names.
- [ ] Shortcut listed in the `?` cheat sheet.
- [ ] New test file passes; web tests, lint, type-check pass.
