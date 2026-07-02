import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  createCheckoutSessionApiV1BillingCheckoutSessionPost,
  createPortalSessionApiV1BillingPortalSessionPost,
  getSubscriptionApiV1BillingSubscriptionGet,
  getSubscriptionApiV1BillingSubscriptionGetQueryKey,
  listPlansApiV1BillingPlansGet,
  listPlansApiV1BillingPlansGetQueryKey,
} from "@forge/api-client";
import type { CheckoutSessionRequest } from "@forge/api-client";
import { useOrg, usePermission } from "@forge/hooks";
import { Permission } from "@forge/types";
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  Skeleton,
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";

// Plans are USD-denominated at the data-model level (no currency field on
// PlanPublic) -- the locale still governs digit grouping/decimal separator
// and symbol placement (e.g. "$10.00" vs "10,00 $"), so it's threaded
// through rather than hardcoded as the implicit en-US formatting `$${...}`
// used to produce.
function formatPrice(cents: number, locale: string): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}

// A plain top-level function rather than inlining `window.location.href =
// url` at the call site -- the latter, written directly inside an async
// handler after an `await`, trips react-hooks' compiler-based
// "immutability" check (it conservatively treats values live across a
// suspend point as frozen). Isolating the assignment in its own
// non-component, non-hook function sidesteps that analysis entirely.
function redirectTo(url: string): void {
  window.location.href = url;
}

export function BillingSettingsPage() {
  const { t, i18n } = useTranslation();
  const { org, isLoading: orgLoading } = useOrg();
  const canManage = usePermission(Permission.BILLING_MANAGE);
  const canRead = usePermission(Permission.BILLING_READ);
  const [actionError, setActionError] = useState<string | null>(null);

  // Raw SDK calls in queryFn rather than the generated *GetOptions()
  // helpers -- see the matching comment in SecuritySettingsPage.tsx for why
  // (exactOptionalPropertyTypes rejects queryOptions()'s return shape here).
  const subscriptionQuery = useQuery({
    queryKey: getSubscriptionApiV1BillingSubscriptionGetQueryKey(),
    queryFn: async () => {
      const { data } = await getSubscriptionApiV1BillingSubscriptionGet({
        throwOnError: true,
      });
      return data;
    },
    enabled: Boolean(org) && canRead,
  });
  const plansQuery = useQuery({
    queryKey: listPlansApiV1BillingPlansGetQueryKey(),
    queryFn: async () => {
      const { data } = await listPlansApiV1BillingPlansGet({
        throwOnError: true,
      });
      return data;
    },
    enabled: Boolean(org) && canRead,
  });

  // Raw SDK calls rather than the generated *Mutation() helpers -- see the
  // comment above for why.
  const checkout = useMutation({
    mutationFn: async (input: CheckoutSessionRequest) => {
      const { data } =
        await createCheckoutSessionApiV1BillingCheckoutSessionPost({
          body: input,
          throwOnError: true,
        });
      return data;
    },
  });
  const portal = useMutation({
    mutationFn: async () => {
      const { data } = await createPortalSessionApiV1BillingPortalSessionPost({
        throwOnError: true,
      });
      return data;
    },
  });

  const subscription = subscriptionQuery.data?.data ?? null;
  const plans = plansQuery.data?.data ?? [];
  const currentPlan = subscription
    ? (plans.find((plan) => plan.id === subscription.plan_id) ?? null)
    : null;

  const handleUpgrade = async (planSlug: string): Promise<void> => {
    setActionError(null);
    try {
      const envelope = await checkout.mutateAsync({
        plan_slug: planSlug,
        interval: "monthly",
      });
      const url = envelope.data?.url;
      if (url) {
        redirectTo(url);
      }
    } catch (error) {
      setActionError(getErrorMessage(error, "billing.error.checkout_failed"));
    }
  };

  const handleManageBilling = async (): Promise<void> => {
    setActionError(null);
    try {
      const envelope = await portal.mutateAsync();
      const url = envelope.data?.url;
      if (url) {
        redirectTo(url);
      }
    } catch (error) {
      setActionError(getErrorMessage(error, "billing.error.portal_failed"));
    }
  };

  if (orgLoading || !org) {
    return (
      <div className="max-w-2xl space-y-6 p-6">
        <h1 className="text-xl font-semibold">{t("billing.title")}</h1>
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (!canRead) {
    return (
      <div className="max-w-2xl space-y-6 p-6">
        <h1 className="text-xl font-semibold">{t("billing.title")}</h1>
        <Alert>
          <AlertDescription>
            {t("billing.error.permission_denied")}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-6 p-6">
      <h1 className="text-xl font-semibold">{t("billing.title")}</h1>

      {actionError ? (
        <Alert variant="destructive">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardDescription>{t("billing.current_plan.title")}</CardDescription>
        </CardHeader>
        <CardContent>
          {subscriptionQuery.isLoading || plansQuery.isLoading ? (
            <Skeleton className="h-8 w-32" />
          ) : (
            <div className="flex items-center gap-3">
              <Badge>
                {currentPlan?.name ?? t("billing.current_plan.free_label")}
              </Badge>
              {subscription ? (
                <span className="text-sm text-muted-foreground">
                  {t("billing.current_plan.status", {
                    status: subscription.status,
                  })}
                </span>
              ) : null}
            </div>
          )}
        </CardContent>
        {canManage && subscription ? (
          <CardFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                void handleManageBilling();
              }}
              disabled={portal.isPending}
            >
              {t("billing.manage_button")}
            </Button>
          </CardFooter>
        ) : null}
      </Card>

      {canManage ? (
        <Card>
          <CardHeader>
            <CardDescription>
              {t("billing.available_plans.title")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {plansQuery.isLoading ? (
              <Skeleton className="h-16 w-full" />
            ) : plans.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t("billing.available_plans.no_plans")}
              </p>
            ) : (
              plans.map((plan) => (
                <div
                  key={plan.id}
                  className="flex items-center justify-between rounded-md border p-3"
                >
                  <div>
                    <p className="font-medium">{plan.name}</p>
                    <p className="text-sm text-muted-foreground">
                      {formatPrice(plan.price_monthly, i18n.language)}
                      {t("billing.available_plans.per_month")}
                    </p>
                  </div>
                  <Button
                    type="button"
                    disabled={plan.id === currentPlan?.id || checkout.isPending}
                    onClick={() => {
                      void handleUpgrade(plan.slug);
                    }}
                  >
                    {plan.id === currentPlan?.id
                      ? t("billing.current_plan.badge")
                      : t("billing.upgrade_button")}
                  </Button>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardDescription>{t("billing.invoices.title")}</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            {t("billing.invoices.not_available")}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
