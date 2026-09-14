import { useCallback, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import {
  createEndpointApiV1WebhooksEndpointsPost,
  deleteEndpointApiV1WebhooksEndpointsEndpointIdDelete,
  listDeliveriesApiV1WebhooksEndpointsEndpointIdDeliveriesGet,
  listDeliveriesApiV1WebhooksEndpointsEndpointIdDeliveriesGetInfiniteQueryKey,
  listEndpointsApiV1WebhooksEndpointsGet,
  listEndpointsApiV1WebhooksEndpointsGetQueryKey,
  listEventTypesApiV1WebhooksEventTypesGet,
  listEventTypesApiV1WebhooksEventTypesGetQueryKey,
  replayDeliveryApiV1WebhooksDeliveriesDeliveryIdReplayPost,
  rotateSecretApiV1WebhooksEndpointsEndpointIdRotateSecretPost,
  updateEndpointApiV1WebhooksEndpointsEndpointIdPatch,
} from "@forge/api-client";
import type {
  WebhookDeliveryPublic,
  WebhookEndpointPublic,
} from "@forge/api-client";
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
  CardHeader,
  Checkbox,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Skeleton,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { VirtualList } from "../../components/VirtualList";
import { getErrorMessage } from "../auth/getErrorMessage";
import { CopyableSecret } from "./CopyableSecret";

/**
 * T-322: webhook endpoints, their delivery log, and replay.
 *
 * Three things here differ from the API-key page next door, and each is a
 * consequence of what a webhook is rather than a style choice:
 *
 * **The delivery log *is* virtualized.** It is the longest list in the
 * product — one row per event per endpoint — and it is paged by keyset
 * (`after` carries the previous page's last id), never OFFSET. The endpoint
 * list above it is not: the backend caps an organization at
 * `MAX_ENDPOINTS_PER_ORG`, so it has a small known ceiling, the same
 * argument the API-key table makes.
 *
 * **The event picker renders from the server's registry**, fetched from
 * `/webhooks/event-types`, not from a list in this file. A product that adds
 * an event gets a checkbox for it without touching the UI, which is the
 * whole point of the registry being typed on the backend.
 *
 * **Rotation shows the deadline, not just the secret.** The previous secret
 * keeps working for a fixed overlap and every request in that window is
 * signed with both — a consumer that cannot see when that ends has to guess
 * when to finish deploying.
 */

// Mirrors app.modules.webhooks.service.MAX_ENDPOINTS_PER_ORG.
const MAX_ENDPOINTS_PER_ORG = 20;
// Mirrors app.modules.webhooks.models.ROTATION_OVERLAP_HOURS.
const ROTATION_OVERLAP_HOURS = 24;

const DELIVERY_ROW_HEIGHT_PX = 56;
const DELIVERY_LIST_MAX_HEIGHT_PX = 420;

const DELIVERY_STATUSES = ["pending", "delivered", "failed", "dead"] as const;
type DeliveryStatus = (typeof DELIVERY_STATUSES)[number];

const DELIVERY_STATUS_LABEL_KEYS: Record<DeliveryStatus, string> = {
  pending: "tenancy.webhooks.delivery_status_pending",
  delivered: "tenancy.webhooks.delivery_status_delivered",
  failed: "tenancy.webhooks.delivery_status_failed",
  dead: "tenancy.webhooks.delivery_status_dead",
};

// `status` arrives as a plain string (the column is a varchar, deliberately
// -- adding a state to a Postgres ENUM is a migration with a lock on the
// biggest table in the product), so the lookup is widened rather than cast:
// a status this build has never heard of renders as "pending" instead of as
// a raw translation key.
function deliveryStatusLabelKey(status: string): string {
  const keys: Record<string, string | undefined> = DELIVERY_STATUS_LABEL_KEYS;
  return keys[status] ?? DELIVERY_STATUS_LABEL_KEYS.pending;
}

const createSchema = z.object({
  url: z.string().url("validation.url_invalid"),
  description: z.string().max(500),
  eventTypes: z.array(z.string()).min(1, "tenancy.webhooks.error_no_events"),
});
type CreateInput = z.infer<typeof createSchema>;

function EndpointForm({
  eventTypes,
  onCreated,
}: {
  eventTypes: { name: string; description: string }[];
  onCreated: (secret: string) => void;
}) {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);

  const createEndpoint = useMutation({
    mutationFn: async (input: CreateInput) => {
      const { data } = await createEndpointApiV1WebhooksEndpointsPost({
        body: {
          url: input.url,
          description: input.description || null,
          event_types: [...input.eventTypes],
        },
        throwOnError: true,
      });
      return data;
    },
  });

  const form = useForm<CreateInput>({
    resolver: zodResolver(createSchema),
    defaultValues: { url: "", description: "", eventTypes: [] },
  });

  const onSubmit = async (values: CreateInput): Promise<void> => {
    setFormError(null);
    try {
      const created = await createEndpoint.mutateAsync(values);
      onCreated(created.data?.secret ?? "");
    } catch (error) {
      setFormError(getErrorMessage(error, "tenancy.webhooks.error_create"));
    }
  };

  return (
    <Form {...form}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          void form.handleSubmit(onSubmit)(event);
        }}
        noValidate
      >
        {formError ? (
          <Alert variant="destructive">
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}

        <FormField
          control={form.control}
          name="url"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("tenancy.webhooks.field_url")}</FormLabel>
              <FormControl>
                <Input
                  placeholder={t("tenancy.webhooks.field_url_placeholder")}
                  data-testid="webhook-url-input"
                  {...field}
                />
              </FormControl>
              <p className="text-xs text-muted-foreground">
                {t("tenancy.webhooks.field_url_hint")}
              </p>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="description"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("tenancy.webhooks.field_description")}</FormLabel>
              <FormControl>
                <Input
                  placeholder={t("tenancy.webhooks.field_description_placeholder")}
                  data-testid="webhook-description-input"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        {/* `field.value`, not `form.watch` -- see ApiKeysPage for why. */}
        <FormField
          control={form.control}
          name="eventTypes"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("tenancy.webhooks.field_events")}</FormLabel>
              <div className="max-h-56 space-y-2 overflow-y-auto">
                {eventTypes.map((event) => (
                  <label
                    key={event.name}
                    className="flex items-start gap-2 text-sm"
                    htmlFor={`webhook-event-${event.name}`}
                  >
                    <Checkbox
                      id={`webhook-event-${event.name}`}
                      data-testid={`webhook-event-${event.name}`}
                      checked={field.value.includes(event.name)}
                      onCheckedChange={(checked) => {
                        field.onChange(
                          checked === true
                            ? [...field.value, event.name]
                            : field.value.filter(
                                (value) => value !== event.name,
                              ),
                        );
                      }}
                    />
                    <span>
                      <span className="font-mono text-xs">{event.name}</span>
                      <span className="block text-xs text-muted-foreground">
                        {event.description}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                {t("tenancy.webhooks.field_events_hint")}
              </p>
              <FormMessage />
            </FormItem>
          )}
        />

        <Button
          type="submit"
          className="w-full"
          disabled={form.formState.isSubmitting}
          data-testid="webhook-submit"
        >
          {t("tenancy.webhooks.submit")}
        </Button>
      </form>
    </Form>
  );
}

