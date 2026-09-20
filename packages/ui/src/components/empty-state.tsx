import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@forge/utils";

export interface EmptyStateProps {
  /** Decorative -- the title carries the meaning, so it is hidden from
   *  assistive tech. Typed as lucide's own icon rather than a hand-written
   *  `ComponentType<{className}>`: lucide types `aria-hidden` as `Booleanish`
   *  (it accepts the string "true"), which a narrower signature rejects under
   *  this repo's `exactOptionalPropertyTypes`. */
  icon?: LucideIcon;
  /** What this list holds, as a statement rather than an absence: "No files
   *  yet", not "Empty". */
  title: string;
  /** Why it is empty and what fills it. One or two sentences. */
  description: string;
  /** The primary action. A list the user cannot fill from here (an audit log,
   *  say) legitimately has none. */
  action?: ReactNode;
  /** A "Learn more" link, or a secondary action. */
  secondaryAction?: ReactNode;
  className?: string;
  "data-testid"?: string;
}

/**
 * An empty state that teaches (F-010).
 *
 * The plan's argument, which is worth keeping in view: "No files yet." tells
 * the user something they can already see. An empty state is the *first*
 * thing a new user meets on most screens, so it is the best-attended
 * explanation of a feature the product will ever get -- and the only place a
 * first action can be offered in context.
 *
 * So every one of these carries four things: what the list holds, why it is
 * empty, what to do about it, and (optionally) where to read more.
 *
 * Not styled as an error. Muted foreground, no border, no destructive colour,
 * no warning icon -- "nothing here yet" is the ordinary state of a new
 * account, and a red-tinged panel on first login reads as a fault the user
 * has to fix before they can start.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  secondaryAction,
  className,
  "data-testid": testId = "empty-state",
}: EmptyStateProps) {
  return (
    <div
      data-testid={testId}
      className={cn(
        "flex flex-col items-center justify-center gap-3 px-6 py-12 text-center",
        className,
      )}
    >
      {Icon ? (
        <div className="rounded-full bg-muted p-3">
          <Icon className="size-6 text-muted-foreground" aria-hidden />
        </div>
      ) : null}
      <div className="space-y-1">
        <p className="text-body-lg font-medium">{title}</p>
        {/* max-w rather than a fixed width: the description is the part that
            varies most between locales, and a line length above ~70
            characters is measurably harder to scan back across. */}
        <p className="mx-auto max-w-prose text-body-sm text-muted-foreground">
          {description}
        </p>
      </div>
      {action || secondaryAction ? (
        <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
          {action}
          {secondaryAction}
        </div>
      ) : null}
    </div>
  );
}
