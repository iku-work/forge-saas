import { createFileRoute, notFound } from "@tanstack/react-router";

import { detectPreferredLocale } from "../i18n";
import { localeOf, localeRedirect } from "../lib/locale-url";

/**
 * T-233: the backwards-compatibility net for locale-less URLs that are too
 * deep to be caught by `$locale.tsx` itself -- `/settings/org`,
 * `/accept-invitation/<token>`, `/admin/users`, every bookmark and every
 * email link mailed before this task landed. None of them match the
 * `/$locale/...` tree, so they fall through to this root splat and get
 * rewritten to their prefixed equivalent, query and hash intact.
 *
 * A path that *already* carries a supported locale and still ended up here
 * is a genuine 404 (`/en/nope`), not something to re-prefix -- rewriting it
 * would produce the same URL again and spin.
 */
export const Route = createFileRoute("/$")({
  beforeLoad: ({ context, location }) => {
    if (localeOf(location.href)) {
      throw notFound();
    }
    localeRedirect(
      location.href,
      detectPreferredLocale(context.auth.user?.locale),
    );
  },
});
