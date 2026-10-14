import type { HTMLAttributes, ReactNode } from "react";
import { Check } from "lucide-react";

import { cn } from "@forge/utils";

interface ChecklistProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title: ReactNode;
  /** Steps done, and of how many -- drives the bar and its value. */
  done: number;
  total: number;
  /** Visible progress text and the progress bar's accessible name,
   *  e.g. "1 of 4 done". */
  progressLabel: string;
}

/**
 * A short list of first steps (the dashboard's "Get started"): a title,
 * progress as text and as a bar, then an ordered list of steps. Each step
 * says when it is done; the caller decides what "done" means.
 */
function Checklist({
  title,
  done,
  total,
  progressLabel,
  children,
  className,
  ...props
}: ChecklistProps) {
  const percent = total === 0 ? 0 : Math.round((done / total) * 100);
  return (
    // A container, so each step can put its button under its text when the
    // list itself is narrow (a phone, or the marketing hero's small frame).
    <section
      className={cn("@container/checklist w-full", className)}
      {...props}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-4 pb-3.5">
        <h2 className="text-[0.9375rem] font-semibold">{title}</h2>
        <div className="flex items-center gap-3 text-[0.8125rem] text-muted-foreground">
          <span aria-hidden="true">{progressLabel}</span>
          <div
            role="progressbar"
            aria-label={progressLabel}
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={done}
            className="h-1 w-30 overflow-hidden rounded-full bg-surface-sunken"
          >
            <div
              className="h-full rounded-full bg-primary transition-[width] duration-[var(--duration-base)]"
              style={{ width: `${String(percent)}%` }}
            />
          </div>
        </div>
      </div>
      <ol className="border-t border-border">{children}</ol>
    </section>
  );
}

interface ChecklistStepProps extends Omit<
  HTMLAttributes<HTMLLIElement>,
  "title"
> {
  title: ReactNode;
  hint?: ReactNode;
  done: boolean;
  /** Shown instead of the action once the step is done, e.g. "Done". */
  doneLabel: ReactNode;
  /** The one thing to do for this step (usually a button or link). */
  action?: ReactNode;
}

function ChecklistStep({
  title,
  hint,
  done,
  doneLabel,
  action,
  className,
  ...props
}: ChecklistStepProps) {
  return (
    <li
      data-done={done ? "" : undefined}
      className={cn(
        "grid grid-cols-[1.5rem_minmax(0,1fr)] items-start gap-x-4 gap-y-3 border-b border-border py-4.5 @md/checklist:grid-cols-[1.5rem_minmax(0,1fr)_auto] @md/checklist:items-center",
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className={cn(
          "mt-0.5 grid size-[1.375rem] place-items-center rounded-full @md/checklist:mt-0",
          done
            ? "bg-primary text-primary-foreground"
            : "ring-[1.5px] ring-inset ring-border-strong",
        )}
      >
        {done ? <Check className="size-3.5 [stroke-width:2.25]" /> : null}
      </span>
      <div className="min-w-0">
        <p
          className={cn(
            "font-medium",
            done &&
              "text-muted-foreground line-through decoration-border-strong",
          )}
        >
          {title}
        </p>
        {hint ? (
          <p className="text-[0.8125rem] text-muted-foreground">{hint}</p>
        ) : null}
      </div>
      <div className="col-start-2 justify-self-start @md/checklist:col-start-auto @md/checklist:justify-self-end">
        {done ? (
          <span className="text-[0.8125rem] text-muted-foreground">
            {doneLabel}
          </span>
        ) : (
          action
        )}
      </div>
    </li>
  );
}

export { Checklist, ChecklistStep };
export type { ChecklistProps, ChecklistStepProps };
