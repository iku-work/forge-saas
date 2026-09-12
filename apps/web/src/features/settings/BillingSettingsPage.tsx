import { useState } from "react";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  createCheckoutSessionApiV1BillingCheckoutSessionPost,
  createPortalSessionApiV1BillingPortalSessionPost,
  getSubscriptionApiV1BillingSubscriptionGet,
  getSubscriptionApiV1BillingSubscriptionGetQueryKey,
  getUsageApiV1BillingUsageGet,
  getUsageApiV1BillingUsageGetQueryKey,
  listInvoicesApiV1BillingInvoicesGet,
  listInvoicesApiV1BillingInvoicesGetInfiniteQueryKey,
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { getErrorMessage } from "../auth/getErrorMessage";

// Plans are USD-denominated at the data-model level (no currency field on
// PlanPublic) -- the locale still governs digit grouping/decimal separator
// and symbol placement (e.g. "$10.00" vs "10,00 $"), so it's threaded
// through rather than hardcoded as the implicit en-US formatting `$${...}`
// used to produce.
function formatPrice(cents: number, currency: string, locale: string): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: currency.toUpperCase(),
  }).format(cents / 100);
}

// An InvoiceDTO carries its own Stripe `currency` -- lowercase per Stripe's
// convention (Intl.NumberFormat requires the uppercase ISO 4217 code). So
// does Plan, since 2026-09-10: before that this whole file assumed USD, and
// a EUR catalogue rendered "$49" for a price Stripe charged in euros.
function formatInvoiceAmount(
  cents: number,
  currency: string,
  locale: string,
): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: currency.toUpperCase(),
  }).format(cents / 100);
}

// Usage prices are stored in micros (1,000,000 micros = one major unit, see
// Plan.usage_prices_micros / docs/runbooks/stripe-usage-pricing.md) rather
// than cents -- a plain cents field can't represent sub-cent per-unit
// pricing like 0.0001 per API call. The currency is the plan's, same as
// formatPrice: these amounts are denominated by the plan that priced them.
function formatMicros(
  micros: number,
  currency: string,
  locale: string,
): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: currency.toUpperCase(),
    maximumFractionDigits: 6,
  }).format(micros / 1_000_000);
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

/**
 * The Stripe portal URL a 409 from POST /billing/checkout-session carries in
 * `error.details`, or null for any other failure.
 *
 * Read defensively rather than through a generated type: hey-api only types
 * the responses FastAPI documents, so non-422 error bodies are `unknown`
 * here -- the same reason getErrorMessage sniffs the envelope by hand.
 */
