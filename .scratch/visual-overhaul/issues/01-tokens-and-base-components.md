# 01: New colours, scale and restyled base components

**What to build:** the whole app and marketing site switch to the new neutral
palette with the Iris accent, sizes scale from one root value, and the existing
base components (buttons, inputs, menus, dialogs, command palette) take the new
look. Layouts do not change yet.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

## Read first

1. `docs/adr/0008-design-language-v2.md` (whole file, short).
2. `packages/ui/styles/tokens.css` (whole file, ~260 lines). This is the file you change most.
3. The prototype's `:root`, `[data-theme="dark"]` and `[data-accent="iris"]` CSS blocks
   (see README for how to view it). Use them as the source of values.
4. `packages/ui/src/components/button.tsx`, then each other component file only when you restyle it.

## Steps

### 1. Replace the colour values in `tokens.css` (keep every existing variable name)

Light (`:root`), mapping prototype names → existing tokens:

| Token | Value | Prototype name |
|---|---|---|
| `--background` | `#f6f7f9` | canvas |
| `--foreground` | `#171a21` | ink |
| `--card`, `--popover` | `#ffffff` | surface |
| `--card-foreground`, `--popover-foreground`, `--secondary-foreground` | `#171a21` | ink |
| `--primary` | `#6246ea` | Iris |
| `--primary-foreground` | `#ffffff` | |
| `--secondary`, `--muted`, `--accent` | `#f1f2f5` | sunken |
| `--muted-foreground` | `#6b7280` | ink-3 |
| `--accent-foreground` | `#171a21` | |
| `--border`, `--input` | `#e7e9ee` / `#d6d9e0` | line / line-strong (input uses the stronger one) |
| `--ring` | `color-mix(in oklab, var(--primary) 55%, transparent)` | |
| `--destructive` | `#c42b2b` | |

Dark (`.dark`):

| Token | Value |
|---|---|
| `--background` | `#0e1014` |
| `--foreground`, `--*-foreground` text tokens | `#e8eaee` |
| `--card`, `--popover` | `#15181e` |
| `--secondary`, `--muted`, `--accent` | `#1c2028` |
| `--muted-foreground` | `#8a91a0` |
| `--primary` | `#9a88ff` |
| `--primary-foreground` | `#0e1014` (dark text on the light accent) |
| `--border` / `--input` | `#252a33` / `#333a46` |
| `--destructive` | `#ff8a80`, with `--destructive-foreground: #0e1014` |

Rewrite the header comment: the palette is now ADR-0008's neutral set; the brand is
`--primary` only. Delete the comments about the old navy/Calendly source.

### 2. Add the new tokens (both themes where they differ)

In `:root` and `.dark`:
- `--foreground-secondary` (second text tier): light `#4b5261`, dark `#a6adba`.
- `--surface-sunken`: light `#f1f2f5`, dark `#1c2028`.
- `--border-strong`: light `#d6d9e0`, dark `#333a46`.
- Derived from primary (write once in `:root`, they follow the theme automatically):
  - `--primary-soft: color-mix(in oklab, var(--primary) 9%, var(--background));` (active nav)
  - `--primary-wash: color-mix(in oklab, var(--primary) 6%, var(--card));` (row hover)

Expose them in `@theme inline` as `--color-foreground-secondary`,
`--color-surface-sunken`, `--color-border-strong`, `--color-primary-soft`,
`--color-primary-wash`, so classes like `text-foreground-secondary`,
`bg-surface-sunken` and `bg-primary-soft` work.

In `@theme inline`, replace radii and shadows:
- Radii: `--radius-sm: 0.375rem` (6px), `--radius-md: 0.5rem` (8px, controls),
  `--radius-lg: 0.625rem` (10px, menus/popovers), `--radius-xl: 0.875rem` (14px, the panel).
- Shadows (neutral, not blue-tinted), from the prototype:
  - `--shadow-panel: 0 0 0 1px rgb(23 26 33 / 0.05), 0 1px 2px rgb(23 26 33 / 0.04), 0 12px 32px -16px rgb(23 26 33 / 0.08);`
  - `--shadow-menu: 0 0 0 1px rgb(23 26 33 / 0.07), 0 8px 24px -6px rgb(23 26 33 / 0.16);`
  - `--shadow-control: 0 1px 2px rgb(23 26 33 / 0.06);`
  - Remove `--shadow-card`, `--shadow-elevated`, `--shadow-button` and replace their
    uses (search `shadow-card`, `shadow-elevated`, `shadow-button` in `packages/ui/src`
    and `apps/`) with `shadow-panel` / `shadow-menu` / `shadow-control`.
  - Shadows must differ in dark mode, and `@theme inline` can't hold per-theme values.
    So put the three values above in plain variables `--elev-panel`, `--elev-menu`,
    `--elev-control` inside `:root`, put the dark values inside `.dark`:
    - `--elev-panel: 0 0 0 1px rgb(255 255 255 / 0.06);`
    - `--elev-menu: 0 0 0 1px rgb(255 255 255 / 0.08), 0 12px 32px -6px rgb(0 0 0 / 0.6);`
    - `--elev-control: 0 0 #0000;` (**never `none`**)

    and in `@theme inline` write `--shadow-panel: var(--elev-panel);`,
    `--shadow-menu: var(--elev-menu);`, `--shadow-control: var(--elev-control);`.
