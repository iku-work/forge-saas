# Visual overhaul: progress

Branch `visual-overhaul` (worktree `.claude/worktrees/visual-overhaul`). Nothing pushed.
Update this file whenever a ticket or step changes state.

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
| 03 | Glossary copy sweep | ⬜ | |
| 04 | App shell | ⬜ | Unblocked. |
| 05 | Collapsible sidebar | ⬜ | Blocked by 04. |
| 06 | Page header + settings rows (Profile) | ⬜ | Blocked by 04. |
| 07 | Other settings pages | ⬜ | Blocked by 06. |
| 08 | Data list + Members | ⬜ | Blocked by 06. |
| 09 | Other lists | ⬜ | Blocked by 07, 08. |
| 10 | Getting started checklist | ⬜ | Blocked by 06. |
| 11 | Auth pages | ⬜ | Unblocked. |
| 12 | Admin console | ⬜ | Blocked by 04, 06, 08. |
| 13 | Marketing hero | ⬜ | Blocked by 02, 04, 10. |
| 14 | Integrate and verify | ⬜ | Blocked by all. |

Startable now: **03**, **04**, **11**.

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

## Lessons for whoever continues

- Pre-commit hooks are **not installed** in this worktree. Run
  `C:\Users\IK\Documents\saas-template\backend\.venv\Scripts\pre-commit.exe run --files <changed files>`
  before each commit. The hook's prettier is v4 alpha; `pnpm exec prettier` (3.x) formats differently.
- Web tests regenerate `apps/web/src/routeTree.gen.ts`. Revert it (`git checkout -- apps/web/src/routeTree.gen.ts`), don't commit it.
- A fresh worktree needs its packages built first: `pnpm exec turbo run build --filter="web^..."`.
- Always run `pnpm --filter marketing build` after touching `tokens.css`: it is the only check that parses the CSS (web tests don't).
- Gemma handled "put these exact values here" well (steps 1–2) and broke things when editing around existing code. It also swapped in similar-sounding Tailwind classes (`shadow-xl` for `shadow-menu`) and made unrequested changes. See `LOCAL-MODEL.md`.
- The shadcn token named `accent` is a grey hover surface, **not** the brand colour (that is `primary`). The README now says so.
