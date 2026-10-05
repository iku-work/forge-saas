# 02: Self-hosted Instrument Sans (plus Arabic) in both apps

**What to build:** the app and the marketing site render in Instrument Sans on every
OS, from font files we ship ourselves. Arabic text renders in IBM Plex Sans Arabic,
which only downloads when Arabic text is on the page. Marketing's build gates and
Lighthouse budget still pass.

**Blocked by:** None (can start immediately). If ticket 01 is done, `--font-sans`
already names "Instrument Sans"; if not, do that one-line change here too.

**Status:** done

## Read first

1. `packages/ui/styles/tokens.css`: find `--font-sans`.
2. `packages/ui/package.json`: the `exports` and `files` fields.
3. `apps/marketing/scripts/check-budget.mjs`: the header comment and the webfont check (search `font-display`).
4. `apps/marketing/src/layouts/BaseLayout.astro`: the `<head>` section.
5. `apps/web/index.html`: the `<head>` section.

## Steps

1. **Get the font files.** Both fonts are SIL OFL 1.1 and on Google Fonts:
   - Instrument Sans, variable, axes `wdth` 75–100 and `wght` 400–700, normal and italic.
   - IBM Plex Sans Arabic, weights 400, 500, 600 (static files are fine).

   Download woff2 files from Google Fonts' CSS API with a modern browser user agent, for example:
   ```bash
   curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36" \
     "https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wdth,wght@0,75..100,400..700;1,75..100,400..700&display=swap" > /tmp/is.css
   ```
   The CSS lists one woff2 URL per unicode subset. Keep **latin** and **latin-ext**
   only. Download each with `curl -o`. Do the same for
   `family=IBM+Plex+Sans+Arabic:wght@400;500;600` and keep only the **arabic** subset files.
   Also download both licence texts (OFL.txt) from the fonts' GitHub repositories
   (`github.com/Instrument/instrument-sans`, `github.com/IBM/plex`).

   If the network is unavailable, stop and mark the ticket blocked. Do not substitute another font.

2. **Put them in the UI package:** `packages/ui/fonts/` with clear names, e.g.
   `instrument-sans-latin.woff2`, `instrument-sans-latin-ext.woff2`,
   `instrument-sans-italic-latin.woff2`, …, `ibm-plex-sans-arabic-400.woff2`, …,
   plus `OFL-instrument-sans.txt`, `OFL-ibm-plex-sans-arabic.txt`.

3. **Write `packages/ui/styles/fonts.css`** with one `@font-face` per file, using the
   `unicode-range` values exactly as Google's CSS gave them, `font-display: swap`,
   and for the variable font `font-weight: 400 700; font-stretch: 75% 100%;`.
   For the Arabic faces use `font-family: "Instrument Sans"` too, with Arabic
   `unicode-range`, so one family name covers both scripts and only Arabic text
   triggers the Arabic download. Paths relative to the CSS file: `url("../fonts/…")`.

4. **Export it:** in `packages/ui/package.json` add `"./styles/fonts.css": "./styles/fonts.css"`
   and `"./fonts/*": "./fonts/*"` to `exports`, and make sure `fonts` is in `files`
   if that field exists. In `tokens.css`, do **not** import fonts.css (keep tokens free
   of network requests); each app imports it.

5. **Web app:** in `apps/web/src/index.css`, add `@import "@forge/ui/styles/fonts.css";`
   right after the tokens import. **No preload in the web app:** it sits behind sign-in
   and `swap` is enough. Add a one-line comment saying so next to the import.

6. **Marketing:** in `apps/marketing/src/styles/global.css` import the same `fonts.css`.
   In `BaseLayout.astro`'s `<head>`, preload the Latin normal file only. Astro can import the
   asset URL in the frontmatter: `import fontUrl from "@forge/ui/fonts/instrument-sans-latin.woff2?url";`
   then `<link rel="preload" href={fontUrl} as="font" type="font/woff2" crossorigin />`.

7. **Budget gate:** `check-budget.mjs` already requires `font-display: swap`; it should
   pass. Update its header comment: the site now ships one self-hosted family (ADR-0008),
   preloads only the Latin file, and the check still guarantees swap. Add a size check:
   fail the build if any single `.woff2` in `dist/` is over 80 KB, with a message naming
   the file and pointing at ADR-0008. Follow the style of the existing checks in that file.

8. **CSP:** fonts are same-origin, so no CSP change. If the marketing Caddy config sets
   `font-src`, confirm it allows `'self'` (search `font-src` in the repo).

## Verify

```bash
pnpm --filter @forge/ui build
pnpm --filter web build && pnpm --filter web test
pnpm --filter marketing build && pnpm --filter marketing test
```

In the built marketing `dist/`, confirm `.woff2` files exist and the HTML has one
`rel="preload"` for the Latin file. Run `pnpm --filter web dev`, check in the browser's
network panel that `instrument-sans-latin*.woff2` loads, and that switching the app to
the `ar` locale loads the Arabic file while English pages never do.

If you can, run Lighthouse on marketing (`pnpm --filter marketing lighthouse`; on
Windows this may fail with EPERM from chrome-launcher, in which case note it and skip).

## Acceptance

- [x] Font files and both OFL licences are in `packages/ui/fonts/`.
- [x] `fonts.css` declares the faces with `font-display: swap` and correct `unicode-range`s.
- [x] Web and marketing render Instrument Sans; Arabic text renders Plex Sans Arabic.
- [x] Non-Arabic pages never request the Arabic files.
- [x] Marketing preloads exactly one font file; budget gate has the 80 KB per-file check.
- [x] All web and marketing builds and tests pass.

## Don't

- Don't use Google Fonts' CDN at runtime (privacy and CSP); files must be self-hosted.
- Don't add a second Latin family or a monospace webfont (mono is the system stack).

## Notes

Completed 2026-10-05. Two deviations from the steps above: the Instrument Sans licence comes from
`github.com/google/fonts` (`ofl/instrumentsans/OFL.txt`), not the URL in step 1; and `fonts.css` also renders
`[lang=ar]` snippets on non-Arabic pages in the system face, or the language switcher's "العربية" loads the
Arabic file on every page. Lighthouse not run locally (Windows EPERM). See `PROGRESS.md`.
