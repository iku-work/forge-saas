import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  deleteAdminOrgFeatureFlag,
  getAdminOrgDetail,
  getAdminOrgFeatureFlags,
  listAdminAuditFeed,
  setAdminOrgFeatureFlag,
} from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  DataListMuted,
  DataListRow,
  Page,
  PageHeader,
  SettingsRow,
  Skeleton,
  StatusPill,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { useRouteLocale } from "../../lib/useRouteLocale";
import { getErrorMessage } from "../auth/getErrorMessage";
import { CompCreditModal } from "./CompCreditModal";
import { ExtendTrialModal } from "./ExtendTrialModal";
import { PlanOverrideModal } from "./PlanOverrideModal";

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <SettingsRow label={label}>
      <span className="text-foreground-secondary">{value}</span>
    </SettingsRow>
  );
}

function SubscriptionTab({ orgId }: { orgId: string }) {
  const { t, i18n } = useTranslation();
  const [planOpen, setPlanOpen] = useState(false);
  const [trialOpen, setTrialOpen] = useState(false);
  const [compOpen, setCompOpen] = useState(false);

  const detailQuery = useQuery({
    queryKey: ["admin", "orgs", "detail", orgId],
    queryFn: async () => {
      const { data } = await getAdminOrgDetail(orgId);
      return data;
    },
  });
  const detail = detailQuery.data?.data ?? null;
  const subscription = detail?.subscription ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setPlanOpen(true);
          }}
        >
          {t("common.admin.org_detail.override_plan_button")}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!subscription}
          onClick={() => {
            setTrialOpen(true);
          }}
        >
          {t("common.admin.org_detail.extend_trial_button")}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!subscription}
          onClick={() => {
            setCompOpen(true);
          }}
        >
          {t("common.admin.org_detail.comp_credit_button")}
        </Button>
      </div>

      {detailQuery.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : subscription ? (
        <div className="@container/section">
          <DetailRow
            label={t("common.admin.org_detail.plan_label")}
            value={subscription.plan_slug}
          />
          <DetailRow
            label={t("common.admin.org_detail.status_label")}
            value={subscription.status}
          />
          <DetailRow
            label={t("common.admin.org_detail.billing_interval_label")}
            value={
              subscription.billing_interval ??
              t("common.admin.org_detail.not_available")
            }
          />
          <DetailRow
            label={t("common.admin.org_detail.trial_end_label")}
            value={
              subscription.trial_end
                ? formatDate(subscription.trial_end, i18n.language)
                : t("common.admin.org_detail.not_available")
            }
          />
          <DetailRow
            label={t("common.admin.org_detail.period_end_label")}
            value={formatDate(subscription.current_period_end, i18n.language)}
          />
          <DetailRow
            label={t("common.admin.org_detail.free_override_label")}
            value={
              subscription.free_override
                ? t("common.admin.org_detail.yes")
                : t("common.admin.org_detail.no")
            }
          />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          {t("common.admin.org_detail.no_subscription")}
        </p>
      )}

      <PlanOverrideModal
        orgId={orgId}
        open={planOpen}
        onOpenChange={setPlanOpen}
        onDone={() => {
          setPlanOpen(false);
        }}
      />
      <ExtendTrialModal
        orgId={orgId}
        open={trialOpen}
        onOpenChange={setTrialOpen}
        onDone={() => {
          setTrialOpen(false);
        }}
      />
      <CompCreditModal
        orgId={orgId}
        open={compOpen}
        onOpenChange={setCompOpen}
        onDone={() => {
          setCompOpen(false);
        }}
      />
    </div>
  );
}

// Members/Files tabs show only the summary counts already on AdminOrgDetail
// -- there's no admin endpoint to list an arbitrary org's members/files (the
// ordinary /orgs/{id}/members and /files endpoints require the caller to be
// a member of that org, which a superadmin usually isn't), and adding one is
// out of scope for T-143's UI wiring.
function MembersTab({
  memberCount,
  isLoading,
}: {
  memberCount: number | undefined;
  isLoading: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="@container/section">
      {isLoading ? (
        <Skeleton className="h-8 w-24" />
      ) : (
        <DetailRow
          label={t("common.admin.org_detail.members_label")}
          value={String(memberCount ?? 0)}
        />
      )}
    </div>
  );
}

