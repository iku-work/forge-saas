import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@forge/utils";

/**
 * The content column of a page inside the shell's panel (ADR-0008): left
 * aligned, capped at ~960px so rows and lists stay readable on a wide screen,
 * with tighter gutters when the shell itself is narrow.
 */
function Page({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "w-full max-w-[63rem] px-4 pb-24 pt-5 @3xl/shell:px-12",
        className,
      )}
      {...props}
    />
  );
}

interface PageHeaderProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  "title"
> {
  title: ReactNode;
  /** One line saying what the page is for. */
  description?: ReactNode;
  /** The page's primary action, at the end of the header. */
  actions?: ReactNode;
  /** Kept as "page-title" by default: the E2E and visual suites find pages by it. */
  titleTestId?: string;
}

/** Every page opens the same way: title, one-line description, primary action. */
function PageHeader({
  title,
  description,
  actions,
  titleTestId = "page-title",
  className,
  ...props
}: PageHeaderProps) {
  return (
    <div
      className={cn("mb-9 flex flex-wrap items-end gap-4", className)}
      {...props}
    >
      <div className="min-w-0 flex-1">
        <h1 className="type-title" data-testid={titleTestId}>
          {title}
        </h1>
        {description ? (
          <p className="mt-1.5 text-foreground-secondary">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}

export { Page, PageHeader };
export type { PageHeaderProps };
