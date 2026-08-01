import { useEffect } from "react";
import { useRouterState } from "@tanstack/react-router";

import { DEFAULT_LOCALE, LOCALE_REGISTRY } from "@forge/i18n";

import { withLocale } from "./locale-url";

/**
 * T-233: emits `<link rel="canonical">` and the per-locale `hreflang`
 * alternates for whatever page is currently matched. T-305 (marketing SEO)
 * relies on this being the one place that knows the convention.
 *
 * Deliberately imperative rather than a head-management library: this is the
 * only head state the app has, the tags are keyed off a single attribute so
 * cleanup is exact, and adding react-helmet/unhead for four `<link>` elements
 * would be the larger dependency.
 *
 * Alternates come from @forge/i18n's registry (T-230), not from the product's
 * `/config` subset. That keeps this synchronous with the first paint and
 * matches what `$locale.tsx` validates against; a product that narrows its
 * `locales` in product.config.ts and cares about SEO should narrow the list
 * here too.
 */

const MANAGED_ATTRIBUTE = "data-locale-head";

// Everything under these two subtrees is behind a session -- see
// $locale._app.tsx and $locale.admin.tsx. They get a canonical URL (so a
// crawler that somehow reaches one still knows the preferred form) but no
// alternates, and stay noindex.
const PRIVATE_ROUTE_IDS = new Set(["/$locale/_app", "/$locale/admin"]);

function head(): HTMLHeadElement | null {
  return typeof document === "undefined" ? null : document.head;
}

function appendManaged(
  parent: HTMLHeadElement,
  tag: "link" | "meta",
  attributes: Record<string, string>,
): void {
  const element = document.createElement(tag);
  element.setAttribute(MANAGED_ATTRIBUTE, "");
  for (const [name, value] of Object.entries(attributes)) {
    element.setAttribute(name, value);
  }
  parent.append(element);
}

export function LocaleHead() {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const isPrivate = useRouterState({
    select: (state) =>
      state.matches.some((match) => PRIVATE_ROUTE_IDS.has(match.routeId)),
  });

  useEffect(() => {
    const parent = head();
    if (!parent) {
      return;
    }
    for (const stale of parent.querySelectorAll(`[${MANAGED_ATTRIBUTE}]`)) {
      stale.remove();
    }

    const { origin } = window.location;
    // Search and hash are dropped: the only pages carrying either are the
    // token-bearing auth pages, and a canonical URL must never publish a
    // one-time token.
    appendManaged(parent, "link", {
      rel: "canonical",
      href: `${origin}${pathname}`,
    });

    if (isPrivate) {
      appendManaged(parent, "meta", {
        name: "robots",
        content: "noindex, nofollow",
      });
    } else {
      for (const { code } of LOCALE_REGISTRY) {
        appendManaged(parent, "link", {
          rel: "alternate",
          hreflang: code,
          href: `${origin}${withLocale(pathname, code)}`,
        });
      }
      appendManaged(parent, "link", {
        rel: "alternate",
        hreflang: "x-default",
        href: `${origin}${withLocale(pathname, DEFAULT_LOCALE)}`,
      });
    }

    return () => {
      for (const managed of parent.querySelectorAll(`[${MANAGED_ATTRIBUTE}]`)) {
        managed.remove();
      }
    };
  }, [pathname, isPrivate]);

  return null;
}