function FilesTab({
  fileCount,
  isLoading,
}: {
  fileCount: number | null | undefined;
  isLoading: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="@container/section">
      {isLoading ? (
        <Skeleton className="h-8 w-24" />
      ) : (
        <DetailRow
          label={t("common.admin.org_detail.files_label")}
          value={
            fileCount === null || fileCount === undefined
              ? "—"
              : String(fileCount)
          }
        />
      )}
    </div>
  );
}

function AuditTab({ orgId }: { orgId: string }) {
  const { t, i18n } = useTranslation();
  const auditQuery = useQuery({
    queryKey: ["admin", "audit", { org: orgId }],
    queryFn: async () => {
      const { data } = await listAdminAuditFeed({ org: orgId, limit: 20 });
      return data;
    },
  });
  const events = auditQuery.data?.data ?? [];

  if (auditQuery.isLoading) {
    return <Skeleton className="h-32 w-full" />;
  }
  if (events.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {t("common.admin.audit.no_results")}
      </p>
    );
  }
  return (
    <div className="border-t border-border">
      {events.map((event) => (
        <DataListRow key={event.id} className="grid-cols-[minmax(0,1fr)_auto]">
          <div className="min-w-0">
            <p className="truncate font-mono text-[0.8125rem]">
              {event.action}
            </p>
            <p className="truncate text-xs text-muted-foreground">
              {event.actor_email ?? t("common.admin.audit.system_actor")}
            </p>
          </div>
          <DataListMuted>{formatDate(event.ts, i18n.language)}</DataListMuted>
        </DataListRow>
      ))}
    </div>
  );
}

