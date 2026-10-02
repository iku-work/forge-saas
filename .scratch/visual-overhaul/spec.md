# Spec: Visual overhaul of the web app and marketing site

Status: ready-for-agent

Decisions recorded in: ADR-0008 (`docs/adr/0008-design-language-v2.md`), `GLOSSARY.md`.
Prototype (primary source, not for main): branch `prototype/visual-overhaul`, file `apps/web/prototype/visual-overhaul.html`. Open it and use the bottom bar: screen, accent, text size, theme; `[` collapses the sidebar.

## Problem Statement

The product works, but it looks like a stock component kit rather than a product someone paid for. Every region has a border (sidebar, header, cards), settings are one bordered card in an 800px column with half the screen empty, card corners disagree with the controls inside them, labels are set in all caps, the user menu's items render at a different size from the rest of the menu, and the disabled "Save" button is a washed-out blue that reads as broken. Type is whatever the visitor's OS supplies, so the product looks different on every machine.

For a product built on this template there is a second problem: the brand is spread through the whole palette. The navy text, the blue-tinted borders and shadows are all "brand", so making the template look like a different product means retuning about twenty colours and re-measuring every one for contrast.

Visitors to the marketing site meet a third version of all this: a page that shares tokens with the app but none of its look, and a hero that describes the product instead of showing it.

## Solution

One quiet, premium design language shared by the app and the marketing site: Stripe's structure (light-first, generous space, few borders) with Linear's chrome (a recessive sidebar, everything one keystroke away).

- Near-colourless surfaces and text; the brand lives in **one accent** (Iris) used only for the primary action, focus, links and the active place in the navigation. A product built on the template rebrands by changing that one value per theme.
- One typeface, Instrument Sans, self-hosted, with page titles set slightly condensed as the single bold typographic move. Text is a little larger than today (16px body).
- No top header. The sidebar carries the Organization switcher, search, navigation, notifications and the user menu; content sits in a raised inset panel. The sidebar collapses to an icon rail.
- Settings become rows (what it is and why on the left, the control on the right), grouped into sections that each save on their own. Lists become full-width, hairline-separated rows with actions in a trailing menu.
- The dashboard greets a new Organization with a Getting started checklist built from what has actually been done.
- Sign-in and the other auth pages drop the card: a narrow form on the page.
- The marketing hero shows the real app, rendered from the same components.

## User Stories

### Everyone using the app

1. As a member, I want the interface to look the same on Windows, macOS and Linux, so that the product feels deliberate rather than dependent on my machine.
2. As a member, I want body text at a comfortable reading size (about 16px), so that I can work for hours without straining.
3. As a member, I want one clear primary action per screen, in the accent colour, so that I always know what the screen expects of me.
4. As a member, I want regions separated by space and surface rather than by borders, so that the screen feels calm and the content stands out.
5. As a member, I want the content to sit in one raised panel next to the navigation, so that what I'm working on is clearly the foreground.
6. As a member, I want every page to open with the same header (title, one-line description, primary action on the end), so that I can find my bearings on any page instantly.
7. As a member, I want menus, dialogs and popovers to share one look and one motion, so that the product feels like one thing.
8. As a member, I want disabled buttons to look plainly unavailable rather than faded brand colour, so that I don't think the app is broken.
9. As a member, I want menu items to be the same size and typeface as the rest of the interface, so that menus don't look like a different app.
10. As a member, I want icons drawn at a fine, consistent stroke, so that they sit quietly next to the text.
11. As a member, I want sentence-case labels instead of all-caps eyebrows, so that the interface reads like plain language.
12. As a member, I want links to be recognisable without relying on colour alone, so that I can find them whatever accent the product uses.

### Navigation and the shell

