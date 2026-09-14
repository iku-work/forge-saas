import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import {
  createApiKeyApiV1ApiKeysPost,
  listApiKeysApiV1ApiKeysGet,
  listApiKeysApiV1ApiKeysGetQueryKey,
  revokeApiKeyApiV1ApiKeysKeyIdRevokePost,
} from "@forge/api-client";
import type { ApiKeyPublic } from "@forge/api-client";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { getErrorMessage } from "../auth/getErrorMessage";
import { CopyableSecret } from "./CopyableSecret";

/**
 * T-313: the API-key management surface.
 *
 * Two decisions worth reading before changing anything here.
 *
 * **The secret lives in component state and nowhere else.** `createKey`
 * below is a plain `useMutation` whose result is copied into `newKey` and
 * then dropped; it is never put into the react-query cache, never a query,
 * and never re-fetched — because the cache outlives this component and would
 * hand the credential back on a later render of a page nobody expected to
 * show it. The backend only ever returns it once anyway (see
 * `ApiKeyCreated`), so the cache would be holding the *only* copy.
 *
 * **No `VirtualList` here, deliberately.** T-241 virtualizes every long list
 * in the product, and this one is not one: the backend caps an organization
 * at `MAX_KEYS_PER_ORG` (50) and refuses the 51st with a 402, so the table
 * has a known, small ceiling. A virtualizer would add a scroll port, a row
 * estimate and three test stubs to a list that fits on a screen and a half.
 */

// Mirrors app.modules.api_keys.service.MAX_KEYS_PER_ORG. Rendered as a hint
// rather than enforced here — the server is what refuses the 51st key, and a
// cap the client alone knew about would not be one.
const MAX_KEYS_PER_ORG = 50;

// Mirrors app.modules.api_keys.scopes.Scope. A closed vocabulary in code on
// both sides: an unknown scope is a 422 from the service, so a checkbox for
// one that has been removed cannot silently mint a key that grants nothing.
const SCOPES = [
  "orgs:read",
  "members:read",
  "members:write",
  "files:read",
] as const;
type Scope = (typeof SCOPES)[number];

const SCOPE_LABEL_KEYS: Record<Scope, string> = {
  "orgs:read": "tenancy.api_keys.scope_orgs_read",
  "members:read": "tenancy.api_keys.scope_members_read",
  "members:write": "tenancy.api_keys.scope_members_write",
  "files:read": "tenancy.api_keys.scope_files_read",
};

const createSchema = z.object({
  name: z.string().min(1, "validation.required").max(100),
  scopes: z.array(z.enum(SCOPES)).min(1, "tenancy.api_keys.error_no_scopes"),
  // A plain `<input type="date">` value, or "" for "never expires". Kept as a
  // string all the way to the request so the empty case stays distinguishable
  // from a date the browser could not parse.
  expiresAt: z.string(),
});
type CreateInput = z.infer<typeof createSchema>;

type KeyStatus = "active" | "revoked" | "expired";

function statusOf(key: ApiKeyPublic): KeyStatus {
  if (key.revoked_at) return "revoked";
  if (key.expires_at && new Date(key.expires_at) <= new Date()) return "expired";
  return "active";
}

const STATUS_LABEL_KEYS: Record<KeyStatus, string> = {
  active: "tenancy.api_keys.status_active",
  revoked: "tenancy.api_keys.status_revoked",
  expired: "tenancy.api_keys.status_expired",
};

