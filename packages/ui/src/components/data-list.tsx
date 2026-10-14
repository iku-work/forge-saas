import { forwardRef } from "react";
import type { ButtonHTMLAttributes, HTMLAttributes } from "react";
import { MoreHorizontal } from "lucide-react";

import { cn } from "@forge/utils";

/*
 * Grid-based list rows (ADR-0008, Stripe's lists): full width, hairline rows,
 * a wash on hover, no outer card. For lists that are *not* virtualized the
 * restyled <Table> gives the same look; these pieces are for the ones that
 * are (a virtualizer positions rows itself, so there is no <table> to use).
 *
 * The column layout is the caller's: pass the same `grid-cols-[...]` class to
 * DataListColumns and to every DataListRow. It is a class rather than a prop
 * because Tailwind only generates classes it can read in source.
 */

/** The column labels. aria-hidden: each row names its own cells for
 *  assistive technology, and a label row outside the list's semantics would
 *  read as a stray first item. */
function DataListColumns({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "grid items-center gap-4 border-b border-border px-3 pb-2 text-[0.78rem] font-medium text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

const DataListRow = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "grid items-center gap-4 border-b border-border px-3 py-3 text-sm transition-colors hover:bg-primary-wash",
        className,
      )}
      {...props}
    />
  ),
);
DataListRow.displayName = "DataListRow";

/** Secondary text in a row: dates, counts, ids. */
function DataListMuted({
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn("whitespace-nowrap text-muted-foreground", className)}
      {...props}
    />
  );
}

interface RowActionsButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: an icon-only button needs a name, e.g. "Actions for Maya". */
  "aria-label": string;
}

/** The trailing "⋯" that opens a row's menu. Forwards its ref so it can be
 *  a DropdownMenuTrigger's child. */
const RowActionsButton = forwardRef<HTMLButtonElement, RowActionsButtonProps>(
  ({ className, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      className={cn(
        "inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-surface-sunken hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:bg-surface-sunken data-[state=open]:text-foreground",
        className,
      )}
      {...props}
    >
      <MoreHorizontal className="size-4" aria-hidden="true" />
    </button>
  ),
);
RowActionsButton.displayName = "RowActionsButton";

export { DataListColumns, DataListRow, DataListMuted, RowActionsButton };
export type { RowActionsButtonProps };
