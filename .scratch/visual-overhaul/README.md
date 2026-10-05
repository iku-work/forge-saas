# Visual overhaul: how to work these tickets

You are implementing a visual redesign of `apps/web` (React + Vite + Tailwind v4)
and `apps/marketing` (Astro), sharing components from `packages/ui` (`@forge/ui`).
This file holds the rules shared by every ticket. Each ticket is in `issues/NN-*.md`.
**Current state of every ticket: `PROGRESS.md`. Read it first; update it when you finish a step or a ticket.**
**Running a local model? Read `LOCAL-MODEL.md` first: launch flags and a prompt template.**

## How to work (important for a small context window)

1. Do **one ticket per session**. Start a fresh session for each ticket.
2. In a new session, read **only**: this README, the ticket file, and the files the
   ticket's "Read first" section lists. Do not read `spec.md` in full; tickets
   quote what you need from it. Do not read whole large files (`AppLayout.tsx`
   is ~590 lines): use search to find the part you need, and read that range.
3. Work the steps in order. After each step, run the narrowest check listed
   (one test file, or the type-check of one package), not the whole suite.
4. When the ticket's checklist is all ticked, run its "Verify" commands, then
   commit (see "Committing") and tick the boxes in the ticket file, setting
   `**Status:** done`.
5. If a step is unclear or a check fails twice for a reason you cannot find, stop,
   write what you found under a `## Notes` heading at the bottom of the ticket,
   and leave `**Status:** blocked`. Do not guess at large rewrites.

Pick the next ticket: the lowest-numbered ticket whose status is `ready-for-agent`
and whose "Blocked by" tickets are all `done`.

## Where to work

- Git worktree: `C:\Users\IK\Documents\saas-template\.claude\worktrees\visual-overhaul`
- Branch: `visual-overhaul`. Commit here. Never commit to `main`. Never push unless the user asks.
- Never merge or cherry-pick from branch `prototype/visual-overhaul` (throwaway).

## Reference material (read only the part you need)

- Decisions: `docs/adr/0008-design-language-v2.md` (short; read it once in ticket 01).
- Vocabulary: `GLOSSARY.md`. Say **Organization** (never workspace/team) and **Member**.
- Visual reference: the prototype, viewable without checking out its branch:
  `git show prototype/visual-overhaul:apps/web/prototype/visual-overhaul.html > %TEMP%\proto.html`
  then open that file in a browser. Its CSS (top of the file) has the exact values
  for spacing, radii, shadows and colours. **Rewrite**, don't copy-paste: the
  prototype has no tests, uses raw CSS and a `data-theme` attribute; this app
  uses Tailwind classes and a `.dark` class.
- Full spec (only if a ticket says so): `.scratch/visual-overhaul/spec.md`.

## Commands (run from the worktree root, in bash)

| What | Command |
|---|---|
| Install deps (once per worktree) | `pnpm install` |
| Build the UI package (needed after any change in `packages/ui`) | `pnpm --filter @forge/ui build` |
| Type-check one package | `pnpm --filter @forge/ui type-check` / `pnpm --filter web type-check` / `pnpm --filter marketing type-check` |
| Lint web | `pnpm --filter web lint` |
| One web test file | `pnpm --filter web exec vitest run src/layouts/AppLayout.test.tsx` |
| All web tests | `pnpm --filter web test` |
| Marketing build (runs its gates) | `pnpm --filter marketing build` |
| Marketing tests | `pnpm --filter marketing test` |
| i18n checks | `pnpm --filter @forge/i18n run lint` |
| Rebuild i18n after adding keys | `pnpm --filter @forge/i18n build` |
| Storybook build | `pnpm --filter @forge/ui storybook:build` |
| Everything (ticket 14 only) | `pnpm lint && pnpm type-check && pnpm test` |

If the package filter name differs, check `name` in that package's `package.json`.
If a web test fails with a missing export from `@forge/ui`, `@forge/i18n` or
`@forge/hooks`, rebuild that package first: their `dist/` is stale, not your code.

## Rules that apply to every ticket

