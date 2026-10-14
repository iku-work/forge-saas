import type { HTMLAttributes } from "react";

import { cn } from "@forge/utils";

type StatusTone = "active" | "pending" | "neutral" | "danger";

interface StatusPillProps extends HTMLAttributes<HTMLSpanElement> {
  /** active: done / live. pending: waiting on something (hollow dot).
   *  neutral: inactive. danger: failed, infected, revoked. */
  tone?: StatusTone;
}

const DOT: Record<StatusTone, string> = {
  active: "bg-success",
  pending: "bg-transparent ring-[1.5px] ring-inset ring-muted-foreground",
  neutral: "bg-muted-foreground",
  danger: "bg-destructive",
};

/**
 * A status as a dot *and* a word: the colour helps a scan, the word is what
 * carries the meaning (WCAG 1.4.1), so children are required in practice.
 */
function StatusPill({
  tone = "neutral",
  className,
  children,
  ...props
}: StatusPillProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-surface-sunken px-2 py-0.5 text-xs font-medium text-foreground-secondary",
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className={cn("size-1.5 rounded-full", DOT[tone])}
      />
      {children}
    </span>
  );
}

export { StatusPill };
export type { StatusPillProps, StatusTone };
