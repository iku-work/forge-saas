import { useParams } from "@tanstack/react-router";

/**
 * T-233: the locale segment of the URL the user is currently on.
 *
 * Every in-app `<Link>`/`navigate()` target is now `/$locale/...` and needs a
 * `locale` param; reading it from the route rather than from i18next is what
 * keeps navigation inside the locale the visitor actually arrived in (a
 * German visitor following a link on `/de/settings` stays on `/de/...`).
 *
 * Only callable from inside the `/$locale` subtree -- which is everything
 * except the two redirect-only routes and NotFoundPage, whose single link
 * points at "/" and is resolved by routes/index.tsx instead.
 */
export function useRouteLocale(): string {
  return useParams({ from: "/$locale", select: (params) => params.locale });
}
