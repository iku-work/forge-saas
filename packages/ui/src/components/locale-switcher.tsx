import { Check, ChevronsUpDown, Globe } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useLocale } from "@forge/hooks";
import { localeMeta } from "@forge/i18n";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "./dropdown-menu";

// Native names come from @forge/i18n's locale metadata registry (T-230,
// src/locales.ts) rather than each UI surface re-deriving them -- that
// registry itself computes them via Intl.DisplayNames (asking a locale to
// display *itself*, e.g. "fi" -> "suomi", always returns that language's own
// endonym), so this stays a lookup, not a hardcoded name map.
function nativeNameFor(code: string): string {
  return localeMeta(code)?.nativeName ?? code;
}

export function LocaleSwitcher() {
  const { t } = useTranslation();
  const { locale, supportedLocales, setLocale } = useLocale();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={t("common.locale_switcher.label")}
          className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium"
        >
          <Globe
            className="size-4 shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
          <span>{nativeNameFor(locale)}</span>
          <ChevronsUpDown
            className="size-4 shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>
          {t("common.locale_switcher.label")}
        </DropdownMenuLabel>
        {supportedLocales.map((code) => (
          <DropdownMenuItem
            key={code}
            onSelect={() => {
              void setLocale(code);
            }}
          >
            <span className="flex-1">{nativeNameFor(code)}</span>
            {code === locale ? (
              <Check className="size-4" aria-hidden="true" />
            ) : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