- Motion: add to `:root`: `--duration-fast: 150ms; --duration-base: 220ms; --ease-out-soft: cubic-bezier(0.2, 0.8, 0.2, 1);`

### 3. Type scale and root size

- In the `@layer base` block add `html { font-size: 112.5%; }`.
- `--font-sans` becomes `"Instrument Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`
  (the font files arrive in ticket 02; until then the fallback renders, which is fine).
- Add `--font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;`
- Add a page-title text style: `--text-title: 1.75rem; --text-title--line-height: 1.15;`
  and a utility class in `@layer components`:
  `.type-title { font-size: var(--text-title); line-height: 1.15; font-weight: 600; font-stretch: 86%; letter-spacing: -0.02em; }`
- Keep the existing display/heading ramp (marketing uses it).
- Body text: add `font-size: 0.875rem;` to the `body` rule. With the 18px root that
  renders at 15.75px, which matches the prototype at its 112% setting (14px × 1.125).
  `text-sm` (0.875rem) stays the app's normal body size.

### 4. Icons

lucide icons: set a global stroke width. Add to the base layer:
`svg.lucide { stroke-width: 1.5; }` (lucide-react adds the `lucide` class).

### 5. Restyle base components in `packages/ui/src/components/`

Work file by file; after each, run `pnpm --filter @forge/ui type-check`.

- `button.tsx`: radius `rounded-md`; height `h-9` default; primary = `bg-primary text-primary-foreground shadow-control hover:bg-primary/90`;
  secondary/outline = `bg-card text-foreground shadow-[0_0_0_1px_var(--color-border-strong)] hover:bg-surface-sunken`;
  ghost = `text-foreground-secondary hover:bg-surface-sunken hover:text-foreground`.
  **Disabled:** `disabled:bg-surface-sunken disabled:text-muted-foreground disabled:shadow-none disabled:opacity-100`
  (not a faded accent). Focus: `focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background`.
- `input.tsx`, `password-input.tsx`: `bg-card rounded-md` with a 1px `border-input`,
  hover `border-muted-foreground`, focus `border-primary ring-4 ring-ring`.
- `dropdown-menu.tsx`, `popover.tsx`, `command.tsx`, `tooltip.tsx`: content `rounded-lg bg-popover shadow-menu`, no extra border.
  Menu items: same font size as body (`text-sm`), height ~`h-8`, `rounded-sm`, hover/focus `bg-surface-sunken`.
  **This fixes the bug where user-menu items looked smaller than the rest.**
  Destructive items use `text-destructive`.
- `dialog.tsx`, `sheet.tsx`: `rounded-xl shadow-menu`; overlay `bg-black/30`.
- `card.tsx`: `rounded-xl bg-card shadow-panel`, no border (it will be used less after later tickets).
- `badge.tsx`: `rounded-full`, `bg-surface-sunken text-foreground-secondary` default.
- `tabs.tsx`: the segmented style from the prototype: list `bg-surface-sunken p-1 rounded-lg`, active trigger `bg-card shadow-panel text-foreground`.
- `checkbox.tsx`: checked = `bg-primary`.
- Links: in `tokens.css` base layer, `a` inside prose isn't styled globally today; leave as is.
  Where components render links, use `text-primary underline decoration-primary/35 underline-offset-[3px] hover:decoration-current`.

### 6. Remove hardcoded palette classes

Search `apps/web/src` and `packages/ui/src` (not tests) for `slate-`, `gray-`,
`zinc-`, `amber-`, `red-`, `green-`, `blue-`. Replace each with a semantic token.
Known ones: about 15 occurrences (banners, maintenance screen, impersonation banner).
For warning banners (amber), add tokens `--warning: #8a5a00` / dark `#f5c46b` and
`--warning-soft` (`color-mix(in oklab, var(--warning) 12%, var(--background))`), expose
as `--color-warning`, `--color-warning-soft`, and use those.

## Verify

```bash
pnpm --filter @forge/ui type-check && pnpm --filter @forge/ui build
pnpm --filter web type-check && pnpm --filter web lint && pnpm --filter web test
pnpm --filter marketing build && pnpm --filter marketing test
pnpm --filter @forge/ui storybook:build
```

Then `pnpm --filter web dev`, open the app, and check by eye in light and dark:
the canvas is near-white (not blue), primary buttons are Iris, disabled Save is grey,
user-menu items match the menu's font size, focus rings are visible.

Contrast: every text token must be ≥ 4.5:1 on `--background`, `--card` and
`--surface-sunken` in both themes. The values above were measured; if you change one,
re-measure (any WCAG contrast calculator).

## Acceptance

- [ ] All colour tokens replaced; new tokens exist and work as Tailwind classes.
- [ ] No `none` inside any shadow token.
- [ ] Root font size 112.5%; body text renders ≈15.75px.
- [ ] Buttons, inputs, menus, popovers, dialogs, tabs restyled; disabled primary is grey.
- [ ] No hardcoded palette classes left in `apps/web/src` or `packages/ui/src` (excluding tests and stories).
- [ ] Web tests, lint, type-check, marketing build and tests, Storybook build all pass.
- [ ] Visual suite failing is expected (README).

## Don't

- Don't rename existing token variables (hundreds of classes depend on them).
- Don't change layouts, the sidebar or pages; later tickets do that.
- Don't add the font files (ticket 02).
