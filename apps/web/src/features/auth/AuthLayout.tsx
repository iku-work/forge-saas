import { useEffect, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useMarketingUrl } from "@forge/hooks";

import { useRouteLocale } from "../../lib/useRouteLocale";

interface AuthLayoutProps {
  title: string;
  description?: string;
  children: ReactNode;
  /** The link to the other flow ("Don't have an account? Sign up"). */
  footer?: ReactNode;
}

/**
 * Every account page (sign in, register, reset, verify, invitations, the
 * first organization): a narrow form on the quiet page, no card (visual
 * overhaul, ticket 11). Brand, title, one-line description, the page's own
 * content, then the link to the other flow; Terms and Privacy at the bottom.
 */
export function AuthLayout({
  title,
  description,
  children,
  footer,
}: AuthLayoutProps) {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const marketingUrl = useMarketingUrl();
  const headingRef = useRef<HTMLHeadingElement>(null);

  // Re-focus the heading whenever the title changes -- on the initial
  // navigation into a page, and on every in-page async-state transition
  // (e.g. "Verifying your email…" -> "Email verified") so screen reader
  // users land on the new content instead of keeping focus on a now-gone
  // button.
  useEffect(() => {
    headingRef.current?.focus();
  }, [title]);

  return (
    <div className="flex min-h-dvh flex-col bg-background p-6">
      {/* `m-auto` on the column rather than centring on the flex parent.
          They centre identically until the form is taller than the viewport
          -- a landscape phone, or the on-screen keyboard halving the height.
          Then centring overflows in *both* directions and the top of the
          form, heading included, is unreachable. Auto margins collapse to
          zero instead of going negative, so the column pins to the top and
          all of it can be scrolled to. */}
      <div className="m-auto w-full max-w-[22.5rem] py-12">
        <div className="mb-10 flex items-center gap-2.5 text-[0.9375rem] font-semibold">
          <span
            aria-hidden="true"
            className="grid size-7 place-items-center rounded-md bg-primary text-sm font-bold text-primary-foreground"
          >
            {t("common.app.name").trim().charAt(0).toUpperCase()}
          </span>
          {t("common.app.name")}
        </div>
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-[2.125rem] font-semibold leading-[1.1] tracking-[-0.025em] [font-stretch:82%] focus:outline-none"
        >
          {title}
        </h1>
        {description ? (
          <p className="mt-1.5 text-foreground-secondary">{description}</p>
        ) : null}
        <div className="mt-8 space-y-4 [&_input:not([type=checkbox])]:h-10">
          {children}
        </div>
        {footer ? (
          <div className="mt-8 space-y-2 text-[0.84rem] text-foreground-secondary [&_a]:text-primary">
            {footer}
          </div>
        ) : null}
      </div>
      {/* The documents live on the marketing host, so a product that turned
          that module off has nothing to link to. */}
      {marketingUrl ? (
        <p className="flex justify-center gap-4 text-xs text-muted-foreground">
          <a
            href={`${marketingUrl}/${locale}/terms`}
            target="_blank"
            rel="noreferrer"
            className="hover:text-foreground"
          >
            {t("auth.legal.terms")}
          </a>
          <a
            href={`${marketingUrl}/${locale}/privacy`}
            target="_blank"
            rel="noreferrer"
            className="hover:text-foreground"
          >
            {t("auth.legal.privacy")}
          </a>
        </p>
      ) : null}
    </div>
  );
}
