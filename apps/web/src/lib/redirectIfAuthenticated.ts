import { redirect } from "@tanstack/react-router";

import type { AuthContext } from "../routes/__root";

/**
 * `beforeLoad` guard for the public auth pages, mirroring `_app`'s.
 *
 * `_app` pushes signed-out visitors to /login, but nothing pushed signed-in
 * ones the other way: /login had no `beforeLoad` at all, so opening the app
 * in a second tab and clicking "sign in" showed a login form to somebody who
 * was already authenticated. The session was fine -- the access token lives
 * in memory and AppRoot restores it from the httpOnly refresh cookie before
 * the router renders -- but the page never asked.
 *
 * That is worse than untidy. Logging in again mints a new session and
 * rotates the refresh cookie, which can sign out the tab you were happily
 * using; and the auth endpoints are rate limited to 5/minute per IP, so a
 * couple of pointless logins can lock you out of a real one.
 *
 * `hasToken` is the exception, and the reason this is a function rather than
 * a constant. /reset-password and /magic-link act on a token from an email,
 * and that token may belong to a *different* account than the one currently
 * signed in -- following a magic link for another user while signed in is a
 * real thing to do. When a token is present the page keeps its job; only the
 * bare, nothing-to-do visit redirects.
 */
export function redirectIfAuthenticated({
  context,
  params,
  hasToken = false,
}: {
  context: { auth: AuthContext };
  params: { locale: string };
  hasToken?: boolean;
}): void {
  if (hasToken || !context.auth.user) {
    return;
  }
  throw redirect({ to: "/$locale", params });
}
