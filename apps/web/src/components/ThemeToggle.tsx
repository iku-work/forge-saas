import { useId } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";

import { THEME_PREFERENCES, type ThemePreference } from "../lib/theme";
import { useTheme } from "../lib/useTheme";

const ICONS: Record<ThemePreference, typeof Sun> = {
  light: Sun,
  dark: Moon,
  system: Monitor,
};

/**
 * The theme control (F-004).
 *
 * A native radio group rather than a switch or a dropdown, for three reasons:
 *
 *   - there are three states, not two, so a switch cannot express it and a
 *     two-state toggle would have to drop "system" -- the one value that keeps
 *     honouring the OS after the user walks away;
 *   - all three fit on screen, so Hick's law says show them rather than hide
 *     them behind a menu that costs a click to even see the current value;
 *   - a radio group gets arrow-key navigation, roving tabindex and the
 *     "3 of 3" announcement from the browser, none of which a div with
 *     role="radio" gets for free.
 *
 * The inputs are `sr-only` rather than `hidden`: a hidden input is not
 * focusable, which would take the keyboard behaviour with it. The visible
 * chrome is the <label>, styled off the input's state with `peer-*`.
 */
export function ThemeToggle() {
  const { t } = useTranslation();
  const { preference, setTheme } = useTheme();
  const groupId = useId();

  return (
    <div
      role="radiogroup"
      aria-label={t("common.theme.label")}
      data-testid="theme-toggle"
      className="inline-flex gap-0.5 rounded-lg bg-surface-sunken p-1"
    >
      {THEME_PREFERENCES.map((value) => {
        const Icon = ICONS[value];
        const id = `${groupId}-${value}`;
        const selected = preference === value;
        return (
          <div key={value} className="contents">
            <input
              type="radio"
              id={id}
              name={groupId}
              value={value}
              checked={selected}
              onChange={() => {
                void setTheme(value);
              }}
              className="peer sr-only"
              data-testid={`theme-option-${value}`}
            />
            <label
              htmlFor={id}
              // min-h/min-w 44px: WCAG 2.2 2.5.8 sets the floor at 24, the
              // Apple HIG at 44, and this control is three targets sitting
              // side by side -- the case where the difference is felt.
              className="flex min-h-11 min-w-11 cursor-pointer items-center justify-center gap-2 rounded-md px-3 text-sm font-medium text-foreground-secondary transition-colors hover:text-foreground peer-checked:bg-card peer-checked:text-foreground peer-checked:shadow-panel peer-focus-visible:outline-none peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2"
            >
              <Icon className="size-4 shrink-0" aria-hidden="true" />
              {t(`common.theme.${value}`)}
            </label>
          </div>
        );
      })}
    </div>
  );
}
