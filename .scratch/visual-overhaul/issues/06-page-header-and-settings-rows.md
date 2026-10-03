# 06: Page header and settings rows, proven on the Profile page

**What to build:** a shared page header (title, one-line description, primary action at
the end) and settings layout (titled sections of two-column rows, each section with
its own Save). The Profile page is rebuilt with them end to end: Personal details
(photo, name, email) saves on its own, Save is enabled only after a change, and
Preferences (language, appearance) apply immediately.

**Blocked by:** 04.

**Status:** ready-for-agent

## Read first

1. `apps/web/src/features/settings/ProfileSettingsPage.tsx` (253 lines, read it whole).
2. `apps/web/src/features/settings/ProfileSettingsPage.test.tsx` (skim test names).
3. `apps/web/src/components/ThemeToggle.tsx` (it becomes the segmented Appearance control).
4. Prototype CSS: `.page`, `.page-head`, `.title`, `.lede`, `.section`, `.section-head`,
   `.row`, `.row-label`, `.row-hint`, `.row-control`, `.section-foot`.

## Design (values at the 112.5% root)

- Page content: `max-w-[63rem]` (about 960px at this root), padding `px-12 pt-5 pb-24`;
  on narrow containers `px-4`. Left-aligned, not centred.
- PageHeader: `flex items-end gap-4 mb-9`. Title uses the `.type-title` class from ticket 01
  and keeps `data-testid="page-title"`. Description: `mt-1.5 text-foreground-secondary`.
  Action slot at the end. On narrow containers it wraps.
- SettingsSection: a title (`text-[0.9375rem] font-semibold`, `pb-3.5`), then rows,
  then an optional footer: `flex justify-end gap-2 pt-4 border-t border-border`.
  Sections are separated by `mt-12`. **No card, no box.**
- SettingsRow: `grid grid-cols-[minmax(0,17.5rem)_minmax(0,1fr)] gap-x-10 gap-y-2 py-5 border-t border-border`.
  Left: label (`font-medium`, a real `<label htmlFor>` when the control is a single input)
  and hint (`text-[0.8125rem] text-muted-foreground mt-0.5`). Right: the control,
  `flex flex-wrap items-center gap-3`. Under a narrow container, one column.
  Use a container query on the section (`@container`) rather than a viewport breakpoint.

## Steps

1. **`@forge/ui` components** (one file each, exported from `index.ts`, with a story each):
   - `page-header.tsx`: `PageHeader({ title, description?, actions?, titleTestId = "page-title" })`.
   - `settings-section.tsx`: `SettingsSection({ title, children, footer? })` and
     `SettingsRow({ label, hint?, htmlFor?, children })`.
   Build `@forge/ui`.

2. **Rebuild `ProfileSettingsPage`** with them:
   - `PageHeader`: title = existing `common.nav.profile`, description = existing
     `auth.profile.description` (reword it in English to "How you appear to other members
     of your organization." if it says something else; keep the key).
   - Section "Personal details" (new key `auth.profile.section_details`):
     - Row "Photo": avatar (size-12) + "Upload photo" (secondary button) + "Remove"
       (ghost button, only if a photo exists). Hint: "PNG or JPG, up to 5 MB. You can
       also drop an image here." Keep drag-and-drop by keeping the existing `FileUploader`
       behaviour, but render it without its large dashed box: check `FileUploader`'s
       props for a compact/trigger mode; if none exists, add a `variant="button"` prop to
       `packages/ui/src/components/FileUploader.tsx` that renders only a button while
       keeping the drop handling on the row. Keep all existing test ids.
     - Row "Name": the existing name input (keep its test id and `autocomplete="name"`).
     - Row "Email": the email as text + a link "Change email" to the Security page (the
       page already explains this; turn that sentence into the link).
     - Footer: "Save changes" (primary). **Disabled until the name differs from the saved
       value** (`form.formState.isDirty`). After a successful save, reset the form to the new
       value so the button disables again.
   - Section "Preferences" (new key `auth.profile.section_preferences`), no footer:
     - Row "Language": `RoutedLocaleSwitcher` (unchanged behaviour).
     - Row "Appearance": `ThemeToggle`, restyled as a segmented control (Light / Dark /
       System with sun / moon / monitor icons) using the `tabs`-style look from ticket 01:
       container `bg-surface-sunken p-1 rounded-lg`, selected option `bg-card shadow-panel`.
       Keep it a radio group or `aria-pressed` buttons, whichever it is today. Applies immediately.
   - Remove the `Card` wrapper.

3. **Translations:** add the new keys to `en` (`auth.json` or wherever `auth.profile.*`
   lives; search for `"profile"` in `packages/i18n/locales/en/`). Rebuild `@forge/i18n`.

## Tests

In `ProfileSettingsPage.test.tsx` (keep existing tests passing; update selectors only
if they depended on the card):
- Save is disabled on load; typing a new name enables it; saving calls the API and
  disables it again.
- Changing appearance does not require pressing Save.

## Verify

```bash
pnpm --filter @forge/ui build
pnpm --filter web exec vitest run src/features/settings/ProfileSettingsPage.test.tsx
pnpm --filter web exec vitest run src/components/ThemeToggle.test.tsx
pnpm --filter web type-check && pnpm --filter web lint && pnpm --filter web test
```

Manual: the page matches the prototype's Profile screen; rows stack on a phone; `ar` mirrors.

## Acceptance

- [ ] `PageHeader`, `SettingsSection`, `SettingsRow` in `@forge/ui` with stories.
- [ ] Profile page uses them; no card; Save enabled only when dirty.
- [ ] Photo row is compact, drag-and-drop still works.
- [ ] Appearance is a segmented control that applies immediately.
- [ ] Tests pass; lint and type-check clean.
