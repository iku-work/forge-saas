import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  getAdminProductSettings,
  patchAdminProductSettings,
  replaceAdminProductSettings,
  type AdminProductSettings,
  type Envelope,
  type ProductSettingsValue,
} from "@forge/api-client";
import { refreshConfig } from "@forge/hooks";
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  Checkbox,
  Input,
  Label,
  Page,
  PageHeader,
  SettingsRow,
  SettingsSection,
  Skeleton,
  toast,
} from "@forge/ui";
import { formatDate } from "@forge/utils";

import { getErrorMessage } from "../auth/getErrorMessage";

// T-263: the operator-facing surface for the global runtime switches.
//
// Two editors over the same document, on purpose. The typed form covers the
// well-known keys and is what someone uses at 3am -- checkboxes and a text
// field cannot produce the `"maintenance_mode": "false"` string that reads as
// truthy. The raw JSON editor underneath is the escape hatch for a key this
// build has never heard of, and it is a `PUT` (replace) rather than a `PATCH`
// precisely so what the operator sees in the textarea is what the server will
// hold afterwards.
//
// The form's *fields* come from the server's `well_known` registry rather than
// a hardcoded list here: the registry lives in the backend's
// app.modules.product_settings.keys, and a form built from a copy of it is a
// form that silently stops matching the day a key is added.

const BANNER_TYPES = ["info", "warning", "critical"] as const;
type BannerType = (typeof BANNER_TYPES)[number];

interface BannerValue {
  type: BannerType;
  message: string;
  dismissible: boolean;
}

function asBoolean(value: ProductSettingsValue): boolean {
  return value === true;
}

function asString(value: ProductSettingsValue): string {
  return typeof value === "string" ? value : "";
}

function asBanner(value: ProductSettingsValue): BannerValue | null {
  if (value === null || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const type = raw.type;
  if (typeof type !== "string" || !BANNER_TYPES.includes(type as BannerType)) {
    return null;
  }
  return {
    type: type as BannerType,
    message: typeof raw.message === "string" ? raw.message : "",
    dismissible: raw.dismissible !== false,
  };
}

function asKillSwitch(value: ProductSettingsValue): Record<string, boolean> {
  if (value === null || typeof value !== "object") return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, on]) => [
      key,
      on === true,
    ]),
  );
}

export function AdminProductSettingsPage() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();

  // The whole envelope is cached, unwrapped at the use site -- same shape as
  // AdminDashboardPage. `data` is typed nullable on every envelope (it is null
  // on an error response), and neither `!` nor `as` is allowed here, so the
  // narrowing has to be a real check rather than an assertion.
  const settingsQuery = useQuery({
    queryKey: ["admin", "product-settings"],
    queryFn: async () => {
      const { data } = await getAdminProductSettings();
      return data;
    },
  });

  const settings = settingsQuery.data?.data ?? null;
  const stored = settings?.settings ?? {};
  // Changes exactly when the stored document does -- including after a save,
  // which returns the new document. Used as the `key` on both editors below
  // so each remounts with fresh initial state instead of re-seeding itself
  // from an effect (react-hooks/set-state-in-effect). Discarding an operator's
  // half-typed edit when the server's copy moves under them is the intended
  // behaviour here: this document is the truth, and two operators during an
  // incident is a real scenario.
  const documentKey = JSON.stringify(stored);

  const applyChanges = useMutation({
    mutationFn: async (changes: Record<string, ProductSettingsValue>) => {
      const { data } = await patchAdminProductSettings(changes);
      return data;
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(["admin", "product-settings"], data);
      // The operator is also a user of this app: without this, the banner they
      // just raised wouldn't appear on their own screen for up to a minute,
      // which reads as "it didn't work" and invites a second flip.
      await refreshConfig();
      toast.success(t("common.admin.product_settings.saved"));
    },
    onError: (error: unknown) => {
      toast.error(
        getErrorMessage(error, "common.admin.product_settings.error_save"),
      );
    },
  });

  return (
    <Page>
      <PageHeader
        title={t("common.admin.product_settings.title")}
        description={t("common.admin.product_settings.description")}
      />

      {settingsQuery.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : settingsQuery.isError ? (
        <Alert variant="destructive">
          <AlertDescription>
            {getErrorMessage(
              settingsQuery.error,
              "common.admin.product_settings.error_load",
            )}
          </AlertDescription>
        </Alert>
      ) : settings ? (
        <>
          {asBoolean(stored.maintenance_mode) ? (
            // Loud, and above everything else on the page: an operator landing
            // here mid-incident should not have to read a checkbox state to
            // learn that the product is currently down.
            <Alert variant="destructive" role="status">
              <AlertDescription>
                {t("common.admin.product_settings.maintenance_active")}
              </AlertDescription>
            </Alert>
          ) : null}

          <TypedSettingsForm
            key={documentKey}
            data={settings}
            onApply={(changes) => {
              applyChanges.mutate(changes);
            }}
            isSaving={applyChanges.isPending}
          />

          <RawJsonEditor
            key={documentKey}
            settings={stored}
            onSaved={async (data) => {
              queryClient.setQueryData(["admin", "product-settings"], data);
              await refreshConfig();
            }}
          />

          <p className="mt-8 text-xs text-muted-foreground">
            {settings.updated_at
              ? t("common.admin.product_settings.last_changed", {
                  date: formatDate(settings.updated_at, i18n.language),
                  actor:
                    settings.updated_by ??
                    t("common.admin.product_settings.unknown_actor"),
                })
              : t("common.admin.product_settings.never_changed")}
          </p>
        </>
      ) : null}
    </Page>
  );
}

