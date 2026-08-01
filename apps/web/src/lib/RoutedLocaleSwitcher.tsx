import { useNavigate, useRouterState } from "@tanstack/react-router";

import { useLocale } from "@forge/hooks";
import { LocaleSwitcher } from "@forge/ui";

import { withLocale } from "./locale-url";

/**
 * T-233: @forge/ui's LocaleSwitcher stays router-agnostic (mobile renders the
 * same component), so the "rewrite the current path's locale segment in
 * place" half of the switcher lives here, in the app that has a router.
 *
 * Switching is the *explicit* choice the URL-wins rule carves out: unlike
 * following a shared `/de/...` link, it does persist -- setLocale() writes
 * `User.locale` through PATCH /auth/me and the locale into localStorage --
 * and only then moves the URL. `replace: true` keeps the pre-switch URL out
 * of history: the back button should leave the page, not undo the language.
 */
export function RoutedLocaleSwitcher() {
  const navigate = useNavigate();
  const { setLocale } = useLocale();
  const href = useRouterState({ select: (state) => state.location.href });

  return (
    <LocaleSwitcher
      onSwitch={async (next) => {
        await setLocale(next);
        // href, not pathname: the page the user is on includes its query and
        // hash, and a language switch must not drop them.
        await navigate({ href: withLocale(href, next), replace: true });
      }}
    />
  );
}