function TypedConfirmDialog({
  title,
  description,
  word,
  submitLabel,
  testIdPrefix,
  onCancel,
  onConfirm,
}: {
  title: string;
  description: string;
  word: string;
  submitLabel: string;
  testIdPrefix: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState("");

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">{description}</p>
          <label className="block space-y-1 text-sm" htmlFor={`${testIdPrefix}-input`}>
            <span>{t("tenancy.webhooks.delete_confirm_label", { word })}</span>
            <Input
              id={`${testIdPrefix}-input`}
              data-testid={`${testIdPrefix}-input`}
              value={typed}
              onChange={(event) => {
                setTyped(event.target.value);
              }}
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onCancel}>
              {t("tenancy.webhooks.cancel")}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={typed !== word}
              onClick={onConfirm}
              data-testid={`${testIdPrefix}-submit`}
            >
              {submitLabel}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function DeliveryLog({ endpoint }: { endpoint: WebhookEndpointPublic }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<DeliveryStatus | "">("");
  const [viewing, setViewing] = useState<WebhookDeliveryPublic | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [replayed, setReplayed] = useState(false);

  // The filter is folded into the query *key*, not only into the request:
  // switching it has to start a fresh keyset page rather than append to the
  // one already loaded under the previous filter. Spread rather than
  // `query: status ? {...} : undefined` -- `exactOptionalPropertyTypes` is
  // on, so an explicit `undefined` is not the same as an absent property.
  const queryFilter = status ? { status } : {};
  const queryKey =
    listDeliveriesApiV1WebhooksEndpointsEndpointIdDeliveriesGetInfiniteQueryKey({
      path: { endpoint_id: endpoint.id },
      query: queryFilter,
    });

  const deliveriesQuery = useInfiniteQuery({
    queryKey,
    queryFn: async ({ pageParam }) => {
      const { data } =
        await listDeliveriesApiV1WebhooksEndpointsEndpointIdDeliveriesGet({
          path: { endpoint_id: endpoint.id },
          query: { after: pageParam, limit: 20, ...queryFilter },
          throwOnError: true,
        });
      return data;
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.meta.next_cursor ?? null,
  });

  const replay = useMutation({
    mutationFn: async (deliveryId: string) => {
      await replayDeliveryApiV1WebhooksDeliveriesDeliveryIdReplayPost({
        path: { delivery_id: deliveryId },
        throwOnError: true,
      });
    },
  });

  const handleReplay = async (deliveryId: string): Promise<void> => {
    setActionError(null);
    setReplayed(false);
    try {
      await replay.mutateAsync(deliveryId);
      setReplayed(true);
      await queryClient.invalidateQueries({ queryKey });
    } catch (error) {
      setActionError(getErrorMessage(error, "tenancy.webhooks.error_replay"));
    }
  };

  const deliveries =
    deliveriesQuery.data?.pages.flatMap((page) => page.data ?? []) ?? [];

  const { fetchNextPage } = deliveriesQuery;
  const loadMore = useCallback(() => {
    void fetchNextPage();
  }, [fetchNextPage]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label className="text-sm text-muted-foreground" htmlFor="delivery-status-filter">
          {t("tenancy.webhooks.delivery_filter")}
        </label>
        <select
          id="delivery-status-filter"
          data-testid="delivery-status-filter"
          className="h-9 rounded-md border border-input bg-background px-3 text-sm shadow-sm"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as DeliveryStatus | "");
          }}
        >
          <option value="">{t("tenancy.webhooks.delivery_filter_all")}</option>
          {DELIVERY_STATUSES.map((value) => (
            <option key={value} value={value}>
              {t(DELIVERY_STATUS_LABEL_KEYS[value])}
            </option>
          ))}
        </select>
      </div>

      {actionError ? (
        <Alert variant="destructive">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      ) : null}
      {replayed ? (
        <Alert>
          <AlertDescription data-testid="delivery-replayed">
            {t("tenancy.webhooks.replayed")}
          </AlertDescription>
        </Alert>
      ) : null}

      {deliveriesQuery.isPending ? (
        <Skeleton className="h-24 w-full" />
      ) : deliveries.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="deliveries-empty">
          {t("tenancy.webhooks.empty_deliveries")}
        </p>
      ) : (
        <VirtualList
          items={deliveries}
          getItemKey={(delivery) => delivery.id}
          label={t("tenancy.webhooks.deliveries")}
          estimateSize={DELIVERY_ROW_HEIGHT_PX}
          maxHeight={DELIVERY_LIST_MAX_HEIGHT_PX}
          hasMore={deliveriesQuery.hasNextPage}
          isLoadingMore={deliveriesQuery.isFetchingNextPage}
          onLoadMore={loadMore}
          itemTestId="delivery-row"
          // No `scrollRestorationId`, unlike the member list: restoring a
          // scroll offset needs the *route* to read the saved entry through
          // `useElementScrollRestoration` and hand it back as `initialOffset`,
          // and this log lives inside a section that is collapsed on arrival
          // — so there would be nothing to restore into. Half-wiring it would
          // snapshot offsets nobody ever reads.
          renderItem={(delivery) => (
            <div className="flex items-center justify-between gap-3 border-b px-2 py-2 text-sm">
              <span className="font-mono text-xs">{delivery.event_type}</span>
              <Badge variant={delivery.status === "delivered" ? "default" : "secondary"}>
                {t(deliveryStatusLabelKey(delivery.status))}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {delivery.response_code ?? "—"}
              </span>
              <span className="text-xs text-muted-foreground">
                {formatDate(delivery.created_at, i18n.language)}
              </span>
              <span className="flex gap-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setViewing(delivery);
                  }}
                  data-testid={`delivery-view-${delivery.id}`}
                >
                  {t("tenancy.webhooks.view")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  title={t("tenancy.webhooks.replay_hint")}
                  onClick={() => {
                    void handleReplay(delivery.id);
                  }}
                  data-testid={`delivery-replay-${delivery.id}`}
                >
                  {t("tenancy.webhooks.replay")}
                </Button>
              </span>
            </div>
          )}
        />
      )}

      <Dialog
        open={viewing !== null}
        onOpenChange={(open) => {
          if (!open) setViewing(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("tenancy.webhooks.payload_title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <pre
              className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs"
              data-testid="delivery-payload"
            >
              {JSON.stringify(viewing?.payload ?? {}, null, 2)}
            </pre>
            <h3 className="text-sm font-medium">
              {t("tenancy.webhooks.response_title")}
            </h3>
            <pre
              className="max-h-40 overflow-auto rounded-md bg-muted p-3 text-xs"
              data-testid="delivery-response"
            >
              {viewing?.response_body ?? t("tenancy.webhooks.no_response")}
            </pre>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface RotatedSecret {
  secret: string;
  expiresAt: string;
}

export function WebhooksPage() {
  const { t, i18n } = useTranslation();
  const { org, isLoading: orgLoading } = useOrg();
  // See ApiKeysPage: the nav entry's permission gate is not the page's.
  const canManage = usePermission(Permission.SETTINGS_MANAGE);
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  // Both of these hold a credential in component state only, never in the
  // react-query cache — see ApiKeysPage's module docstring.
  const [newSecret, setNewSecret] = useState<string | null>(null);
  const [rotated, setRotated] = useState<RotatedSecret | null>(null);
  const [deleting, setDeleting] = useState<WebhookEndpointPublic | null>(null);
  const [openLog, setOpenLog] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const endpointsQuery = useQuery({
    queryKey: listEndpointsApiV1WebhooksEndpointsGetQueryKey(),
    queryFn: async () => {
      const { data } = await listEndpointsApiV1WebhooksEndpointsGet({
        query: { limit: MAX_ENDPOINTS_PER_ORG },
        throwOnError: true,
      });
      return data;
    },
    enabled: Boolean(org) && canManage,
  });

  const eventTypesQuery = useQuery({
    queryKey: listEventTypesApiV1WebhooksEventTypesGetQueryKey(),
    queryFn: async () => {
      const { data } = await listEventTypesApiV1WebhooksEventTypesGet({
        throwOnError: true,
      });
      return data;
    },
    enabled: Boolean(org) && canManage,
  });

  const toggleEnabled = useMutation({
    mutationFn: async (input: { endpointId: string; enabled: boolean }) => {
      await updateEndpointApiV1WebhooksEndpointsEndpointIdPatch({
        path: { endpoint_id: input.endpointId },
        body: { enabled: input.enabled },
        throwOnError: true,
      });
    },
  });

  const rotateSecret = useMutation({
    mutationFn: async (endpointId: string) => {
      const { data } =
        await rotateSecretApiV1WebhooksEndpointsEndpointIdRotateSecretPost({
          path: { endpoint_id: endpointId },
          throwOnError: true,
        });
      return data;
    },
  });

  const deleteEndpoint = useMutation({
    mutationFn: async (endpointId: string) => {
      await deleteEndpointApiV1WebhooksEndpointsEndpointIdDelete({
        path: { endpoint_id: endpointId },
        throwOnError: true,
      });
    },
  });

  const invalidate = async (): Promise<void> => {
    await queryClient.invalidateQueries({
      queryKey: listEndpointsApiV1WebhooksEndpointsGetQueryKey(),
    });
  };

  const handleToggle = async (endpointId: string, enabled: boolean): Promise<void> => {
    setActionError(null);
    try {
      await toggleEnabled.mutateAsync({ endpointId, enabled });
      await invalidate();
    } catch (error) {
      setActionError(getErrorMessage(error, "tenancy.webhooks.error_update"));
    }
  };

  const handleRotate = async (endpointId: string): Promise<void> => {
    setActionError(null);
    try {
      const result = await rotateSecret.mutateAsync(endpointId);
      setRotated({
        secret: result.data?.secret ?? "",
        expiresAt: result.data?.previous_secret_expires_at ?? "",
      });
      await invalidate();
    } catch (error) {
      setActionError(getErrorMessage(error, "tenancy.webhooks.error_rotate"));
    }
  };

  const handleDelete = async (endpointId: string): Promise<void> => {
    setActionError(null);
    try {
      await deleteEndpoint.mutateAsync(endpointId);
      await invalidate();
    } catch (error) {
      setActionError(getErrorMessage(error, "tenancy.webhooks.error_delete"));
    } finally {
      setDeleting(null);
    }
  };

  if (orgLoading || !org) {
    return (
      <div className="max-w-4xl space-y-6 p-6">
        <h1 className="text-xl font-semibold" data-testid="page-title">{t("tenancy.webhooks.title")}</h1>
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (!canManage) {
    return (
      <div className="max-w-4xl space-y-6 p-6" data-testid="webhooks-page">
        <h1 className="text-xl font-semibold" data-testid="page-title">{t("tenancy.webhooks.title")}</h1>
        <Alert>
          <AlertDescription data-testid="webhooks-permission-denied">
            {t("common.error.permission_denied")}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const endpoints = endpointsQuery.data?.data ?? [];
  const eventTypes = eventTypesQuery.data?.data ?? [];

  return (
    <div className="max-w-4xl space-y-6 p-6" data-testid="webhooks-page">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold" data-testid="page-title">{t("tenancy.webhooks.title")}</h1>
        <Button
          type="button"
          disabled={endpoints.length >= MAX_ENDPOINTS_PER_ORG}
          onClick={() => {
            setCreateOpen(true);
          }}
          data-testid="webhooks-create-button"
        >
          {t("tenancy.webhooks.create")}
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardDescription>{t("tenancy.webhooks.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {actionError ? (
            <Alert variant="destructive">
              <AlertDescription>{actionError}</AlertDescription>
            </Alert>
          ) : null}
          {endpointsQuery.isError ? (
            <Alert variant="destructive">
              <AlertDescription>{t("tenancy.webhooks.error_load")}</AlertDescription>
            </Alert>
          ) : null}

          {endpointsQuery.isPending ? (
            <Skeleton className="h-24 w-full" />
          ) : endpoints.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="webhooks-empty">
              {t("tenancy.webhooks.empty")}
            </p>
          ) : (
            <ul className="space-y-4">
              {endpoints.map((endpoint) => (
                <li
                  key={endpoint.id}
                  className="space-y-3 rounded-md border p-4"
                  data-testid={`webhook-row-${endpoint.id}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="break-all font-mono text-sm">{endpoint.url}</span>
                    <Badge variant={endpoint.enabled ? "default" : "secondary"}>
                      {endpoint.enabled
                        ? t("tenancy.webhooks.status_enabled")
                        : endpoint.disabled_at
                          ? t("tenancy.webhooks.status_auto_disabled", {
                              count: endpoint.consecutive_failures,
                            })
                          : t("tenancy.webhooks.status_disabled")}
                    </Badge>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {endpoint.event_types.map((event) => (
                      <Badge key={event} variant="secondary">
                        {event}
                      </Badge>
                    ))}
                  </div>
                  {endpoint.secret_previous_expires_at ? (
                    <p className="text-xs text-muted-foreground">
                      {t("tenancy.webhooks.rotated_warning", {
                        expires: formatDate(
                          endpoint.secret_previous_expires_at,
                          i18n.language,
                        ),
                      })}
                    </p>
                  ) : null}
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setOpenLog(openLog === endpoint.id ? null : endpoint.id);
                      }}
                      data-testid={`webhook-deliveries-${endpoint.id}`}
                    >
                      {t("tenancy.webhooks.deliveries")}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        void handleToggle(endpoint.id, !endpoint.enabled);
                      }}
                      data-testid={`webhook-toggle-${endpoint.id}`}
                    >
                      {endpoint.enabled
                        ? t("tenancy.webhooks.disable")
                        : t("tenancy.webhooks.enable")}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        void handleRotate(endpoint.id);
                      }}
                      data-testid={`webhook-rotate-${endpoint.id}`}
                    >
                      {t("tenancy.webhooks.rotate")}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setDeleting(endpoint);
                      }}
                      data-testid={`webhook-delete-${endpoint.id}`}
                    >
                      {t("tenancy.webhooks.delete")}
                    </Button>
                  </div>
                  {openLog === endpoint.id ? <DeliveryLog endpoint={endpoint} /> : null}
                </li>
              ))}
            </ul>
          )}

          <p className="text-xs text-muted-foreground">
            {t("tenancy.webhooks.limit_note", { limit: MAX_ENDPOINTS_PER_ORG })}
          </p>
        </CardContent>
      </Card>

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("tenancy.webhooks.create_title")}</DialogTitle>
          </DialogHeader>
          <EndpointForm
            eventTypes={eventTypes}
            onCreated={(secret) => {
              setCreateOpen(false);
              setNewSecret(secret);
              void invalidate();
            }}
          />
        </DialogContent>
      </Dialog>

      <Dialog
        open={newSecret !== null}
        onOpenChange={(open) => {
          if (!open) setNewSecret(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("tenancy.webhooks.secret_title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <Alert variant="destructive">
              <AlertDescription data-testid="webhook-secret-warning">
                {t("tenancy.webhooks.secret_warning")}
              </AlertDescription>
            </Alert>
            <CopyableSecret
              value={newSecret ?? ""}
              testId="webhook-secret-value"
              copyLabel={t("tenancy.webhooks.copy")}
              copiedLabel={t("tenancy.webhooks.copied")}
            />
            <div className="flex justify-end">
              <Button
                type="button"
                onClick={() => {
                  setNewSecret(null);
                }}
                data-testid="webhook-secret-done"
              >
                {t("tenancy.webhooks.done")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog
        open={rotated !== null}
        onOpenChange={(open) => {
          if (!open) setRotated(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("tenancy.webhooks.rotated_title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <Alert>
              <AlertDescription data-testid="webhook-rotated-warning">
                {t("tenancy.webhooks.rotated_warning", {
                  expires: rotated
                    ? formatDate(rotated.expiresAt, i18n.language)
                    : "",
                })}
              </AlertDescription>
            </Alert>
            <CopyableSecret
              value={rotated?.secret ?? ""}
              testId="webhook-rotated-value"
              copyLabel={t("tenancy.webhooks.copy")}
              copiedLabel={t("tenancy.webhooks.copied")}
            />
            <p className="text-xs text-muted-foreground">
              {t("tenancy.webhooks.rotate_description", {
                hours: ROTATION_OVERLAP_HOURS,
              })}
            </p>
            <div className="flex justify-end">
              <Button
                type="button"
                onClick={() => {
                  setRotated(null);
                }}
                data-testid="webhook-rotated-done"
              >
                {t("tenancy.webhooks.done")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {deleting ? (
        <TypedConfirmDialog
          title={t("tenancy.webhooks.delete_title")}
          description={t("tenancy.webhooks.delete_description", { url: deleting.url })}
          word={t("tenancy.webhooks.delete_confirm_word")}
          submitLabel={t("tenancy.webhooks.delete_submit")}
          testIdPrefix="webhook-delete-confirm"
          onCancel={() => {
            setDeleting(null);
          }}
          onConfirm={() => {
            void handleDelete(deleting.id);
          }}
        />
      ) : null}
    </div>
  );
}
