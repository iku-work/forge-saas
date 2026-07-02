import { Check, ChevronsUpDown, Globe } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useLocale } from "@forge/hooks";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "./dropdown-menu";

// Native names are derived from Intl.DisplayNames rather than a hardcoded
// map -- asking a locale to display *itself* (e.g. new
// Intl.DisplayNames(["fi"], { type: "language" }).of("fi") -> "suomi")
// always returns that language's own endonym, which is what a language
// picker should show regardless of how many locales @forge/i18n ships.
function nativeNameFor(code: string): string {
  try {
    return new Intl.DisplayNames([code], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
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
