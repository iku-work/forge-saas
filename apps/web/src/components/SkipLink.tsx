import { useTranslation } from "react-i18next";

/** The id the skip link targets, and the one every shell must put on its
 *  <main>. Exported so the two are impossible to drift apart -- a skip link
 *  pointing at an id nothing renders is worse than no skip link, because it
 *  moves focus to nowhere and the next Tab restarts from the top of the
 *  document. */
export const MAIN_CONTENT_ID = "main-content";

/**
 * WCAG 2.4.1 Bypass Blocks (F-013).
 *
 * Must be the first focusable element in the document, so it has to be the
 * first child of the shell -- not visually first, *DOM* first. Everything
 * before it is something a keyboard user has to tab through before they can
 * skip anything.
 *
 * Hidden by `sr-only` until focused rather than by `display:none` or
 * `visibility:hidden`: both of those remove an element from the tab order
 * entirely, so the link could never receive the focus that is supposed to
 * reveal it. `sr-only` is a clip-rect, which keeps it focusable.
 *
 * `focus:` rather than `focus-visible:`: this element is only ever reached by
 * keyboard, so there is no mouse-focus case to suppress, and Safari's
 * `:focus-visible` support for links has historically been the flaky one.
 */
export function SkipLink() {
  const { t } = useTranslation();

  return (
    <a
      href={`#${MAIN_CONTENT_ID}`}
      data-testid="skip-link"
      className="sr-only focus:not-sr-only focus:absolute focus:start-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-background focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-foreground focus:shadow-elevated focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
    >
      {t("common.nav.skip_to_content")}
    </a>
  );
}