function TypedSettingsForm({
  data,
  onApply,
  isSaving,
}: {
  data: AdminProductSettings;
  onApply: (changes: Record<string, ProductSettingsValue>) => void;
  isSaving: boolean;
}) {
  const { t } = useTranslation();
  const stored = data.settings;

  // Seeded once, from the document this component was mounted for. The parent
  // gives it a `key` derived from that document, so a save (from here or from
  // the raw editor below) remounts this form with the new values rather than
  // leaving the two halves of the page disagreeing.
  const seed = asBanner(stored.global_banner);
  const [maintenanceMode, setMaintenanceMode] = useState(() =>
    asBoolean(stored.maintenance_mode),
  );
  const [maintenanceMessage, setMaintenanceMessage] = useState(() =>
    asString(stored.maintenance_message),
  );
  const [signupDisabled, setSignupDisabled] = useState(() =>
    asBoolean(stored.signup_disabled),
  );
  const [bannerEnabled, setBannerEnabled] = useState(seed !== null);
  const [bannerType, setBannerType] = useState<BannerType>(
    seed?.type ?? "info",
  );
  const [bannerMessage, setBannerMessage] = useState(seed?.message ?? "");
  const [bannerDismissible, setBannerDismissible] = useState(
    seed?.dismissible ?? true,
  );
  const [killSwitch, setKillSwitch] = useState<Record<string, boolean>>(() =>
    asKillSwitch(stored.emergency_kill_switch),
  );

  const documented = new Map(
    data.well_known.map((entry) => [entry.key, entry]),
  );

  const handleSubmit = (): void => {
    onApply({
      // `null` unsets rather than storing a falsy value -- the two look the
      // same in this form and read very differently in the audit diff.
      maintenance_mode: maintenanceMode ? true : null,
      maintenance_message: maintenanceMessage.trim()
        ? maintenanceMessage
        : null,
      signup_disabled: signupDisabled ? true : null,
      global_banner:
        bannerEnabled && bannerMessage.trim()
          ? {
              type: bannerType,
              message: bannerMessage,
              dismissible: bannerDismissible,
            }
          : null,
      emergency_kill_switch: Object.values(killSwitch).some(Boolean)
        ? killSwitch
        : null,
    });
  };

  return (
    <SettingsSection
      title={t("common.admin.product_settings.switches_title")}
      footer={
        <Button
          type="button"
          onClick={handleSubmit}
          disabled={isSaving}
          data-testid="save-product-settings"
        >
          {isSaving
            ? t("common.admin.product_settings.saving")
            : t("common.admin.product_settings.save")}
        </Button>
      }
    >
      <SettingsRow
        label={t("common.admin.product_settings.maintenance_mode_label")}
        hint={documented.get("maintenance_mode")?.description}
        htmlFor="maintenance-mode"
      >
        <Checkbox
          id="maintenance-mode"
          checked={maintenanceMode}
          onCheckedChange={(checked) => {
            setMaintenanceMode(checked === true);
          }}
        />
      </SettingsRow>

      <SettingsRow
        label={t("common.admin.product_settings.maintenance_message_label")}
        hint={documented.get("maintenance_message")?.description}
        htmlFor="maintenance-message"
      >
        <Input
          id="maintenance-message"
          value={maintenanceMessage}
          onChange={(event) => {
            setMaintenanceMessage(event.target.value);
          }}
          placeholder={t(
            "common.admin.product_settings.maintenance_message_placeholder",
          )}
        />
      </SettingsRow>

      <SettingsRow
        label={t("common.admin.product_settings.signup_disabled_label")}
        hint={documented.get("signup_disabled")?.description}
        htmlFor="signup-disabled"
      >
        <Checkbox
          id="signup-disabled"
          checked={signupDisabled}
          onCheckedChange={(checked) => {
            setSignupDisabled(checked === true);
          }}
        />
      </SettingsRow>

      <SettingsRow
        label={t("common.admin.product_settings.banner_label")}
        hint={documented.get("global_banner")?.description}
        htmlFor="banner-enabled"
      >
        <Checkbox
          id="banner-enabled"
          checked={bannerEnabled}
          onCheckedChange={(checked) => {
            setBannerEnabled(checked === true);
          }}
        />
      </SettingsRow>
      {bannerEnabled ? (
        <>
          <SettingsRow
            label={t("common.admin.product_settings.banner_type_label")}
            htmlFor="banner-type"
          >
            <select
              id="banner-type"
              value={bannerType}
              onChange={(event) => {
                setBannerType(event.target.value as BannerType);
              }}
              className="h-9 w-full rounded-md border border-input bg-card px-3 text-sm"
            >
              {BANNER_TYPES.map((type) => (
                <option key={type} value={type}>
                  {t(`common.admin.product_settings.banner_type_${type}`)}
                </option>
              ))}
            </select>
          </SettingsRow>
          <SettingsRow
            label={t("common.admin.product_settings.banner_message_label")}
            htmlFor="banner-message"
          >
            <Input
              id="banner-message"
              value={bannerMessage}
              onChange={(event) => {
                setBannerMessage(event.target.value);
              }}
            />
          </SettingsRow>
          <SettingsRow
            label={t("common.admin.product_settings.banner_dismissible_label")}
            htmlFor="banner-dismissible"
          >
            <Checkbox
              id="banner-dismissible"
              checked={bannerDismissible}
              onCheckedChange={(checked) => {
                setBannerDismissible(checked === true);
              }}
            />
          </SettingsRow>
        </>
      ) : null}

      <SettingsRow
        label={t("common.admin.product_settings.kill_switch_label")}
        hint={documented.get("emergency_kill_switch")?.description}
      >
        <div className="grid gap-2.5">
          {data.killable_modules.map((module) => (
            <div key={module} className="flex items-center gap-2">
              <Checkbox
                id={`kill-${module}`}
                checked={killSwitch[module] === true}
                onCheckedChange={(checked) => {
                  setKillSwitch((current) => ({
                    ...current,
                    [module]: checked === true,
                  }));
                }}
              />
              <Label
                htmlFor={`kill-${module}`}
                className="font-mono text-[0.8125rem] font-normal"
              >
                {module}
              </Label>
            </div>
          ))}
        </div>
      </SettingsRow>
    </SettingsSection>
  );
}

