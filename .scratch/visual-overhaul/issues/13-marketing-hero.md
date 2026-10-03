# 13: Marketing landing with the real app in the hero

**What to build:** the marketing landing page opens with a large condensed headline,
the subtitle, one "Get started" button and a quiet "Read the guides" link, then a
picture of the real product: the app's shell and Getting started checklist, rendered
from the same `@forge/ui` components as static HTML. It rises into place once on load,
fades out at the bottom, and shows the panel without the sidebar on phones. Below it,
the three "What you start with" points as plain columns. Other marketing pages pick up
the new tokens and type with no layout change.

**Blocked by:** 02, 04, 10.

**Status:** ready-for-agent

## Read first

1. `apps/marketing/src/pages/[...locale]/index.astro` (the landing page).
2. `apps/marketing/src/components/SiteHeader.astro`.
3. `packages/ui/src/components/app-shell.tsx` and `checklist.tsx` (props only).
4. The prototype's "Marketing" screen and its CSS block starting at
   `/* ---- Marketing: the hero is the product itself`.
5. `apps/marketing/astro.config.*`: confirms the React integration is installed.

## Design

- Container `max-w-[75rem] mx-auto px-6`. Hero top padding `pt-18` (4.5rem).
- Headline: `text-[clamp(2.75rem,3.5vw+1.5rem,5.25rem)] leading-[1.02] font-semibold [font-stretch:78%] tracking-[-0.035em] max-w-[13ch]`.
- Subtitle: `mt-6 max-w-[34rem] text-xl text-foreground-secondary`.
- CTAs: `mt-9 flex items-center gap-5`; primary button `h-11 px-5`; secondary is a text link.
- Frame: `mt-16 h-[35rem] overflow-hidden rounded-t-2xl bg-background shadow-[0_0_0_1px_var(--color-border),0_30px_80px_-30px_rgb(23_26_33/0.25)]`,
  with `mask-image: linear-gradient(to bottom, #000 70%, transparent)` (and `-webkit-mask-image`).
  Inside, the shell rendered at a fixed inner width and scaled with `zoom: 0.8`
  (supported in all current browsers). On phones (`max-width: 767px`): height 22.5rem,
  `zoom: 0.72`, and the sidebar hidden, so only the panel shows.
- Load animation (once, CSS only, no JS): frame `@keyframes rise { from { opacity:0; transform: translateY(32px) } }`,
  640ms, `--ease-out-soft`, 120ms delay; checklist steps `settle` (opacity 0, translateY 6px)
  at 520/580/640/700ms. The global reduced-motion rule disables it; also add an explicit
  `@media (prefers-reduced-motion: reduce) { animation: none }` on these two selectors.
- Facts: `grid grid-cols-3 gap-10 pt-16 pb-36`, each item a `h2` (`text-base font-semibold pt-4 border-t border-border-strong`)
  and a paragraph (`mt-2 text-foreground-secondary`). One column on phones.

**Scope the hero styles to the hero's direct children** (`.hero > h1`, `.hero > p`), because
the frame inside it contains headings and paragraphs. (The prototype got this wrong first.)

## Steps

1. Create `apps/marketing/src/components/HeroProduct.tsx`, a React component that renders
   `AppShell` + `ShellSidebar` + `ShellPanel` + `PageHeader` + `Checklist` with **sample
   content** (Organization "Acme", nav Dashboard/Files/Reports/Settings with Dashboard active,
   a checklist with 1 of 4 done). All sample strings come from the marketing translations
   (add keys under `marketing.hero_product.*` in `packages/i18n/locales/en/marketing.json`);
   `@forge/ui` components take strings as props, so pass them in.
2. In `index.astro`, render it **without** a `client:*` directive, so Astro outputs static
   HTML and ships no JavaScript for it. Wrap it in the frame element with `aria-hidden="true"`
   and `inert`.
3. Rebuild the hero markup and the facts section per the design. Keep the existing
   translation keys (`marketing.hero.*`, `marketing.highlights.*`) and the SEO/JSON-LD code.
4. Container queries: the shell uses `@container` queries (ticket 04), so inside the 0.8-zoom
   frame it lays out as a desktop shell. Check that the frame's inner width (frame width
   / 0.8) is above the shell's two-column threshold; if not, set an explicit inner width.
5. Other marketing pages (features, pricing, blog, guides, legal): no layout change. Check
   by eye that they look right with the new tokens and font; fix only broken contrast or
   overflow. Remove any card/shadow styling that ticket 01 made look wrong.

## Verify

```bash
pnpm --filter @forge/ui build
pnpm --filter marketing build && pnpm --filter marketing test
pnpm --filter marketing type-check
```

In `dist/en/index.html`: the hero product markup is present as HTML, and no new `<script>`
was added for it (compare the script tags before and after). Run `pnpm --filter marketing preview`
and check desktop, 375px, dark (if marketing supports it), and reduced motion (DevTools →
Rendering → emulate). Lighthouse: `pnpm --filter marketing lighthouse` (may fail on Windows
with EPERM; then note it and leave it to ticket 14 / CI).

## Acceptance

- [ ] Hero matches the design; one primary CTA.
- [ ] The product frame is real `@forge/ui` components rendered statically (no client JS), hidden from assistive tech and inert.
- [ ] Panel-only on phones; animation plays once and respects reduced motion.
- [ ] Facts as columns, not cards.
- [ ] Marketing build gates (budget, SEO, hreflang, indexing) and tests pass.
