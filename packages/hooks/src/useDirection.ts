import { useSyncExternalStore } from "react";
import i18next from "i18next";

import { localeMeta } from "@forge/i18n";

export type Direction = "ltr" | "rtl";

function subscribeLanguageChanged(listener: () => void): () => void {
  i18next.on("languageChanged", listener);
  return () => {
    i18next.off("languageChanged", listener);
  };
}

function getDirectionSnapshot(): Direction {
  return localeMeta(i18next.language)?.dir ?? "ltr";
}

// Mirrors useLocale.ts's useSyncExternalStore-over-i18next-"languageChanged"
// shape -- direction is a pure function of the active locale (@forge/i18n's
// T-231 registry), so this needs no bootstrap/persistence of its own, just a
// live read of the same source useLocale() and apps/web's/apps/mobile's own
// i18n bootstraps already derive `dir` from.
export function useDirection(): Direction {
  return useSyncExternalStore(subscribeLanguageChanged, getDirectionSnapshot);
}