13. As a member, I want the Organization switcher at the top of the sidebar, so that I always know which Organization I'm working in.
14. As a member, I want a search entry in the sidebar showing its shortcut, so that I learn I can jump anywhere with Ctrl/⌘ K.
15. As a member, I want the notification bell and my account at the bottom of the sidebar, so that the top of the page belongs to my work.
16. As a member, I want the unread count on the bell shown as a number, so that I don't depend on a coloured dot.
17. As a member, I want the active page marked in the sidebar by more than colour (a surface and an accented icon), so that I can see where I am at a glance.
18. As a member, I want to collapse the sidebar to an icon rail, so that wide tables and documents get more room.
19. As a member, I want to toggle the sidebar with `[`, so that I can do it without reaching for the mouse.
20. As a member, I want the collapsed rail to show each item's name on hover and focus, so that I never have to guess what an icon means.
21. As a member, I want the sidebar to stay collapsed or expanded on this device the next time I come back, so that I set it once per screen size.
22. As a member, I want my laptop and my desktop monitor to remember the sidebar independently, so that each screen gets the layout that suits it.
23. As a member on a phone, I want the sidebar to open as a drawer from a menu button, so that the content gets the whole screen.
24. As a member, I want Settings to swap the sidebar to settings navigation with a clear "Back to app" at the top, so that I can get in and out of settings without hunting.
25. As a member, I want settings navigation grouped under "Your account" and the Organization's name, so that I can tell personal settings from shared ones.
26. As a member, I want a breadcrumb at the top of the panel, so that I know where I am inside settings.

### Dashboard

27. As the first member of a new Organization, I want a Getting started checklist on the dashboard, so that I know what to do first instead of facing an empty page.
28. As a member, I want each checklist step to tick itself off when I've actually done it, so that the list reflects reality rather than my clicks.
29. As a member, I want to see how many steps are done, so that I know how close the Organization is to being ready.
30. As a member, I want each open step to carry one button that takes me straight to doing it, so that I don't have to find the right page.
31. As a member, I want steps for modules the product doesn't have to be absent, so that I'm never sent to a page that doesn't exist.
32. As a member without permission to invite or to manage billing, I want those steps hidden from me, so that I'm not offered actions I can't take.
33. As a member, I want the checklist to disappear once every step is done, so that the dashboard stops nagging me.

### Settings

34. As a member, I want each setting laid out as its name and a one-line explanation beside the control, so that I understand a setting before I change it.
35. As a member, I want settings grouped into titled sections with no boxes around them, so that long pages stay calm and scannable.
36. As a member, I want each section to save on its own, so that I can change my name without worrying about other fields.
37. As a member, I want the Save button to wake up only once I've changed something, so that I know when there's something to save.
38. As a member, I want my profile photo shown next to plain Upload and Remove buttons (with drag-and-drop still working), so that the page isn't dominated by a drop zone.
39. As a member, I want Appearance (Light, Dark, System) as a segmented control that applies immediately, so that I can see the result straight away.
40. As a member on a phone, I want settings rows to stack (label above control), so that nothing is squeezed.

### Lists

41. As an admin, I want the members list as full-width rows (avatar, name and email, role, status, joined), so that I can scan the Organization quickly.
42. As an admin, I want status shown as a small pill with a dot and a word ("Active", "Invited"), so that it doesn't depend on colour.
43. As an admin, I want each row's actions in a trailing "⋯" menu with destructive actions separated and in red, so that I can't remove someone by accident.
44. As an admin, I want a filter box and a count above each list, so that I can find one member in a large Organization.
45. As a member, I want files, API keys, webhooks, invoices and admin lists to look and behave like the members list, so that I learn one pattern once.

### Sign-in and account pages

46. As a visitor signing in, I want a simple form on a quiet page (logo, title, fields, one button), so that signing in feels fast and trustworthy.
47. As a visitor, I want "Forgot password?" next to the password label, so that recovery is where I look for it.
48. As a visitor, I want the alternative sign-in methods (Google, GitHub, a sign-in link by email) below the form, so that the main path stays primary.
49. As a visitor, I want the same look on register, reset password, verify email, magic link and accept invitation, so that the whole flow feels like one product.

### Admin console

50. As a superadmin, I want the admin console in the same shell as the app, so that I don't have to learn a second interface.
51. As a superadmin, I want an unmistakable "Admin" marker that stays visible in the admin console, so that I never mistake admin actions for ordinary ones.

### Marketing site