function CreateKeyForm({
  onCreated,
}: {
  onCreated: (key: string) => void;
}) {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);

  // Raw SDK call rather than the generated *Mutation() helper — see
  // CreateOrgForm.tsx for why.
  const createKey = useMutation({
    mutationFn: async (input: CreateInput) => {
      const { data } = await createApiKeyApiV1ApiKeysPost({
        body: {
          name: input.name,
          scopes: [...input.scopes],
          // The date input gives a local calendar day; the API wants an
          // instant. End of that day in the browser's zone is the reading a
          // person means by "expires on the 14th".
          expires_at: input.expiresAt
            ? new Date(`${input.expiresAt}T23:59:59`).toISOString()
            : null,
        },
        throwOnError: true,
      });
      return data;
    },
  });

  const form = useForm<CreateInput>({
    resolver: zodResolver(createSchema),
    defaultValues: { name: "", scopes: [], expiresAt: "" },
  });

  const onSubmit = async (values: CreateInput): Promise<void> => {
    setFormError(null);
    try {
      const created = await createKey.mutateAsync(values);
      // Straight to the caller's state. Never `queryClient.setQueryData` —
      // see the module docstring.
      onCreated(created.data?.key ?? "");
    } catch (error) {
      setFormError(getErrorMessage(error, "tenancy.api_keys.error_create"));
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
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("tenancy.api_keys.field_name")}</FormLabel>
              <FormControl>
                <Input
                  placeholder={t("tenancy.api_keys.field_name_placeholder")}
                  data-testid="api-key-name-input"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        {/* `field.value` rather than `form.watch("scopes")`: watch() returns a
            subscription the React Compiler cannot memoize, which downgrades
            the whole component to "compilation skipped". The field render
            prop already re-renders on change and needs no subscription. */}
        <FormField
          control={form.control}
          name="scopes"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("tenancy.api_keys.field_scopes")}</FormLabel>
              <div className="space-y-2">
                {SCOPES.map((scope) => (
                  <label
                    key={scope}
                    className="flex items-center gap-2 text-sm"
                    htmlFor={`api-key-scope-${scope}`}
                  >
                    <Checkbox
                      id={`api-key-scope-${scope}`}
                      data-testid={`api-key-scope-${scope}`}
                      checked={field.value.includes(scope)}
                      onCheckedChange={(checked) => {
                        field.onChange(
                          checked === true
                            ? [...field.value, scope]
                            : field.value.filter((value) => value !== scope),
                        );
                      }}
                    />
                    {t(SCOPE_LABEL_KEYS[scope])}
                  </label>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                {t("tenancy.api_keys.field_scopes_hint")}
              </p>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="expiresAt"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t("tenancy.api_keys.field_expires")}</FormLabel>
              <FormControl>
                <Input type="date" data-testid="api-key-expires-input" {...field} />
              </FormControl>
              <p className="text-xs text-muted-foreground">
                {t("tenancy.api_keys.field_expires_hint")}
              </p>
              <FormMessage />
            </FormItem>
          )}
        />

        <Button
          type="submit"
          className="w-full"
          disabled={form.formState.isSubmitting}
          data-testid="api-key-submit"
        >
          {t("tenancy.api_keys.submit")}
        </Button>
      </form>
    </Form>
  );
}

