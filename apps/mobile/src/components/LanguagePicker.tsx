import { useTranslation } from "react-i18next";
import { Pressable, Text, View } from "react-native";

import { cn } from "@forge/utils";

import { useLocale } from "../lib/locale";

// Mirrors packages/ui/src/components/locale-switcher.tsx's intent (a picker
// backed by useLocale()) but as plain React Native primitives instead of a
// DOM dropdown -- there are only ever a handful of SUPPORTED_LOCALES, so a
// simple row of options needs no native picker dependency.
function nativeNameFor(code: string): string {
  try {
    return new Intl.DisplayNames([code], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

export function LanguagePicker() {
  const { t } = useTranslation();
  const { locale, supportedLocales, setLocale } = useLocale();

  return (
    <View className="gap-2">
      <Text className="text-sm font-medium text-slate-700">
        {t("common.locale_switcher.label")}
      </Text>
      <View className="flex-row flex-wrap gap-2">
        {supportedLocales.map((code) => {
          const selected = code === locale;
          return (
            <Pressable
              key={code}
              accessibilityRole="radio"
              accessibilityLabel={nativeNameFor(code)}
              accessibilityHint={t("common.locale_switcher.select_hint")}
              accessibilityState={{ selected }}
              onPress={() => {
                void setLocale(code);
              }}
              className={cn(
                "rounded-md border px-3 py-2",
                selected
                  ? "border-slate-900 bg-slate-900"
                  : "border-slate-300 bg-white",
              )}
            >
              <Text
                className={cn(
                  "text-sm font-medium",
                  selected ? "text-white" : "text-slate-900",
                )}
              >
                {nativeNameFor(code)}
              </Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}
