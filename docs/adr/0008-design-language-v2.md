# ADR-0008: Design language v2 — one webfont, one accent token

## Status

Accepted. The default accent is **Iris**: `#6246ea` light (5.86:1 with white
on it, 5.47:1 as text on the canvas), `#9a88ff` dark (6.21:1 on the surface).
Chosen against indigo and violet on the prototype kept on the
`prototype/visual-overhaul` branch.

**Date:** 2026-10-02

## Context

The web app and the marketing site share one token layer
(`packages/ui/styles/tokens.css`), derived on 2026-09-12 from a single
reference site: navy ink, a vivid blue, blue-tinted shadows. It reads as one
product's brand rather than a template's, and that brand is spread across ~20
tokens: the foreground, the muted text, the borders and the shadows are all
blue. A product built on the template that wants its own colour has to retune
every one of them and re-measure contrast for each.

The same layer deliberately ships no webfont, and `apps/marketing`'s
`scripts/check-budget.mjs` is written around that ("text paints in the first
frame and costs zero bytes"). The consequence is that the type, which is what
carries most of the "premium" in the services this overhaul is measured
against (Stripe for structure, Linear for chrome), renders as whatever each OS
supplies: Segoe UI on Windows, SF on macOS.

The overhaul's two goals pull against the status quo in both places: it has
to feel premium, and it has to be cheap for a downstream product to make its
own.

## Decision

- **Neutrals carry no brand.** Canvas, surfaces, text and hairlines are
  near-achromatic with a faint cool tint, the same in every product built on
  the template.
- **Brand lives in one accent token** (plus its dark-theme value), used only
  for the primary action, focus rings, links and the active navigation state.
  Its soft tints are derived from it with `color-mix()`, so rebranding is one
  value per theme.
- **One typeface: Instrument Sans**, self-hosted as a variable woff2 (weight
  400–700, width 75–100), in both `apps/web` and `apps/marketing`. Only the
  Latin subset is preloaded; `font-display: swap`. **IBM Plex Sans Arabic** is
  declared with a `unicode-range`, so it downloads only when Arabic glyphs
  render (the `ar` locale). Arabic snippets on a non-Arabic page (the language
  switcher's "العربية") use the platform face, so they don't pull the file in.
- **Machine values** (API keys, webhook secrets, IDs, code) use the
  platform's own monospace stack, which costs zero bytes. Nothing else does: no
  display serif, no monospace for ordinary labels.
- **Every size is in rem, and the root is 112.5%** (18px), so the body
  renders at about 16px rather than 14px. Text, controls and spacing scale
  together from that one value.
- `check-budget.mjs` keeps its font rules (swap, same origin, a preload) and
  gains an 80 KB per-file limit rather than being switched off.
- lucide icons render at a 1.5px stroke.

## Consequences

- Rebranding drops from ~20 retuned tokens to one value per theme. Contrast
  only has to be re-measured for that value.
- The type is the same on every OS and carries the identity the colour no
  longer does.
- **Cost:** marketing gives up "zero bytes of font". One 57 KB file (upright
  Latin, variable) is preloaded on every page, and text can repaint once on a slow first visit
  (swap). LCP is still gated by T-306's Lighthouse budget.
- **Cost:** two more files to keep updated (the font files and their
  licences, both SIL OFL 1.1), and the Arabic face must stay in step with
  the Latin one in weight.
- An accent that is dark in light mode (graphite, for example) cannot be
  lifted mechanically for dark mode, which is why the token carries an
  explicit dark value rather than deriving one.
- Every Playwright visual baseline regenerates.

## Alternatives considered

- **Keep the navy palette and refine it.** It looks fine, but leaves the
  brand spread across the neutrals, which is exactly the rebranding cost
  this ADR exists to remove.
- **Keep zero webfonts.** Cheapest, but the system face differs per OS, and
  the type is what makes this look premium.
- **Inter.** Free and excellent, but it is the default of nearly every
  shadcn-based product, so it reads as a template rather than a choice.
  Mona Sans, Hanken Grotesk and Geist were also considered. Geist is now
  associated with Vercel, and Instrument Sans's width axis gives display
  headings a voice without a second family.
- **A font in the app only.** Splits the identity between the first thing a
  visitor sees and the product they then sign into.
