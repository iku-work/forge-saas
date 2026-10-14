# Visual overhaul: progress

Branch `visual-overhaul` (worktree `.claude/worktrees/visual-overhaul`). Nothing pushed.
Update this file whenever a ticket or step changes state.
Deferred work (backend gaps, untranslated keys, checks still owed) lives in `FOLLOW-UPS.md`.

Legend: ✅ done · 🟡 partial · ⬜ not started

## Planning (done)

| What | Who | Commit |
|---|---|---|
| Design interview, prototype (branch `prototype/visual-overhaul`, never merge) | Claude + user | 1dcb320c |
| ADR-0008 (design language), `GLOSSARY.md` | Claude | 3765b848 |
| Spec, local issue tracker config (`docs/agents/`) | Claude | fa9f0340 |
| README + 14 tickets | Claude | 9a11fabe |
| "Agent skills" section in `agents.md` | Claude | ab709b98 |

## Tickets

| # | Ticket | Status | Notes |
|---|---|---|---|
| 01 | Tokens and base components | ✅ | Done 2026-10-05. Details below. |
| 02 | Self-hosted font | ✅ | Done 2026-10-05 (Claude). See notes below. |
| 03 | Glossary copy sweep | ✅ | Done 2026-10-05 (Claude). See notes. |
| 04 | App shell | ✅ | Done 2026-10-05 (Claude). Not yet looked at in a running app. See notes. |
| 05 | Collapsible sidebar | ✅ | Done 2026-10-05 (Claude). Not yet looked at in a running app. See notes. |
| 06 | Page header + settings rows (Profile) | ✅ | Done 2026-10-05 (Claude). Not yet looked at in a running app. See notes. |
| 07 | Other settings pages | ✅ | Done 2026-10-05 (Claude). Not yet looked at in a running app. See notes. |
| 08 | Data list + Members | ✅ | Done 2026-10-05 (Claude), with gaps logged in FOLLOW-UPS. Not yet looked at in a running app. |
| 09 | Other lists | ✅ | Done 2026-10-05 (Claude). Not yet looked at in a running app. |
| 10 | Getting started checklist | ✅ | Done 2026-10-05 (Claude). Not yet looked at in a running app. See notes. |
| 11 | Auth pages | ✅ | Done 2026-10-06 (Claude). Seen in a browser (login, register, `ar`, dark, 375px, short window). See notes. |
| 12 | Admin console | ✅ | Done 2026-10-09 (Claude). Not yet looked at in a running app (needs a superadmin login). See notes. |
| 13 | Marketing hero | ✅ | Done 2026-10-09 (Claude). Screenshotted at 1440 and 375px (Playwright). See notes. |
| 14 | Integrate and verify | ✅ | Done 2026-10-09 (Claude). Everything green locally, incl. the full Playwright suite in Docker. See notes. |

All tickets done. Nothing pushed; the branch is ready for review.

## Ticket 01, step by step

Steps 1–4 committed in b52cea71 (2026-10-04), steps 5–6 on 2026-10-05. Gemma made
uncommitted passes on steps 1–5; Claude reviewed, repaired and committed. **Gemma is no
longer used on this overhaul** (decided 2026-10-05).

| Step | Status | Who | What |
|---|---|---|---|
| 1. Colour values | ✅ | Gemma, checked by Claude | All light/dark values match the ticket. |
| 2. New tokens | ✅ | Gemma, fixed by Claude | Gemma added everything correctly but left 5 uses of the deleted shadow classes (skip link, theme toggle, sheet, 2 command stories); Claude replaced them. |
| 3. Type scale and root size | ✅ | Gemma, repaired by Claude | Gemma's edit broke `tokens.css` (a declaration written over a comment, which broke the marketing build; duplicated `--text-title`; `body` rule overwritten). Claude restored and cleaned it. |
| 4. Icon stroke | ✅ | Gemma, moved by Claude | Rule moved into the base layer so utilities can still override it. |
| 5. Restyle base components | ✅ | Gemma (one item per prompt), most redone by Claude | Of Gemma's 9 component edits: card right; tabs, command, popover, dialog partly right; badge, tooltip, sheet wrong; **checkbox broken** (no visible outline, grey check fill, grey focus ring). All redone to spec. Claude also did dropdown items and submenu, links, toasts, the dialog close button (now RTL-safe), and the hand-styled `<select>`s. Command items keep `min-h-11` (F-014 touch targets). |
| 6. Remove hardcoded palette classes | ✅ | Claude | Zero palette classes left. Added `--success` and `--warning-foreground`. Admin layout keeps its dark look via a `dark` class on its root until ticket 12. No Tailwind built-in shadows left either. |