function FeatureFlagsTab({ orgId }: { orgId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const flagsQuery = useQuery({
    queryKey: ["admin", "orgs", "feature-flags", orgId],
    queryFn: async () => {
      const { data } = await getAdminOrgFeatureFlags(orgId);
      return data;
    },
  });
  const flags = flagsQuery.data?.data?.flags ?? {};

  const invalidate = async (): Promise<void> => {
    await queryClient.invalidateQueries({
      queryKey: ["admin", "orgs", "feature-flags", orgId],
    });
  };

  const setFlag = useMutation({
    mutationFn: async ({
      flag,
      enabled,
    }: {
      flag: string;
      enabled: boolean;
    }) => {
      await setAdminOrgFeatureFlag(orgId, flag, enabled);
    },
    onSuccess: () => void invalidate(),
  });
  const clearFlag = useMutation({
    mutationFn: async (flag: string) => {
      await deleteAdminOrgFeatureFlag(orgId, flag);
    },
    onSuccess: () => void invalidate(),
  });

  const entries = Object.entries(flags);

  if (flagsQuery.isLoading) {
    return <Skeleton className="h-32 w-full" />;
  }

  return (
    <div className="space-y-2">
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t("common.admin.orgs.no_results")}
        </p>
      ) : (
        entries.map(([flag, state]) => (
          <div
            key={flag}
            className="flex flex-wrap items-center justify-between gap-3 border-b border-border py-3 text-sm"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-[0.8125rem]">{flag}</span>
              <StatusPill tone={state.resolved ? "active" : "neutral"}>
                {state.resolved
                  ? t("common.admin.org_detail.flag_resolved_on")
                  : t("common.admin.org_detail.flag_resolved_off")}
              </StatusPill>
              {state.override !== null ? (
                <StatusPill tone="pending">
                  {t("common.admin.org_detail.flag_override_prefix", {
                    value: state.override
                      ? t("common.admin.org_detail.flag_resolved_on")
                      : t("common.admin.org_detail.flag_resolved_off"),
                  })}
                </StatusPill>
              ) : null}
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={setFlag.isPending}
                onClick={() => {
                  setError(null);
                  setFlag.mutate(
                    { flag, enabled: true },
                    {
                      onError: (err) => {
                        setError(
                          getErrorMessage(
                            err,
                            "common.admin.org_detail.error_action_default",
                          ),
                        );
                      },
                    },
                  );
                }}
              >
                {t("common.admin.org_detail.flag_turn_on")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={setFlag.isPending}
                onClick={() => {
                  setError(null);
                  setFlag.mutate(
                    { flag, enabled: false },
                    {
                      onError: (err) => {
                        setError(
                          getErrorMessage(
                            err,
                            "common.admin.org_detail.error_action_default",
                          ),
                        );
                      },
                    },
                  );
                }}
              >
                {t("common.admin.org_detail.flag_turn_off")}
              </Button>
              {state.override !== null ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={clearFlag.isPending}
                  onClick={() => {
                    setError(null);
                    clearFlag.mutate(flag, {
                      onError: (err) => {
                        setError(
                          getErrorMessage(
                            err,
                            "common.admin.org_detail.error_action_default",
                          ),
                        );
                      },
                    });
                  }}
                >
                  {t("common.button.remove")}
                </Button>
              ) : null}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

export function AdminOrgDetailPage({ orgId }: { orgId: string }) {
  const locale = useRouteLocale();
  const { t, i18n } = useTranslation();

  const detailQuery = useQuery({
    queryKey: ["admin", "orgs", "detail", orgId],
    queryFn: async () => {
      const { data } = await getAdminOrgDetail(orgId);
      return data;
    },
  });
  const detail = detailQuery.data?.data ?? null;

  return (
    <Page>
      <Link
        to="/$locale/admin/orgs"
        params={{ locale }}
        className="mb-3 inline-flex items-center gap-1.5 text-[0.8125rem] text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" aria-hidden="true" />
        {t("common.admin.org_detail.back_to_orgs")}
      </Link>

      {detailQuery.isLoading ? (
        <Skeleton className="h-10 w-64" />
      ) : detailQuery.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {getErrorMessage(
              detailQuery.error,
              "common.admin.org_detail.error_load",
            )}
          </AlertDescription>
        </Alert>
      ) : detail ? (
        <>
          <PageHeader
            title={detail.name}
            description={
              <span className="flex flex-wrap gap-x-4 gap-y-1">
                <span className="font-mono text-[0.8125rem]">
                  {detail.slug}
                </span>
                <span>
                  {t("common.admin.org_detail.created_line", {
                    date: formatDate(detail.created_at, i18n.language),
                  })}
                </span>
                <span>
                  {t("common.admin.org_detail.last_activity_line", {
                    date: detail.last_activity_at
                      ? formatDate(detail.last_activity_at, i18n.language)
                      : t("common.admin.org_detail.never"),
                  })}
                </span>
              </span>
            }
          />

          <Tabs defaultValue="subscription">
            <TabsList>
              <TabsTrigger value="subscription">
                {t("common.admin.org_detail.tab_subscription")}
              </TabsTrigger>
              <TabsTrigger value="members">
                {t("common.admin.org_detail.tab_members")}
              </TabsTrigger>
              <TabsTrigger value="files">
                {t("common.admin.org_detail.tab_files")}
              </TabsTrigger>
              <TabsTrigger value="audit">
                {t("common.admin.org_detail.tab_audit")}
              </TabsTrigger>
              <TabsTrigger value="feature-flags">
                {t("common.admin.org_detail.tab_feature_flags")}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="subscription">
              <SubscriptionTab orgId={orgId} />
            </TabsContent>
            <TabsContent value="members">
              <MembersTab
                memberCount={detail.member_count}
                isLoading={detailQuery.isLoading}
              />
            </TabsContent>
            <TabsContent value="files">
              <FilesTab
                fileCount={detail.file_count}
                isLoading={detailQuery.isLoading}
              />
            </TabsContent>
            <TabsContent value="audit">
              <AuditTab orgId={orgId} />
            </TabsContent>
            <TabsContent value="feature-flags">
              <FeatureFlagsTab orgId={orgId} />
            </TabsContent>
          </Tabs>
        </>
      ) : null}
    </Page>
  );
}