function RawJsonEditor({
  settings,
  onSaved,
}: {
  settings: Record<string, ProductSettingsValue>;
  onSaved: (envelope: Envelope<AdminProductSettings>) => Promise<void>;
}) {
  const { t } = useTranslation();
  // Same remount-on-change contract as TypedSettingsForm above: the parent's
  // `key` is what re-seeds this textarea, so there is no effect writing over
  // what the operator has typed.
  const [draft, setDraft] = useState(() => JSON.stringify(settings, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async (parsed: Record<string, ProductSettingsValue>) => {
      const { data } = await replaceAdminProductSettings(parsed);
      return data;
    },
    onSuccess: async (data) => {
      await onSaved(data);
      toast.success(t("common.admin.product_settings.saved"));
    },
    onError: (error: unknown) => {
      toast.error(
        getErrorMessage(error, "common.admin.product_settings.error_save"),
      );
    },
  });

  const handleSave = (): void => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(draft);
    } catch {
      // Caught here rather than sent to the server: a JSON syntax error is a
      // typo in this textarea, and telling the operator that costs no round
      // trip. Anything that *is* valid JSON goes to the server, which owns
      // the real validation.
      setParseError(t("common.admin.product_settings.raw_invalid_json"));
      return;
    }
    if (
      parsed === null ||
      typeof parsed !== "object" ||
      Array.isArray(parsed)
    ) {
      setParseError(t("common.admin.product_settings.raw_not_an_object"));
      return;
    }
    setParseError(null);
    save.mutate(parsed as Record<string, ProductSettingsValue>);
  };

  return (
    <SettingsSection
      title={
        <span className="flex items-center gap-2">
          {t("common.admin.product_settings.raw_title")}
          <Badge variant="secondary">
            {t("common.admin.product_settings.raw_advanced")}
          </Badge>
        </span>
      }
      description={t("common.admin.product_settings.raw_description")}
      footer={
        <Button
          type="button"
          variant="outline"
          onClick={handleSave}
          disabled={save.isPending}
          data-testid="save-raw-product-settings"
        >
          {t("common.admin.product_settings.raw_save")}
        </Button>
      }
    >
      <div className="space-y-3 pb-4">
        <Label htmlFor="raw-settings" className="sr-only">
          {t("common.admin.product_settings.raw_title")}
        </Label>
        <textarea
          id="raw-settings"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
          }}
          spellCheck={false}
          rows={10}
          className="w-full rounded-md border border-input bg-card p-3 font-mono text-xs"
        />
        {parseError ? (
          <Alert variant="destructive">
            <AlertDescription>{parseError}</AlertDescription>
          </Alert>
        ) : null}
      </div>
    </SettingsSection>
  );
}