Also fixed in 01: light `--muted-foreground` darkened to `#646b78` (the ticket's `#6b7280` was 4.32:1 on the sunken surface). Every text/token pairing now measures ≥ 4.5:1 in both themes; `--success` is for icons and fills only (3.45:1).

Checks at the 01 completion commit: `@forge/ui` and web type-check clean; web lint (one existing warning in `VirtualList`); web tests 373/373;
marketing build passes, marketing tests 236 passed / 2 skipped; Storybook builds; prettier hook passes.
Visual screenshot suite is expected to fail until ticket 14.

## Ticket 02 notes

- 7 woff2 files + 2 OFL licences in `packages/ui/fonts/`; `packages/ui/styles/fonts.css`
  declares them with Google's own unicode-ranges. Upright Latin is 57 KB (the ADR said ~25 KB;
  corrected), the largest file 62 KB, under the new 80 KB per-file budget.
- Marketing preloads exactly one file (upright Latin); the web app preloads none.
- Verified in a browser: `/en/` downloads only the Latin file; `/ar/` adds the three Plex Arabic files.
  The footer's "العربية" had been pulling the Arabic file onto every page; Arabic snippets on
  non-Arabic pages now use the system face (rule in `fonts.css`, noted in ADR-0008).
- `check-budget.mjs` gained the 80 KB per-font check; `budget.test.ts` now asserts one family and one
  preload per page (it used to assert *no* webfont) and has a failing case for the size check.
- Lighthouse could not run locally (Windows chrome-launcher EPERM, known); left to CI.
- Windows-only flake found: back-to-back Astro builds in the marketing suite intermittently fail
  renaming `.astro/content-assets.mjs` (EPERM). It appeared with the webfonts; a control run without
  them did not hit it. `tests/helpers.ts` retries a build (max 3) only on that exact error on Windows.

## Ticket 14 notes

- **Static and unit:** `pnpm lint`, `pnpm type-check` (18/18) and `pnpm test --concurrency=2`
  (13/13: web 392, marketing 237, mobile 113, i18n 53, email 53, utils 11) green; Storybook builds
  and its 77 story tests (axe errors fail them) pass.
