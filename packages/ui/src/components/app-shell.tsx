import { forwardRef, useId } from "react";
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import { Slot } from "@radix-ui/react-slot";
import { Search } from "lucide-react";

import { cn } from "@forge/utils";

/*
 * The application shell (ADR-0008): a recessive sidebar beside one raised
 * panel. These pieces render layout only -- no data, no routing, no
 * translation -- so the web app fills them with live navigation and the
 * marketing hero can render the same shell with sample content.
 *
 * Layout responds to the *container*, not the viewport: inside the marketing
 * hero the shell is drawn at a fraction of the page width, and a viewport
 * breakpoint would lay it out for the page rather than for its frame. At
 * 48rem of shell width and up the sidebar is a column; below that it is
 * hidden and the panel spans the width (the app shows its own drawer there).
 */

interface AppShellProps extends HTMLAttributes<HTMLDivElement> {
  sidebar: ReactNode;
}

function AppShell({ sidebar, children, className, ...props }: AppShellProps) {
  return (
    <div
      className={cn("@container/shell flex flex-1 flex-col", className)}
      {...props}
    >
      <div className="flex flex-1">
        <div className="sticky top-0 hidden h-dvh w-[15.5rem] shrink-0 @3xl/shell:block">
          {sidebar}
        </div>
        {/* min-w-0: a flex item will not shrink below its content otherwise,
            and one wide page (the members list) would scroll the whole app
            sideways on a phone. */}
        <div className="flex min-w-0 flex-1 flex-col @3xl/shell:py-2 @3xl/shell:pe-2">
          {children}
        </div>
      </div>
    </div>
  );
}

interface ShellSidebarProps extends HTMLAttributes<HTMLDivElement> {
  header?: ReactNode;
  footer?: ReactNode;
}

function ShellSidebar({
  header,
  footer,
  children,
  className,
  ...props
}: ShellSidebarProps) {
  return (
    <div
      className={cn("flex h-full flex-col gap-1 px-2.5 py-3", className)}
      {...props}
    >
      {header ? (
        <div className="flex shrink-0 flex-col gap-1 pb-2">{header}</div>
      ) : null}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        {children}
      </div>
      {footer ? (
        <div className="flex shrink-0 flex-col gap-1 pt-2">{footer}</div>
      ) : null}
    </div>
  );
}

interface ShellNavGroupProps extends HTMLAttributes<HTMLDivElement> {
  label?: ReactNode;
}

/** A labelled set of rows; announced as a group named by its label. */
function ShellNavGroup({
  label,
  children,
  className,
  ...props
}: ShellNavGroupProps) {
  const labelId = useId();
  return (
    <div
      role={label ? "group" : undefined}
      aria-labelledby={label ? labelId : undefined}
      className={cn("flex flex-col gap-0.5", className)}
      {...props}
    >
      {label ? (
        <p
          id={labelId}
          className="px-2.5 pb-1 pt-3 text-xs font-medium text-muted-foreground"
        >
          {label}
        </p>
      ) : null}
      {children}
    </div>
  );
}

const shellRowClass =
  "flex min-h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-start text-sm font-medium text-foreground-secondary transition-colors hover:bg-foreground/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&_svg]:size-4 [&_svg]:shrink-0";

interface ShellNavItemProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Render the child (e.g. a router link) instead of a <button>. */
  asChild?: boolean;
}

/**
 * One sidebar row. The active state is read from `aria-current="page"`, which
 * a router link sets itself, so the styling cannot disagree with what is
 * announced to assistive technology.
 */
const ShellNavItem = forwardRef<HTMLButtonElement, ShellNavItemProps>(
  ({ asChild = false, className, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(
          shellRowClass,
          "aria-[current=page]:bg-primary-soft aria-[current=page]:text-foreground aria-[current=page]:[&_svg]:text-primary",
          className,
        )}
        {...props}
      />
    );
  },
);
ShellNavItem.displayName = "ShellNavItem";

interface ShellSearchButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: ReactNode;
  /** The shortcut hint, e.g. "⌘K". Hidden from assistive technology: pass
   *  `aria-keyshortcuts` to announce it instead. */
  shortcut?: ReactNode;
}

const ShellSearchButton = forwardRef<HTMLButtonElement, ShellSearchButtonProps>(
  ({ label, shortcut, className, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      className={cn(shellRowClass, className)}
      {...props}
    >
      <Search aria-hidden="true" />
      <span className="flex-1 truncate">{label}</span>
      {shortcut ? (
        <kbd
          aria-hidden="true"
          className="rounded-sm px-1.5 py-0.5 font-sans text-xs font-medium text-muted-foreground shadow-[0_0_0_1px_var(--color-border-strong)]"
        >
          {shortcut}
        </kbd>
      ) : null}
    </button>
  ),
);
ShellSearchButton.displayName = "ShellSearchButton";

interface ShellPanelProps extends HTMLAttributes<HTMLDivElement> {
  /** The panel's top row: breadcrumb, and the drawer trigger on narrow screens. */
  top?: ReactNode;
}

function ShellPanel({ top, children, className, ...props }: ShellPanelProps) {
  return (
    <div
      className={cn(
        "flex min-h-dvh flex-1 flex-col bg-card @3xl/shell:min-h-[calc(100dvh-1rem)] @3xl/shell:rounded-xl @3xl/shell:shadow-panel",
        className,
      )}
      {...props}
    >
      {top ? (
        <div className="flex h-13 shrink-0 items-center gap-2 px-4 text-xs text-muted-foreground @3xl/shell:px-7">
          {top}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export {
  AppShell,
  ShellSidebar,
  ShellNavGroup,
  ShellNavItem,
  ShellSearchButton,
  ShellPanel,
};
export type {
  AppShellProps,
  ShellSidebarProps,
  ShellNavGroupProps,
  ShellNavItemProps,
  ShellSearchButtonProps,
  ShellPanelProps,
};