function RevokeDialog({
  target,
  onCancel,
  onConfirm,
}: {
  target: ApiKeyPublic;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState("");
  const word = t("tenancy.api_keys.revoke_confirm_word");

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {t("tenancy.api_keys.revoke_title", { name: target.name })}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {t("tenancy.api_keys.revoke_description")}
          </p>
          <label className="block space-y-1 text-sm" htmlFor="api-key-revoke-confirm">
            <span>{t("tenancy.api_keys.revoke_confirm_label", { word })}</span>
            <Input
              id="api-key-revoke-confirm"
              data-testid="api-key-revoke-confirm-input"
              value={typed}
              onChange={(event) => {
                setTyped(event.target.value);
              }}
            />
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onCancel}>
              {t("tenancy.api_keys.cancel")}
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={typed !== word}
              onClick={onConfirm}
              data-testid="api-key-revoke-submit"
            >
              {t("tenancy.api_keys.revoke_submit")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ApiKeysPage() {
  const { t, i18n } = useTranslation();
  const { org, isLoading: orgLoading } = useOrg();
  // The nav entry is hidden without this permission, but a direct navigation
  // is not a navigation through the nav -- and a page that renders and then
  // fills with 403s is worse than one that says why. Same shape as
  // BillingSettingsPage's `canRead` gate.
  const canManage = usePermission(Permission.SETTINGS_MANAGE);
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  // The one and only place the secret exists on this side of the wire.
  const [newKey, setNewKey] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<ApiKeyPublic | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const keysQuery = useQuery({
    queryKey: listApiKeysApiV1ApiKeysGetQueryKey(),
    queryFn: async () => {
      const { data } = await listApiKeysApiV1ApiKeysGet({
        query: { limit: MAX_KEYS_PER_ORG },
        throwOnError: true,
      });
      return data;
    },
    enabled: Boolean(org) && canManage,
  });

  const revokeKey = useMutation({
    mutationFn: async (keyId: string) => {
      await revokeApiKeyApiV1ApiKeysKeyIdRevokePost({
        path: { key_id: keyId },
        throwOnError: true,
      });
    },
  });

  const invalidate = async (): Promise<void> => {
    await queryClient.invalidateQueries({
      queryKey: listApiKeysApiV1ApiKeysGetQueryKey(),
    });
  };

  const handleRevoke = async (keyId: string): Promise<void> => {
    setActionError(null);
    try {
      await revokeKey.mutateAsync(keyId);
      await invalidate();
    } catch (error) {
      setActionError(getErrorMessage(error, "tenancy.api_keys.error_revoke"));
    } finally {
      setRevoking(null);
    }
  };

  if (orgLoading || !org) {
    return (
      <div className="max-w-4xl space-y-6 p-6">
        <h1 className="text-xl font-semibold" data-testid="page-title">{t("tenancy.api_keys.title")}</h1>
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (!canManage) {
    return (
      <div className="max-w-4xl space-y-6 p-6" data-testid="api-keys-page">
        <h1 className="text-xl font-semibold" data-testid="page-title">{t("tenancy.api_keys.title")}</h1>
        <Alert>
          <AlertDescription data-testid="api-keys-permission-denied">
            {t("common.error.permission_denied")}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const keys = keysQuery.data?.data ?? [];

  return (
    <div className="max-w-4xl space-y-6 p-6" data-testid="api-keys-page">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold" data-testid="page-title">{t("tenancy.api_keys.title")}</h1>
        <Button
          type="button"
          disabled={keys.length >= MAX_KEYS_PER_ORG}
          onClick={() => {
            setCreateOpen(true);
          }}
          data-testid="api-keys-create-button"
        >
          {t("tenancy.api_keys.create")}
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardDescription>{t("tenancy.api_keys.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {actionError ? (
            <Alert variant="destructive">
              <AlertDescription>{actionError}</AlertDescription>
            </Alert>
          ) : null}
          {keysQuery.isError ? (
            <Alert variant="destructive">
              <AlertDescription>{t("tenancy.api_keys.error_load")}</AlertDescription>
            </Alert>
          ) : null}

          {keysQuery.isPending ? (
            <Skeleton className="h-24 w-full" />
          ) : keys.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-testid="api-keys-empty">
              {t("tenancy.api_keys.empty")}
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("tenancy.api_keys.table_name")}</TableHead>
                  <TableHead>{t("tenancy.api_keys.table_prefix")}</TableHead>
                  <TableHead>{t("tenancy.api_keys.table_scopes")}</TableHead>
                  <TableHead>{t("tenancy.api_keys.table_last_used")}</TableHead>
                  <TableHead>{t("tenancy.api_keys.table_expires")}</TableHead>
                  <TableHead>{t("tenancy.api_keys.table_status")}</TableHead>
                  <TableHead>{t("tenancy.api_keys.table_actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.map((key) => {
                  const status = statusOf(key);
                  return (
                    <TableRow key={key.id} data-testid={`api-key-row-${key.id}`}>
                      <TableCell>{key.name}</TableCell>
                      <TableCell className="font-mono text-xs">{key.prefix}</TableCell>
                      <TableCell className="space-x-1">
                        {key.scopes.map((scope) => (
                          <Badge key={scope} variant="secondary">
                            {scope}
                          </Badge>
                        ))}
                      </TableCell>
                      <TableCell>
                        {key.last_used_at
                          ? formatDate(key.last_used_at, i18n.language)
                          : t("tenancy.api_keys.not_yet")}
                      </TableCell>
                      <TableCell>
                        {key.expires_at
                          ? formatDate(key.expires_at, i18n.language)
                          : t("tenancy.api_keys.never")}
                      </TableCell>
                      <TableCell>
                        <Badge variant={status === "active" ? "default" : "secondary"}>
                          {t(STATUS_LABEL_KEYS[status])}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {status === "revoked" ? null : (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              setRevoking(key);
                            }}
                            data-testid={`api-key-revoke-${key.id}`}
                          >
                            {t("tenancy.api_keys.revoke")}
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}

          <p className="text-xs text-muted-foreground">
            {t("tenancy.api_keys.limit_note", { limit: MAX_KEYS_PER_ORG })}
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
            <DialogTitle>{t("tenancy.api_keys.create_title")}</DialogTitle>
          </DialogHeader>
          <CreateKeyForm
            onCreated={(key) => {
              setCreateOpen(false);
              setNewKey(key);
              void invalidate();
            }}
          />
        </DialogContent>
      </Dialog>

      <Dialog
        open={newKey !== null}
        onOpenChange={(open) => {
          // Closing forgets it, which is the whole contract: the server has
          // no copy either.
          if (!open) setNewKey(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("tenancy.api_keys.created_title")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <Alert variant="destructive">
              <AlertDescription data-testid="api-key-created-warning">
                {t("tenancy.api_keys.created_warning")}
              </AlertDescription>
            </Alert>
            <CopyableSecret
              value={newKey ?? ""}
              testId="api-key-created-value"
              copyLabel={t("tenancy.api_keys.copy")}
              copiedLabel={t("tenancy.api_keys.copied")}
            />
            <div className="flex justify-end">
              <Button
                type="button"
                onClick={() => {
                  setNewKey(null);
                }}
                data-testid="api-key-created-done"
              >
                {t("tenancy.api_keys.done")}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {revoking ? (
        <RevokeDialog
          target={revoking}
          onCancel={() => {
            setRevoking(null);
          }}
          onConfirm={() => {
            void handleRevoke(revoking.id);
          }}
        />
      ) : null}
    </div>
  );
}
