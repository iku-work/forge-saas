# 12: Admin console in the shared shell, with an Admin marker

**What to build:** the superadmin console uses the same sidebar-and-panel shell as the
app, its pages use the shared page header and list look, and a persistent "Admin"
marker in the sidebar makes it impossible to mistake admin mode for the ordinary app.

**Blocked by:** 04, 06, 08.

**Status:** done

## Read first

1. `apps/web/src/layouts/AdminLayout.tsx` (162 lines, whole file).
2. `packages/ui/src/components/app-shell.tsx`.
3. One admin page as the first conversion: `apps/web/src/features/admin/AdminUsersPage.tsx`.
4. `apps/web/src/routes/-admin.test.tsx` and `apps/web/src/features/admin/AdminUsersPage.test.tsx` (skim).

## Design

- Same `AppShell`, `ShellSidebar`, `ShellPanel` as the app (ticket 04). Collapsing (ticket 05)
  is optional here; if you reuse the hook, it shares the same per-device setting.
- Sidebar header: instead of the Organization switcher, the product monogram + "Admin
  console" and directly under it the **Admin marker**: a pill `rounded-full px-2 py-0.5 text-xs font-semibold`
  in the warning tokens from ticket 01 (`bg-warning-soft text-warning`), text "Admin",
  with a shield icon. Not the accent colour, not colour-only (it has a word).
- Sidebar items: the existing admin navigation, then at the bottom "Back to app".
- The impersonation banner (`ImpersonationBanner`) keeps working and stays visible above everything.

## Steps

1. Rewrite `AdminLayout` with the shell components and the marker. Keep its routes,
   guards and test ids. Add `data-testid="admin-marker"` to the marker.
2. Convert admin pages one at a time to `PageHeader` + `Table`/`DataListRow` + `StatusPill`:
   `AdminDashboardPage`, `AdminUsersPage`, `AdminUserDetailPage`, `AdminOrgsPage`,
   `AdminOrgDetailPage`, `AdminAuditPage`, `AdminProductSettingsPage`. Detail pages and the
   product settings page use `SettingsSection`/`SettingsRow` for their key/value and form parts.
   Modals (`ImpersonateModal`, `PlanOverrideModal`, …) only need the restyle they got from ticket 01.
3. Translations: "Admin" marker label, "Admin console", "Back to app" (reuse the app's key). Rebuild i18n.

## Tests

- Existing admin tests pass (`-admin.test.tsx`, `AdminUsersPage.test.tsx`,
  `AdminProductSettingsPage.test.tsx`, impersonation flows).
- Add to `-admin.test.tsx` (or a new `AdminLayout.test.tsx`): the marker (`admin-marker`)
  is visible on the admin dashboard and on one other admin route, and has the text "Admin".

## Verify

```bash
pnpm --filter web exec vitest run src/features/admin src/routes
pnpm --filter web type-check && pnpm --filter web lint && pnpm --filter web test
```

## Acceptance

- [x] Admin console uses the shared shell; "Back to app" present.
- [x] Persistent Admin marker (word + icon, warning colours) on every admin page; test added.
- [x] All admin pages use the page header and the shared list/settings components.
- [x] Impersonation banner unaffected; tests, lint, type-check pass.

## Notes

Completed 2026-10-09; see `PROGRESS.md`. The admin console no longer forces dark mode (the
marker distinguishes it now). No collapsible rail in admin. Not yet seen in a browser.