- **Translations were a real CI failure.** Tickets 04–13 left 84 English-only keys, which put
  ar/de/es/fr over check-locales' 5% gate. All 84 are now translated in the four locales
  (Sie / tú / vous, existing terms reused) and every one is listed in that locale's
  `__review.json` (the repo's "machine first pass, human review pending" marker; es got one).
- **Visual baselines:** all 16 regenerated in `mcr.microsoft.com/playwright:v1.61.1-noble` against
  the compose stack, looked at, and passing without the update flag (12/12 over three repeats,
  no retries). Recipe as in the memory note, with one change: `--network
  container:visual-overhaul-caddy-1`, `E2E_BASE_URL=https://localhost` and
  `E2E_MAILHOG_URL=http://mailhog:8025` make the email-verifying specs runnable too (the
  verify link says `https://localhost`, which is Caddy inside its namespace).
- **Full Playwright suite in Docker:** 26 passed, 2 skipped (prod-smoke needs a deployed target).
  That includes a11y (light, `ar`, dark), touch targets, mobile layout, acceptance, journey,
  modules and visual, on chromium and webkit.
- **Defects found and fixed on the way:**
  - The palette hint and dashboard note read "CtrlK" off Apple. New `renderChord()` in the
    shortcuts registry: "Ctrl+K" / "⌘K" (test added). Pre-existing (F-005), but the overhaul
    put it on every screen.
  - Touch targets: "Forgot password?", Terms and Privacy (ticket 11) were 18–22px tall; now
    `min-h-6`.
  - The theme toggle overflowed a 320px screen by 1px in webkit; segments use `px-2` below `sm`.
  - Visual spec shot the dashboard before the checklist loaded (skeleton); it now waits for
    `getting-started`.
  - a11y spec scanned the command palette mid open-animation (dark theme, chromium, every run);
    it now waits for finite animations to settle (same fix T-221 made for the bell panel).
  - The worker could not render email: `packages/email-templates/dist` was never built in the
    worktree. Built it (environment, not code).
  - `mobile` type-check failed only because the earlier `pnpm add --filter marketing` had left
    mobile's `.bin` unlinked; `pnpm install --offline --frozen-lockfile` relinked it.
- **Diff review** (`origin/main...HEAD`, 177 files): no hex colours added in TSX, no
  `console.log`, no Card left on converted pages.
- ADR-0008 Consequences gained two "found during the build" bullets (admin console not forced
  dark; muted text `#646b78`).
- Not run here: Lighthouse (Windows EPERM, CI does it); the CI module-matrix legs (the
  per-configuration test files cover the new code; main's `b-minimal` leg is already red).

## Ticket 13 notes

- New `apps/marketing/src/components/HeroProduct.tsx`: the real `AppShell` / `ShellSidebar` /
  `ShellPanel` / `PageHeader` / `Checklist` with sample content (Acme; Dashboard active;
  1 of 4 done), every string from `marketing.hero_product.*`. Rendered with no `client:*`
  directive, so it is static HTML: the landing page still has no executable script (only
  the two JSON-LD blocks it already had). Because it is never hydrated it takes `t` directly.
  Sample hints and the description are copied from the app's own dashboard copy.
- `lucide-react` added to `apps/marketing` (same `^1.21.0` @forge/ui already uses; the
  lockfile gained three lines and no new package) because pnpm will not let marketing import
  @forge/ui's dependency.
- Landing: condensed headline, subtitle, one "Get started" button and a "Read the guides" text
  link (new key `hero.cta_guides`; `hero.cta_secondary` "Sign in" removed everywhere since
  the header already has it). The frame is `aria-hidden` + `inert`; inside, the shell at
  `zoom: .8` (phones `.72`), so at 1440px the shell is ~1296 CSS px wide and lays out with
  its sidebar; at 375px it is narrower than the sidebar breakpoint and shows the panel only,
  with no extra CSS. `rise` and `settle` animations play once on load; reduced motion turns
  them off (checked in Playwright: `animation-name: none`).
- Facts: three plain columns with a strong hairline above each `h2`; the old
  "What you start with" heading is now the section's `aria-label` (the build test still
  finds the string). Billing's column still follows the module.
- The header gets a `wide` prop (via `BaseLayout`) so it lines up with the 75rem landing
  column; every other page keeps `max-w-5xl`. The brand-colour rule above the old headline is gone.
- Other pages (features, pricing, guides, blog, terms) looked fine with the new tokens and font
  at 1280px, no horizontal overflow at 375px; nothing changed there.
- Marketing has no dark mode (nothing sets `.dark`), so dark was not a thing to check.
- Checks: marketing build (budget, SEO, hreflang, indexing gates) and 237 tests pass,
  `astro check` clean. Lighthouse not run (Windows EPERM; CI).

## Ticket 12 notes

- `AdminLayout` now uses `AppShell` / `ShellSidebar` / `ShellPanel`. Sidebar: monogram +
  "Admin console", the **Admin marker** (`data-testid="admin-marker"`: shield icon + "Admin",
  `bg-warning-soft text-warning`, rounded-full), the five admin links, and at the bottom
  "Back to app" (`admin-back-to-app`, reusing `common.nav.back_to_app`), the email and
  "Log out". On narrow screens the same sidebar opens as a drawer from the panel's top row.
  Not done here: the collapsible rail, ⌘K and shortcuts (optional per the ticket; the
  admin console has no palette today either).
- **The forced `dark` class is gone.** T-143 made the admin surface dark to tell it apart;
  the marker now does that job and the console follows the user's theme like the app.
- `common.admin.back_to_app` removed from every locale; `common.admin.console` added (en).
- Pages: all seven on `Page` + `PageHeader`, no cards.
  - Dashboard: the three numbers as a `<dl>` row of figures under a hairline; recent sign-ups
    and payment failures as `SettingsSection`s of `DataListRow`s.
  - Users / Organizations / Audit: filters above a bare `Table`; status as `StatusPill`
    (users active/inactive; subscription status by tone: active, trialing = pending,
    past_due/unpaid/incomplete_expired = danger, else neutral; audit severity elevated =
    danger). Plan slugs and audit actions in mono.
  - User detail: back link with an arrow, `PageHeader` (email; superadmin pill, name,
    created date as the description; Mobile handoff and Impersonate as actions), then
    memberships, sessions and recent actions as sections.
  - Organization detail: tabs kept; key/value pairs are `SettingsRow`s inside an
    `@container/section`; audit rows are `DataListRow`s; flag states are pills
    (on = active, off = neutral, override = pending), flag names in mono.
  - Product settings: two `SettingsSection`s. The typed switches are one `SettingsRow`
    each (label + documented hint on the left, control on the right; banner options appear
    as rows when the banner is on) with Save in the footer; the raw JSON editor keeps its
    "Advanced" badge in the title and Save in the footer. All ids, labels and test ids kept.
- Tests: new `layouts/AdminLayout.test.tsx` (marker + Back to app on `/admin` and
  `/admin/users`). 390 web tests pass.

## Ticket 11 notes

- `AuthCard` is now `AuthLayout` (`features/auth/AuthLayout.tsx`; renamed, every import
  updated, no alias). Same props. Brand row (monogram + `common.app.name`), condensed title,
  description, content, the switch-flow link, and Terms/Privacy at the page bottom when the
  marketing module gives a URL (new keys `auth.legal.terms/privacy`). Margin-based centring kept.
- Inputs inside it are `h-10` through one descendant class on the layout (no Input size prop
  added); full-width buttons on the auth pages got `size="lg"` (h-10).
- **Login:** "Forgot password?" sits at the end of the password label row; the magic link moved
  from the footer into the alternatives as a ghost button under "or", after any OAuth buttons.
  Since the magic link is always there, "or" now always shows (except under an SSO hint), so
  `LoginPageOAuthUnconfigured` asserts the opposite of before.
- Status paragraphs on the other pages lost `text-center` (the layout is start-aligned).
- The create-first-organization screen (`CreateFirstOrgPrompt` in `AppLayout.tsx`) uses
  `AuthLayout` too, which closes that FOLLOW-UP.
- Tests: Terms/Privacy links asserted on login and absent with marketing off. 388 web tests pass.
- Looked at in the browser pane without a backend: login (light, desktop), register and
  `ar` login (dark, 375px, no horizontal scroll), a 375×420 window (heading reachable).
  Not seen: Terms/Privacy (no config without the backend), the other auth routes.

## Ticket 10 notes

- New in `@forge/ui`: `Checklist` (title, progress text, a `role="progressbar"` bar, `<ol>`)
  and `ChecklistStep` (tick, title, hint, "Done" or the action). Story with three states.
- The dashboard (`$locale._app.index.tsx`) shows "Welcome to {org}" and the checklist, a
  skeleton while loading, and only "Your organization is set up." once every step is done.
  The hook lives in `features/dashboard/useGettingStarted.ts` (its own file for react-refresh),
  the component in `GettingStarted.tsx`.
- "Done" comes from the queries the real pages already run, with the same query keys, so a step
  ticks as soon as its page changes the cache. No backend change, no dismissed state.
  Profile: a name, plus a photo when file storage is on. Files: one file. Invite: a second
  member *or* a pending invitation. Plan: subscription `active` (a trial is not done).
  Invite needs `members:manage`, plan needs billing on and `billing:manage`.
- The first open step's button is primary, the rest outline.
- **Tests:** four files, not two. `@forge/hooks` caches the signed-in user and their role per
  module as well as the config, so each user/role/config is its own file: `DashboardChecklist`
  (new owner), `...Member`, `...AllDone`, `...NoFiles`. Eight shell tests that waited for a
  "Dashboard" heading now wait for "Welcome to Acme Inc". 387 web tests pass.
- Old `dashboard.empty*` keys removed from all six locales; the new keys are English-only
  elsewhere for now (FOLLOW-UPS, translations).

## Ticket 09 notes

- Files, API keys, Webhooks, the Billing invoices list and Reports use `Page` + `PageHeader`;
  no cards left on them. Statuses are `StatusPill`s (files: ready / scanning / infected-failed;
  keys: active vs revoked-expired; webhooks: enabled / auto-disabled / disabled; deliveries:
  delivered / pending / failed-dead; invoices: Stripe's five statuses, now translated).
- **Row actions:** the destructive or rarer actions moved into a "⋯" menu (Files: Delete;
  API keys: Revoke; Webhooks: Enable/Disable, Rotate secret, separator, Delete). Kept visible on
  purpose: Files' Download (its disabled state explains itself), Webhooks' Deliveries (opens the
  log in place), the delivery log's View and Replay (frequent, non-destructive). All existing
  confirm dialogs unchanged; menu items keep the old test ids.
- Machine values in mono: key prefixes, scopes, webhook URLs, event names, response codes.
- Files keeps its dropzone under the header (it is the drop target and the empty state refers
  to it) rather than an "Upload" header button.
- Tests: four files updated to open the row menu first; the `@forge/hooks` mocks gained
  `useDirection`. Billing's test now expects "Paid" (translated). 383 web tests pass.
- Remaining hand-written `<h1>`s: admin pages (12), AuthCard (11), dashboard (10), maintenance
  screen (stays), and the create-first-organization screen (in FOLLOW-UPS).

## Ticket 08 notes

- New in `@forge/ui`: `DataListColumns` (aria-hidden label row), `DataListRow` (grid row with
  hover wash), `DataListMuted`, `RowActionsButton` (the "⋯"), `StatusPill` (active / pending /
  neutral / danger; dot *and* word). `Table` restyled the same way for non-virtual lists.
  Stories for DataList and StatusPill.
- **Column layout pattern:** a page defines one literal class, e.g.
  `grid-cols-[minmax(0,1fr)_6.5rem_2rem] @xl/list:grid-cols-[...]`, and passes it to the columns
  row and every row; wrap the list in `@container/list` and mark wide-only cells
  `hidden @xl/list:block`. (Literal, because Tailwind cannot see a class built at runtime.)
- Members: Member / Role / Status / Joined / ⋯. The ⋯ menu has "Change role" (submenu, radio
  items with the current role checked) and, after a separator, "Remove from organization" in red.
  No menu on your own row or for members without `members:manage`. Virtualization, `member-row`
  test ids and the a11y test unchanged.
- **Data gap:** the members endpoint returns ids only, so other members show as a short id (you
  see your own name). Search, count, invitation actions and names are logged in FOLLOW-UPS.md.
- Pending invitations stay a second section (their own paginated query) in the same list style;
  "Expired" is a danger StatusPill.
- Tests: three new (menu contents and order, remove sends DELETE, no menu on own row / for plain
  members). The `@forge/hooks` mock in that file needed `useDirection` for the dropdown.
  383 web tests pass.

## Ticket 07 notes

- Notifications, Organization, Security and Billing now use `Page` + `PageHeader` + sections; no
  cards except Billing's invoices list (ticket 09).
- **Notifications:** the type x channel matrix stays a table inside one section; Save in the
  footer is **always enabled** on purpose -- saving persists the shown defaults and heals a stored
  row that switched off a required channel (two existing tests depend on that).
- **Organization:** one "General" section, logo row (still the disabled "coming soon" upload)
  and name row; Save only once the name changes; members without permission see the notice.
- **Security:** six sections with titles. Password and email are rows with Save in the footer
  (disabled until changed); sessions, two-factor, security keys and linked accounts keep their
  inner layout (lists and a multi-step flow; ticket 09 restyles lists). The password test now
  scopes to the "Password" region instead of the <form>, since Save is outside the form.
- **Billing:** current plan as a row with "Manage billing" as its control; usage table in a
  section; one row per available plan with price as the hint and Upgrade as the control.
- New en keys: section titles and descriptions (`notifications.preferences.section_title`,
  `tenancy.org_settings.section_general/logo_label/name_hint`, `auth.security_keys.title`,
  `auth.change_password.title`, `auth.change_email.title`, `auth.security_page.description`,
  `billing.description`, `billing.current_plan.plan_label`).
- 380 web tests pass; lint and type-check clean.

## Ticket 06 notes

- New in `@forge/ui`: `Page` (the content column: left aligned, max ~960px, gutters by shell
  width), `PageHeader` (title in `.type-title`, one-line description, actions; keeps
  `data-testid="page-title"`), `SettingsSection` (a `<section>` labelled by its h2, optional
  footer) and `SettingsRow` (two columns at `@2xl/section`, one below). Stories for both.
- **Pattern for the other pages:** every section carries `mt-12`; margins collapse through
  plain wrappers, so nesting is fine (changed in ticket 07 -- it was `[&+&]:mt-12`, which broke
  under the Security page's test-id wrappers). Put each section's `<form id>` *inside* the section and point the
  footer's Save at it with `form={id}` (see ProfileSettingsPage). A react-hook-form field goes
  `<FormItem><SettingsRow label={<FormLabel/>}>…<FormControl/>…</SettingsRow></FormItem>` so
  the label stays tied to the input.
- `FileUploader` has `variant="button"` + `buttonLabel`: one outline button, drag-and-drop
  kept on its wrapper. The dropzone stays the default.
- Profile: Personal details (photo, name, email + "Change email" link to Security) saves on its
  own; Save is enabled only once the name changes. Preferences (language, appearance) apply
  immediately. ThemeToggle restyled as a sunken track with a raised selected option.
- **Deviations:** no "Remove photo" button -- the API cannot clear an avatar (no endpoint or
  null path), so there would be nothing to call. Save stays "Save" (not "Save changes") to
  keep its existing translations.
- Tests: Profile page test stubs the router `Link`/`useParams` (it renders the page without a
  router, as it already did for the locale switcher) and checks "Change email" points at
  Security. 380 web tests pass.

## Ticket 05 notes

- `AppShell` takes `collapsed`; it sets `data-collapsed` on the root, which is also the
  Tailwind group `shell`. Every shell piece styles itself with `group-data-[collapsed]/shell:`
  (written out in full -- Tailwind cannot see classes assembled from a prefix). The phone drawer
  is portaled outside the group, so it never collapses.
- New `ShellLabel` (text that becomes sr-only in the rail) and `ShellExpandedOnly`. Group
  labels turn into a hairline; the footer stacks.
- `useSidebarCollapsed` (apps/web/src/lib): localStorage key `forge.sidebar`, read in the
  state initialiser (no flash), every access in try/catch. Per device, no backend.
- `[` is a registry shortcut in the "view" category (so the `?` sheet lists it); the hook skips
  it inside text fields like the other bare keys. Toggle button in the panel's top row, hidden
  where the drawer is used, with `aria-expanded` and `aria-keyshortcuts`.
- Rail tooltips via a `RailTooltip` helper in AppLayout (Radix sides are physical, so the side
  flips under rtl); the app now has a `TooltipProvider` around the shell.
- Tests: `AppLayoutSidebarCollapse.test.tsx` (button, `[`, ignored in a text field, persisted,
  names kept when collapsed, listed in the cheat sheet). 379 web tests pass.
- **Not checked in a running app.** Look at: collapse/expand animation, tooltips on hover and on
  Tab, rail in `ar` (should be on the right with tooltips to its left), dark mode.

## Ticket 03 notes

- 11 keys reworded in all six locales (with each locale's existing word for Organization and
  Member): the dashboard welcome ("workspace"), the member-invited email (title, subject, button),
  the trial-ending email ("your team's access" → "so your members keep their access"), the
  marketing hero subtitle, the accounts highlight, the seats label, and three "teammate" keys
  (dashboard invite action, invite dialog title, create-organization description), which the
  ticket's `team` search had missed.
- Left alone on purpose: the blog author "The ForgeSaaS team" (the company), "a team writes late"
  (a development team), "pnpm workspace" (the tool), developer comments, and `teams_*` key names.
- Five tests asserted the old copy and were updated. The backend reads the same locale files; the
  Docker image bakes a copy at build time, so emails change on the next image build.

## Ticket 04 notes

- Shell components in `@forge/ui` (`app-shell.tsx`: AppShell, ShellSidebar, ShellNavGroup,
  ShellNavItem, ShellSearchButton, ShellPanel), no data, container queries (`@3xl/shell`, i.e.
  48rem of shell width). Story: Components/AppShell.
- `AppLayout`: header removed; search, support link, user menu and bell in the sidebar; breadcrumb
  and the phone drawer trigger in the panel's top row. `SidebarNav` renders the whole sidebar, used
  both for the column and inside the drawer.
- Top row: the Organization switcher (with a monogram) when multi-org is on, otherwise the product
  wordmark. The old "wordmark links home" test became "Back to app links home" in settings.
- Settings groups come from a new `group` field on `SETTINGS_NAV` items. `ShellNavGroup` is a
  `role="group"` labelled by its heading, so screen readers announce "Your account, group".
- Active nav styling is driven by the router's own `aria-current="page"`.
- Bell and user menu open upwards (`side="top" align="start"`); unread badge is Iris.
- Checks: web type-check/lint, 374 web tests, `@forge/i18n` lint, Storybook build; every new class
  verified present in the built CSS. **Not checked in a running app** (needs the Docker backend):
  look at it in `pnpm --filter web dev`, including below ~860px wide (drawer), `ar` and dark mode.
- `NotificationFeedVirtual`'s arrow-key test failed once in a full run and passed in isolation 3/3
  and in the next full run; it is load-sensitive (seen once in ticket 02 too).

## Lessons for whoever continues

- Pre-commit hooks are **not installed** in this worktree. Run
  `C:\Users\IK\Documents\saas-template\backend\.venv\Scripts\pre-commit.exe run --files <changed files>`
  before each commit. The hook's prettier is v4 alpha; `pnpm exec prettier` (3.x) formats differently.
- Web tests regenerate `apps/web/src/routeTree.gen.ts`. Revert it (`git checkout -- apps/web/src/routeTree.gen.ts`), don't commit it.
- A fresh worktree needs its packages built first: `pnpm exec turbo run build --filter="web^..."`.
- Always run `pnpm --filter marketing build` after touching `tokens.css`: it is the only check that parses the CSS (web tests don't).
- Gemma handled "put these exact values here" well (steps 1–2) and broke things when editing around existing code. It also swapped in similar-sounding Tailwind classes (`shadow-xl` for `shadow-menu`) and made unrequested changes. See `LOCAL-MODEL.md`.
- The shadcn token named `accent` is a grey hover surface, **not** the brand colour (that is `primary`). The README now says so.