function portalUrlFromError(error: unknown): string | null {
  if (typeof error !== "object" || error === null || !("error" in error)) {
    return null;
  }
  const details = (
    error as { error?: { details?: { portal_url?: unknown } | null } | null }
  ).error?.details;
  const url = details?.portal_url;
  return typeof url === "string" && url.length > 0 ? url : null;
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
  const usageQuery = useQuery({
    queryKey: getUsageApiV1BillingUsageGetQueryKey(),
    queryFn: async () => {
      const { data } = await getUsageApiV1BillingUsageGet({
        throwOnError: true,
      });
      return data;
    },
    enabled: Boolean(org) && canRead,
  });
  // Raw SDK call rather than the generated *GetInfiniteOptions() helper --
  // see MembersSettingsPage.tsx for why. The query key function alone is
  // fine to reuse since it doesn't make a network call.
  const invoicesQuery = useInfiniteQuery({
    queryKey: listInvoicesApiV1BillingInvoicesGetInfiniteQueryKey(),
    queryFn: async ({ pageParam }: { pageParam: string | null }) => {
      const { data } = await listInvoicesApiV1BillingInvoicesGet({
        query: { after: pageParam, limit: 20 },
        throwOnError: true,
      });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
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
  // The step-up gate lives on this one, not on `checkout` above -- it moved
  // there on 2026-09-10, because interrupting an upgrade to demand a fresh
  // sign-in cost conversions and protected nothing (a stolen session reaching
  // checkout only earns a Stripe URL the attacker must put their own card
  // into). The portal is the surface worth protecting: payment instrument,
  // invoice history, cancellation.
  //
  // So a session idling past five minutes surfaces here as
  // errors.step_up_auth_required, shown through getErrorMessage's normal
  // code->copy mapping like any other error rather than a dedicated re-auth
  // prompt -- the same convention SecuritySettingsPage's change-email flow
  // already follows.
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
  const usage = usageQuery.data?.data ?? [];
  const invoices =
    invoicesQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];
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
      // The org already pays for something, so the server refused to open a
      // second checkout (two live subscriptions on one org happened in
      // production) and handed back the portal instead. Following it is the
      // whole point of sending it: the customer pressed a plan button, and
      // the portal is where Stripe lets an existing customer change plan.
      // Showing "you already have a subscription" and stopping would leave
      // them on the one page where they came to spend money with no way to.
      const portalUrl = portalUrlFromError(error);
      if (portalUrl) {
        redirectTo(portalUrl);
        return;
      }
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
              <Badge data-testid="billing-current-plan">
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

      {usageQuery.isLoading || usageQuery.isError || usage.length > 0 ? (
        <Card>
          <CardHeader>
            <CardDescription>{t("billing.usage.title")}</CardDescription>
          </CardHeader>
          <CardContent>
            {usageQuery.isLoading ? (
              <Skeleton className="h-16 w-full" />
            ) : usageQuery.isError ? (
              <Alert variant="destructive">
                <AlertDescription>
                  {getErrorMessage(
                    usageQuery.error,
                    "billing.usage.error_load",
                  )}
                </AlertDescription>
              </Alert>
            ) : (
              <Table data-testid="billing-usage-table">
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("billing.usage.metric_col")}</TableHead>
                    <TableHead>{t("billing.usage.quantity_col")}</TableHead>
                    <TableHead className="text-end">
                      {t("billing.usage.estimated_cost_col")}
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {usage.map((item) => (
                    <TableRow key={item.metric}>
                      <TableCell>{item.metric}</TableCell>
                      <TableCell>{item.quantity}</TableCell>
                      <TableCell className="text-end">
                        {item.estimated_cost_micros === null ? (
                          <span className="text-muted-foreground">
                            {t("billing.usage.no_price_configured")}
                          </span>
                        ) : (
                          formatMicros(
                            item.estimated_cost_micros,
                            // The plan that priced this usage. Falls back to
                            // the catalogue's first plan when the org has no
                            // subscription -- there is no metered usage in
                            // that state, so the value is never rendered.
                            currentPlan?.currency ??
                              plans[0]?.currency ??
                              "usd",
                            i18n.language,
                          )
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      ) : null}

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
                      {formatPrice(
                        plan.price_monthly,
                        plan.currency,
                        i18n.language,
                      )}
                      {t("billing.available_plans.per_month")}
                    </p>
                  </div>
                  <Button
                    type="button"
                    disabled={plan.id === currentPlan?.id || checkout.isPending}
                    onClick={() => {
                      void handleUpgrade(plan.slug);
                    }}
                    data-testid={`billing-upgrade-${plan.slug}`}
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
        <CardContent className="space-y-4">
          {invoicesQuery.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : invoicesQuery.isError ? (
            <Alert variant="destructive">
              <AlertDescription>
                {getErrorMessage(
                  invoicesQuery.error,
                  "billing.invoices.error_load",
                )}
              </AlertDescription>
            </Alert>
          ) : invoices.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("billing.invoices.not_available")}
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("billing.invoices.date_col")}</TableHead>
                  <TableHead>{t("billing.invoices.amount_col")}</TableHead>
                  <TableHead>{t("billing.invoices.status_col")}</TableHead>
                  <TableHead className="text-end">
                    {t("billing.invoices.pdf_col")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invoices.map((invoice) => (
                  <TableRow key={invoice.id}>
                    <TableCell>
                      {formatDate(invoice.created_at, i18n.language)}
                    </TableCell>
                    <TableCell>
                      {formatInvoiceAmount(
                        invoice.amount_paid,
                        invoice.currency,
                        i18n.language,
                      )}
                    </TableCell>
                    <TableCell>
                      {invoice.status ? (
                        <Badge variant="secondary">{invoice.status}</Badge>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-end">
                      {invoice.invoice_pdf ? (
                        <a
                          href={invoice.invoice_pdf}
                          target="_blank"
                          rel="noreferrer"
                          className="text-sm underline underline-offset-4"
                        >
                          {t("billing.invoices.download_pdf")}
                        </a>
                      ) : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {invoicesQuery.hasNextPage ? (
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                void invoicesQuery.fetchNextPage();
              }}
              disabled={invoicesQuery.isFetchingNextPage}
            >
              {t("billing.invoices.load_more")}
            </Button>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