- **Tokens, not colours.** Components use semantic Tailwind classes
  (`bg-background`, `text-muted-foreground`, `bg-primary`, `border-border`, …).
  Never write a hex value or `slate-500`-style palette class in a component. New
  values go in `packages/ui/styles/tokens.css`.
- **The accent is `primary`.** Use it only for the primary action, focus rings,
  links and the active navigation item. Not for decoration.
- **Dark mode is the `.dark` class** on `<html>` (variant `dark:`). Do not add a
  `prefers-color-scheme` media query or a `data-theme` attribute.
- **Every user-facing string goes through translations.** Web: `useTranslation()`
  and `t("namespace.key")`. Add the key to `packages/i18n/locales/en/<namespace>.json`
  first; other locales may lag (English fallback is allowed and checked by
  `check-locales`). Then rebuild `@forge/i18n`. ESLint's `react/jsx-no-literals`
  fails on raw text in JSX. Interpolation is ICU: `{name}`, never `{{name}}`.
  Components inside `packages/ui` must take their labels as props from the app,
  not call `t()` themselves.
- **Sizes in rem.** The root font size becomes 112.5% in ticket 01. Do not
  introduce `px` sizes for text, controls or spacing (1px borders are fine).
- **Logical properties** for RTL (`ms-`/`me-`/`ps-`/`pe-`/`start-`/`end-`, not
  `ml-`/`mr-`/`left-`/`right-`). The `ar` locale must mirror correctly.
- **Accessibility floor:** visible focus ring on everything interactive; targets
  at least 24×24px; never colour alone (status needs a word, links an underline,
  counts a number); icon-only buttons need `aria-label`; tooltips need a text
  alternative.
- **Motion:** use the duration/easing tokens from ticket 01. The global
  reduced-motion rule in `tokens.css` already neutralises transitions and
  animations; don't fight it.
- **`@forge/ui` components:** one file per component in
  `packages/ui/src/components/` (kebab-case, like `button.tsx`), exported from
  `packages/ui/src/index.ts`, no top-level side effects (the package is
  `sideEffects: false` and tsup builds one chunk per component). Each new
  component gets a `<name>.stories.tsx` next to it (copy the shape of
  `button.stories.tsx`).
- **No new npm dependencies** unless the ticket says so. Lucide icons are already
  installed.
- **Tests check behaviour, not styling.** No test asserts a class name or a colour.

## Known traps (each one has cost a session before)

- `packages/ui/dist/` and `packages/i18n/dist/` are gitignored and survive branch
  switches. Rebuild them after switching branches or after editing those packages.
- A `box-shadow` token that is part of a comma-separated list must never be
  `none` (use `0 0 #0000`). `none` silently drops the whole declaration.
- jsdom (web tests) lacks some browser APIs; stubs live in
  `apps/web/src/test/setup.ts`. If a test dies with "is not a function" on
  `scrollIntoView`, `matchMedia`, pointer capture or `ResizeObserver`, add or reuse
  a stub there.
- Web tests can be slow; the timeout is already 15s. Don't lower it.
- A code path that only runs when an optional module (file storage, billing) is on
  needs its test in its own file per module configuration, like
  `apps/web/src/features/DashboardEmptyState.test.tsx` and
  `DashboardEmptyStateNoFiles.test.tsx`.
- In bash on Windows, set `MSYS_NO_PATHCONV=1` before any `docker run` with
  container paths.

## The visual screenshot suite is expected to be red until ticket 14

`apps/web/e2e/visual.spec.ts` compares screenshots against committed baselines.
Ticket 01 changes every pixel, so it stays red on this branch until ticket 14
regenerates the baselines. **Every other check must pass at the end of every
ticket:** web unit tests, lint, type-check, i18n lint, marketing build and tests.

## Committing

One commit per ticket (more is fine), message:

```
<Imperative summary> (visual overhaul NN)

<Two to four lines: what changed and why.>

Co-Authored-By: <your model name> <noreply@example.com>
```

The pre-commit hooks run prettier and whitespace checks. If they modify files,
`git add` them and commit again. Never use `--no-verify`.