52. As a visitor to the marketing site, I want the same typeface, colour and restraint as the app, so that the site and the product feel like one company.
53. As a visitor, I want the hero to show the real product (the app's shell and dashboard), so that I can see what I'd be getting instead of reading about it.
54. As a visitor, I want one primary call to action in the hero, so that the next step is obvious.
55. As a visitor, I want the hero to settle into place once when the page loads, so that the page feels alive without being busy.
56. As a visitor on a phone, I want the hero's product picture to show the app the way my phone would, so that it reads at that size.
57. As a visitor, I want the marketing pages to stay fast with the new typeface, so that the site doesn't feel heavier than before.

### Theme, locale and accessibility

58. As a member who prefers dark mode, I want every new surface, accent and state to have a dark counterpart with the same contrast care, so that dark mode isn't an afterthought.
59. As a member who set reduced motion, I want the hero animation, the rail collapse and every menu transition to be instant, so that nothing moves that I asked not to.
60. As a keyboard user, I want a visible accent focus ring on every interactive element, including rail icons and row menus, so that I always know where I am.
61. As a keyboard user, I want row menus to open with focus on their first item and close on Escape, so that I can act on a row without a mouse.
62. As a screen-reader user, I want the collapsed rail's items, the bell's unread count and the checklist progress announced in words, so that the visual shorthand isn't lost on me.
63. As a member using the Arabic locale, I want the sidebar, the rail and every row layout to mirror correctly, and Arabic text rendered in a matching typeface, so that the product feels native in right-to-left.
64. As a member in any non-Arabic locale, I want not to download the Arabic typeface, so that the app stays light.
65. As a member, I want every touch target to stay at least 24px (and comfortable on touch screens), so that the larger, airier layout is also easier to hit.

### Adopting the template

66. As a developer building a product on the template, I want to rebrand by changing one accent value per theme, so that I don't have to retune and re-audit the whole palette.
67. As a developer, I want the accent's soft tints (selected rows, active navigation) derived from the accent automatically, so that a new accent looks right everywhere without extra work.
68. As a developer, I want page header, settings section, settings row, data list, checklist and shell components in the shared UI package, so that every new page I add looks right by default.
69. As a developer, I want new pages to get the look from those components rather than from hand-written classes, so that pages can't drift apart again.
70. As a developer, I want the shell components to adapt to the width of their container rather than the window, so that I can embed them (as the marketing hero does) and they still lay out correctly.
71. As a developer, I want every size in rem from one root value, so that changing the product's overall scale is one change.
72. As a developer, I want the glossary's vocabulary (Organization, Member) used consistently in the interface copy, so that the product speaks one language.

## Implementation Decisions

### Tokens (the shared token layer)

- The existing semantic token names stay (background, foreground, card, primary, muted, accent, border, ring, and the rest), so components keep compiling. Their **values** change to the neutral set from ADR-0008: a cool near-white canvas, white surface, a slightly sunken surface, near-black ink, two quieter text tiers, two hairline weights. Dark mode is a full counterpart set.
- **Primary is the brand.** Iris: `#6246ea` light, `#9a88ff` dark, with near-black text on the dark-mode accent. Soft tints (selected navigation, row hover, focus ring) are derived with `color-mix()` from primary, so a rebrand is one value per theme. From the prototype:

  ```css
  --brand-soft: color-mix(in oklab, var(--brand) 9%, var(--canvas)); /* active nav */
  --brand-wash: color-mix(in oklab, var(--brand) 6%, var(--surface)); /* row hover */
  --ring:       color-mix(in oklab, var(--brand) 55%, transparent);
  ```

- **Added tokens:** a sunken surface; a second text tier between foreground and muted; a stronger hairline; radii for controls (8px), menus (10px) and the panel (14px); shadows for the panel, menus and controls (blue-tinted shadows go); motion: 150ms fast, 220ms base, one ease-out curve.
- **A shadow token that composes into a comma list must never be `none`.** Use a transparent zero shadow. The prototype lost every dark-mode input outline to this.
- **Type:** Instrument Sans for everything. Page titles use the width axis (about 86%) with tight tracking; the marketing hero headline goes further (about 78%). Machine values (API keys, webhook secrets, IDs, code) use the system monospace stack, and nothing else does.
- **Scale:** the root is 112.5% and every size is rem, so text, controls and spacing scale together. Tailwind's spacing unit is rem already. Fixed-pixel sizes in components are converted.
- **Font delivery:** the font files and their `@font-face` (variable woff2, weight 400 to 700, width 75 to 100; Latin subset preloaded; `font-display: swap`) live in the shared UI package and both apps consume them. IBM Plex Sans Arabic is declared with a `unicode-range` so only Arabic text downloads it. Both OFL licences ship alongside the files.
- Theme switching stays on the existing class mechanism (the `.dark` class set by the boot snippet). The prototype's `data-theme` attribute is prototype-only.
- lucide icons render at a 1.5 stroke globally.

### Shared shell components (in the shared UI package, no data of their own)

- **AppShell**: a sidebar column plus an inset content panel; collapsed/expanded and drawer state come in as props. It lays out with **container queries**, not viewport breakpoints, so it renders correctly inside the marketing hero frame.
- **Sidebar parts**: Organization switcher slot, search entry (label plus shortcut hint), navigation group with an optional sentence-case group label, navigation row (icon, label, active state, tooltip when collapsed), and a footer slot (user, notifications).
- **Collapsed rail**: about 64px wide; labels, shortcut hints and the user's name hide; group labels become thin dividers; the footer stacks. Every row keeps an accessible name and shows a tooltip on hover and focus.
- **PageHeader**: title (the condensed style), optional one-line description, an optional action slot on the end. It replaces the ~25 hand-written page titles.
- **SettingsSection** (title, rows, an optional footer with the section's own Save) and **SettingsRow** (label, hint, control): two columns, label column about 280px, stacking to one column in a narrow container. Hairline between rows, no surrounding card. Content capped at about 960px.
- **DataList**: full-width table styling (muted sentence-case header, hairline rows, accent-wash hover, muted secondary text, no outer card), a toolbar (filter plus count) and a trailing actions cell built on the existing dropdown menu. **StatusPill**: dot plus a word, with a hollow dot for pending states.
- **Checklist / ChecklistStep**: progress (count plus bar), steps with done/open state, title, hint and one action.
- The existing Button, Input, Select, DropdownMenu, Dialog, Popover, Tooltip and Command components are restyled to the new tokens rather than replaced. Disabled primary buttons use the sunken surface and the quietest text, not faded brand colour. Menu items use the interface's own size and typeface.
- Every new component stays free of top-level side effects (the package is `sideEffects: false` with per-component chunks).

### The app (apps/web)

- **AppLayout** composes AppShell. The top header is removed. Search (the command palette trigger), the notification bell and the user menu move into the sidebar; the bell's popover and the user menu open from the footer. The breadcrumb moves into the panel's top row. The page title comes from each page's PageHeader.
- **Sidebar collapse** is per device: stored in browser storage, read before first paint where possible, toggled by a button in the panel's top row and by `[` (registered in the existing shortcuts registry so the cheat sheet lists it, and ignored inside text fields). No backend column.
- **Settings mode** keeps its own sidebar navigation, adds "Back to app" at the top, and groups into "Your account" and the Organization's name.
- **AdminLayout** composes the same AppShell with its own navigation and a persistent "Admin" marker in a distinct, non-accent colour.
- **Dashboard**: the Getting started checklist replaces the current empty state. Steps and their "done" signals come from data the app already fetches:
  - complete your profile: the user has a name (and photo, if file storage is on);
  - upload a file (file storage on): the Organization has at least one file;
  - invite a member (permission to invite): the Organization has more than one member or a pending invitation;
  - choose a plan (billing on, permission to manage billing): the Organization's subscription is past the trial/free state.

  Steps are gated by the same module, permission and flag checks the navigation uses. When every visible step is done the checklist is not rendered.
- **Pages move onto the shared components:** every settings page (profile, security, notifications, Organization, members, billing, API keys, webhooks), files, reports, and the admin pages. Card-wrapped forms become SettingsSections. Each section saves independently, and its Save is enabled only when that section is dirty.
- **Auth pages** (login, register, forgot/reset password, verify email, magic link, accept invitation, change-email confirm/cancel, two-factor challenge) lose AuthCard's card: logo, condensed title, one-line description, form, alternatives, then the switch-flow link. Centring stays margin-based (the existing fix for tall forms on short screens).
- **Copy:** user-facing "workspace" and "team" become Organization/Member (GLOSSARY.md). All new strings go through translation keys in the English source first, then the existing translator workflow for the other locales.

### Marketing (apps/marketing)

- Reads the same tokens and font. The budget gate gains an explicit, measured font allowance (preloaded Latin subset) rather than being switched off.
- The landing hero: condensed headline, subtitle, one primary CTA plus a quiet text link, then the **real app shell** (AppShell + sidebar + dashboard checklist from the shared UI package, filled with sample content) rendered to static HTML at build time, with no client JavaScript. It is decorative to assistive technology (hidden and inert), fades out at the bottom, shows panel-only at phone width, and plays one load sequence (frame rises, then checklist rows settle, about 0.7s, disabled under reduced motion).
- The hero's own heading and paragraph styles are scoped to its direct children, because the frame inside it contains headings and paragraphs (a prototype bug).
- The highlights below the hero become plain columns with a hairline above each, not cards. Other marketing pages pick up the tokens; they are restyled to the same language rather than re-laid-out.

## Testing Decisions

- **Good tests here check what a person sees or does, not how it's built:** "pressing `[` collapses the sidebar and it stays collapsed after reload", not "the store key is set". No test asserts a class name or a token value.
- **Seam 1 (main, existing): Playwright.** The visual regression suite is regenerated on purpose once the shell and pages land (all 16 baselines, via the existing Docker recipe for Linux baselines from Windows). The axe-core accessibility gate re-checks contrast for every new token pairing in both themes. The `mobile-layout` (no horizontal overflow, the drawer opens into the viewport) and `touch-targets` specs are updated for the header-less shell and must stay green. Prior art: `visual.spec.ts`, `a11y.spec.ts`, `mobile-layout.spec.ts`, `touch-targets.spec.ts`.
- **Seam 2 (existing): Vitest + Testing Library at page and layout level in apps/web**, only for new behaviour:
  - the sidebar collapses and expands from the button and from `[`, `[` does nothing inside a text field, and the choice persists per device;
  - checklist steps reflect real data, are absent when their module is off or the member lacks the permission, and the checklist disappears when all visible steps are done;
  - a settings section saves on its own, and its Save is enabled only when that section changes;
  - the Admin marker is present on every admin page.

  Prior art: the `AppLayout*.test.tsx` family (module- and permission-gated navigation), `DashboardEmptyState*.test.tsx` (one file per module configuration), `ProfileSettingsPage.test.tsx`.
- **Marketing's existing build gates:** `check-budget` (with the new font allowance), Lighthouse CI (LCP and CLS with the font and the hero frame), `check-seo` and `check-hreflang`.
- **No new seams** and no per-component unit tests for the new layout components. Their look is covered by the visual suite, their behaviour by the page tests. Storybook stories are updated (both themes) as documentation, and Storybook's existing a11y check applies.
- Module-configuration rule (from earlier work): any code path that only runs when an optional module is on (file storage, billing) gets its test in a file per module configuration, like the dashboard empty-state tests.

## Out of Scope

- The Expo mobile app. It has its own components and doesn't read the shared tokens; it gets a separate plan.
- Transactional email templates.
- A density setting (comfortable/compact). There is one density.
- Storing the sidebar state per account. It's per device by decision.
- Onboarding screens (F-018). The checklist is the onboarding.
- A display serif or any third typeface.
- New pages, features or information architecture beyond what's listed. The navigation items stay the same.
- Re-laying-out marketing pages other than the landing page. They take the new tokens and type only.
- Fixing the Arabic RTL visual test, which already cannot pass on `main`. It is regenerated with the rest but not newly fixed here.

## Further Notes

- **Rollout order:** tokens and font first (everything changes at once, behind no flag), then the shell, then the shared page components with the page sweep, then auth, admin and marketing, then the baseline regeneration. Each step leaves the app working.
- The prototype is the visual reference for spacing, weights and states. Where this spec and the prototype disagree, this spec wins. Rewrite components properly rather than copying prototype CSS.
- Contrast for the accent was measured: Iris `#6246ea` is 5.86:1 under white text and 5.47:1 as text on the canvas; `#9a88ff` is 6.21:1 on the dark surface. The neutral text tiers must each clear 4.5:1 on canvas, surface and sunken in both themes. axe enforces this.
- Memory trap from the prototype: viewport breakpoints inside an embedded shell fire on the page, not the frame. Hence container queries.
- `@forge/ui` must be rebuilt after a branch switch (its dist is gitignored and survives checkouts), or Storybook and the apps resolve stale components.
