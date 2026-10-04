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
| 01 | Tokens and base components | 🟡 | Steps 1–4 done, step 5 partial, step 6 not started. Details below. |
| 02 | Self-hosted font | ⬜ | Not started (no `packages/ui/fonts/`, no `fonts.css`). Needs internet. |
| 03 | Glossary copy sweep | ⬜ | |
| 04 | App shell | ⬜ | Blocked by 01. |
| 05 | Collapsible sidebar | ⬜ | Blocked by 04. |
| 06 | Page header + settings rows (Profile) | ⬜ | Blocked by 04. |
| 07 | Other settings pages | ⬜ | Blocked by 06. |
| 08 | Data list + Members | ⬜ | Blocked by 06. |
| 09 | Other lists | ⬜ | Blocked by 07, 08. |
| 10 | Getting started checklist | ⬜ | Blocked by 06. |
| 11 | Auth pages | ⬜ | Blocked by 01. |
| 12 | Admin console | ⬜ | Blocked by 04, 06, 08. |
| 13 | Marketing hero | ⬜ | Blocked by 02, 04, 10. |
| 14 | Integrate and verify | ⬜ | Blocked by all. |

Startable now: **01** (finish it), **02**, **03**.

## Ticket 01, step by step

Committed in b52cea71 (2026-10-04). Gemma did a first, uncommitted pass; Claude
reviewed it, repaired it and committed.

| Step | Status | Who | What |
|---|---|---|---|
| 1. Colour values | ✅ | Gemma, checked by Claude | All light/dark values match the ticket. |
| 2. New tokens | ✅ | Gemma, fixed by Claude | Gemma added everything correctly but left 5 uses of the deleted shadow classes (skip link, theme toggle, sheet, 2 command stories); Claude replaced them. |
| 3. Type scale and root size | ✅ | Gemma, repaired by Claude | Gemma's edit broke `tokens.css` (a declaration written over a comment, which broke the marketing build; duplicated `--text-title`; `body` rule overwritten). Claude restored and cleaned it. |
| 4. Icon stroke | ✅ | Gemma, moved by Claude | Rule moved into the base layer so utilities can still override it. |
| 5. Restyle base components | 🟡 | Gemma, button rewritten by Claude | Done: `button` (Claude rewrote it: sizes, icon sizing, double border, disabled state were wrong), `input`, dropdown content panel. **To do:** dropdown menu items, `popover`, `command`, `tooltip`, `dialog`, `sheet` (radius/overlay), `card`, `badge`, `tabs`, `checkbox`, `password-input`, links. |
| 6. Remove hardcoded palette classes | ⬜ | | Warning tokens it needs already exist. |

Checks at b52cea71: `@forge/ui` and web type-check clean; web tests 373/373;
marketing build passes, marketing tests 236 passed / 2 skipped; prettier hook passes.
Visual screenshot suite is expected to fail until ticket 14.

## Lessons for whoever continues

- Pre-commit hooks are **not installed** in this worktree. Run
  `C:\Users\IK\Documents\saas-template\backend\.venv\Scripts\pre-commit.exe run --files <changed files>`
  before each commit. The hook's prettier is v4 alpha; `pnpm exec prettier` (3.x) formats differently.
- Web tests regenerate `apps/web/src/routeTree.gen.ts`. Revert it (`git checkout -- apps/web/src/routeTree.gen.ts`), don't commit it.
- A fresh worktree needs its packages built first: `pnpm exec turbo run build --filter="web^..."`.
- Always run `pnpm --filter marketing build` after touching `tokens.css`: it is the only check that parses the CSS (web tests don't).
- Gemma handled "put these exact values here" well (steps 1–2) and broke things when editing around existing code (steps 3, 5). Give it small, table-like steps and have each one reviewed before committing.
