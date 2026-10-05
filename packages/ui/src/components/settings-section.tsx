import { useId } from "react";
import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@forge/utils";

/*
 * Settings as rows, not cards (ADR-0008, Stripe's structure): what a setting
 * is and why on the left, the control on the right, hairlines between rows
 * and no box around them. A section groups rows under a title and may carry
 * its own footer (usually that section's Save), so each section saves alone.
 *
 * Rows go to one column when the *section* is narrow -- a container query,
 * so the same section reads correctly in the panel, in a dialog, or beside a
 * collapsed or expanded sidebar.
 */

interface SettingsSectionProps extends Omit<
  HTMLAttributes<HTMLElement>,
  "title"
> {
  title: ReactNode;
  /** Shown under the title when the whole section needs one sentence. */
  description?: ReactNode;
  /** Usually the section's own Save button(s), aligned to the end. */
  footer?: ReactNode;
}

function SettingsSection({
  title,
  description,
  footer,
  children,
  className,
  ...props
}: SettingsSectionProps) {
  const titleId = useId();
  return (
    <section
      aria-labelledby={titleId}
      className={cn("@container/section [&+&]:mt-12", className)}
      {...props}
    >
      <div className="pb-3.5">
        <h2 id={titleId} className="text-[0.9375rem] font-semibold">
          {title}
        </h2>
        {description ? (
          <p className="mt-1 text-[0.8125rem] text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {children}
      {footer ? (
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          {footer}
        </div>
      ) : null}
    </section>
  );
}

interface SettingsRowProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  "title"
> {
  label: ReactNode;
  /** One line explaining the setting. */
  hint?: ReactNode;
  /** The control's id, when the label should be a real <label> for it. Leave
   *  unset when `label` is already a label element (a form library's) or the
   *  control labels itself (a radio group with its own aria-label). */
  htmlFor?: string;
}

function SettingsRow({
  label,
  hint,
  htmlFor,
  children,
  className,
  ...props
}: SettingsRowProps) {
  return (
    <div
      className={cn(
        "grid gap-x-10 gap-y-2 border-t border-border py-5 @2xl/section:grid-cols-[minmax(0,17.5rem)_minmax(0,1fr)]",
        className,
      )}
      {...props}
    >
      <div>
        {htmlFor ? (
          <label htmlFor={htmlFor} className="font-medium">
            {label}
          </label>
        ) : (
          <div className="font-medium">{label}</div>
        )}
        {hint ? (
          <p className="mt-0.5 text-[0.8125rem] text-muted-foreground">
            {hint}
          </p>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        {children}
      </div>
    </div>
  );
}

export { SettingsSection, SettingsRow };
export type { SettingsSectionProps, SettingsRowProps };
