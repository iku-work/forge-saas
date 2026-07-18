// Canonical locale registry (T-230): the single source of truth for which
// locale codes this package ships data for, plus per-locale display metadata.
// index.ts re-exports SUPPORTED_LOCALES/SupportedLocale from here rather than
// the other way around, so this file has no dependency on index.ts (avoids
// an import cycle between "what locales exist" and "how to describe them").
export const SUPPORTED_LOCALES = ["en", "fi", "de", "es", "fr", "ar"] as const;
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number];

export interface LocaleMeta {
  code: SupportedLocale;
  nativeName: string;
  dir: "ltr" | "rtl";
}

// The template's first right-to-left locale (T-231). Every locale not
// listed is "ltr" -- see the `dir` computation below.
const RTL_LOCALES: ReadonlySet<string> = new Set(["ar"]);

// nativeName is derived from Intl.DisplayNames rather than a hardcoded map --
// asking a locale to display *itself* (e.g.
// new Intl.DisplayNames(["fi"], { type: "language" }).of("fi") -> "suomi")
// always returns that language's own endonym. This mirrors
// packages/ui/src/components/locale-switcher.tsx's own nativeNameFor(), just
// computed once here so every consumer (web switcher, mobile picker, any
// future surface) reads the same values instead of each re-deriving them.
function nativeNameFor(code: string): string {
  try {
    return new Intl.DisplayNames([code], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

// The metadata registry itself: the language switcher (T-069) and mobile
// picker (T-070) render from this (via SUPPORTED_LOCALES / LOCALES), not a
// hardcoded list of codes/names each maintains separately.
export const LOCALES: readonly LocaleMeta[] = SUPPORTED_LOCALES.map((code) => ({
  code,
  nativeName: nativeNameFor(code),
  dir: RTL_LOCALES.has(code) ? "rtl" : "ltr",
}));

export function localeMeta(code: string): LocaleMeta | undefined {
  return LOCALES.find((locale) => locale.code === code);
}
