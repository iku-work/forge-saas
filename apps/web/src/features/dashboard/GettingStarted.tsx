import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button, Checklist, ChecklistStep, Skeleton } from "@forge/ui";

import { useRouteLocale } from "../../lib/useRouteLocale";
import type { GettingStartedState } from "./useGettingStarted";

/** The checklist itself. Renders nothing once every visible step is done. */
export function GettingStarted({
  state,
  footer,
}: {
  state: GettingStartedState;
  footer?: ReactNode;
}) {
  const { t } = useTranslation();
  const locale = useRouteLocale();
  const { isLoading, steps } = state;

  if (isLoading) {
    return (
      <div className="space-y-3" data-testid="getting-started-loading">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    );
  }

  const done = steps.filter((step) => step.done).length;
  if (done === steps.length) {
    return null;
  }
  const firstOpen = steps.find((step) => !step.done);

  return (
    <>
      <Checklist
        data-testid="getting-started"
        title={t("common.dashboard.checklist_title")}
        done={done}
        total={steps.length}
        progressLabel={t("common.dashboard.progress", {
          done,
          total: steps.length,
        })}
      >
        {steps.map((step) => (
          <ChecklistStep
            key={step.id}
            done={step.done}
            title={step.title}
            hint={step.hint}
            doneLabel={t("common.dashboard.step_done")}
            action={
              // The first open step is the primary action on the page; the
              // rest are secondary, so the eye has one place to land.
              <Button
                asChild
                variant={step === firstOpen ? "default" : "outline"}
              >
                <Link to={step.to} params={{ locale }}>
                  {step.actionLabel}
                </Link>
              </Button>
            }
          />
        ))}
      </Checklist>
      {footer}
    </>
  );
}
