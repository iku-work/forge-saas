import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { updateNotificationPreferencesApiV1MeNotificationPreferencesPatch } from "@forge/api-client";
import { useAuth } from "@forge/hooks";
import {
  Alert,
  AlertDescription,
  Button,
  Checkbox,
  Page,
  PageHeader,
  SettingsSection,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@forge/ui";
import {
  DEFAULT_NOTIFICATION_PREFERENCES,
  isNotificationChannelRequired,
  NOTIFICATION_CHANNELS,
  NOTIFICATION_TYPES,
} from "@forge/types";
import type { NotificationChannel, NotificationType } from "@forge/types";

type PreferencesMatrix = Record<
  NotificationType,
  Record<NotificationChannel, boolean>
>;

const CHANNEL_LABEL_KEYS: Record<NotificationChannel, string> = {
  email: "notifications.preferences.channel_email",
  in_app: "notifications.preferences.channel_in_app",
  push: "notifications.preferences.channel_push",
};

const TYPE_LABEL_KEYS: Record<NotificationType, string> = {
  member_invited: "notifications.preferences.type_member_invited",
  payment_failed: "notifications.preferences.type_payment_failed",
  subscription_expiring: "notifications.preferences.type_subscription_expiring",
  weekly_summary: "notifications.preferences.type_weekly_summary",
  file_uploaded: "notifications.preferences.type_file_uploaded",
  file_infected: "notifications.preferences.type_file_infected",
  webhook_endpoint_disabled:
    "notifications.preferences.type_webhook_endpoint_disabled",
};

const REQUIRED_NOTE_ID = "notification-required-note";

// The saved column for a type may be a partial row (only the channels the
// user has touched before) -- a missing channel falls back to that type's
// own default rather than reading as "off", mirroring
// backend/app/modules/notifications/preferences.py's channels_for().
//
// A required channel (T-234) is forced on regardless of what came back from
// the server: `channels_for()` will send it either way, so showing it off
// would misrepresent what the account actually does.
function buildInitialMatrix(
  saved: Record<string, Record<string, boolean>> | undefined,
): PreferencesMatrix {
  const matrix = {} as PreferencesMatrix;
  for (const type of NOTIFICATION_TYPES) {
    const defaults = DEFAULT_NOTIFICATION_PREFERENCES[type];
    const overrides = saved?.[type];
    const row = { ...defaults, ...overrides };
    for (const channel of NOTIFICATION_CHANNELS) {
      if (isNotificationChannelRequired(type, channel)) {
        row[channel] = true;
      }
    }
    matrix[type] = row;
  }
  return matrix;
}

export function NotificationsSettingsPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [matrix, setMatrix] = useState<PreferencesMatrix>(() =>
    buildInitialMatrix(user?.notification_preferences),
  );
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const savePreferences = useMutation({
    mutationFn: async (next: PreferencesMatrix) => {
      await updateNotificationPreferencesApiV1MeNotificationPreferencesPatch({
        body: { preferences: next },
        throwOnError: true,
      });
    },
  });

  const toggleChannel = (
    type: NotificationType,
    channel: NotificationChannel,
  ): void => {
    if (isNotificationChannelRequired(type, channel)) {
      return; // the checkbox is disabled; this guards a keyboard/programmatic path
    }
    setSaved(false);
    setMatrix((current) => ({
      ...current,
      [type]: { ...current[type], [channel]: !current[type][channel] },
    }));
  };

  const handleSave = async (): Promise<void> => {
    setError(null);
    setSaved(false);
    try {
      await savePreferences.mutateAsync(matrix);
      setSaved(true);
    } catch {
      setError(t("notifications.preferences.error_save"));
    }
  };

  return (
    <Page>
      <PageHeader
        title={t("notifications.preferences.title")}
        description={t("notifications.preferences.description")}
      />
      {/* A type x channel matrix stays a table: rows of label-and-control
          don't fit a grid of checkboxes. Save is always enabled, unlike the
          other settings sections: saving persists the defaults shown and
          heals a stored row that switched off a required channel. */}
      <SettingsSection
        title={t("notifications.preferences.section_title")}
        footer={
          <Button
            type="button"
            disabled={savePreferences.isPending}
            onClick={() => {
              void handleSave();
            }}
          >
            {t("notifications.preferences.save")}
          </Button>
        }
      >
        <div className="space-y-4 pb-5">
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          {saved ? (
            <Alert>
              <AlertDescription>
                {t("notifications.preferences.saved")}
              </AlertDescription>
            </Alert>
          ) : null}

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead />
                {NOTIFICATION_CHANNELS.map((channel) => (
                  <TableHead key={channel} className="text-center">
                    {t(CHANNEL_LABEL_KEYS[channel])}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {NOTIFICATION_TYPES.map((type) => (
                <TableRow key={type}>
                  <TableCell>{t(TYPE_LABEL_KEYS[type])}</TableCell>
                  {NOTIFICATION_CHANNELS.map((channel) => {
                    const required = isNotificationChannelRequired(
                      type,
                      channel,
                    );
                    const label = `${t(TYPE_LABEL_KEYS[type])} ${t(CHANNEL_LABEL_KEYS[channel])}`;
                    return (
                      <TableCell key={channel} className="text-center">
                        <Checkbox
                          checked={matrix[type][channel]}
                          disabled={required}
                          // The disabled state alone reads as "temporarily
                          // unavailable" to a screen reader; the word and the
                          // note it points at are what say *why*.
                          aria-label={
                            required
                              ? `${label} (${t("notifications.preferences.channel_required")})`
                              : label
                          }
                          aria-describedby={
                            required ? REQUIRED_NOTE_ID : undefined
                          }
                          onCheckedChange={() => {
                            toggleChannel(type, channel);
                          }}
                        />
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <p id={REQUIRED_NOTE_ID} className="text-sm text-muted-foreground">
            {t("notifications.preferences.required_note")}
          </p>
        </div>
      </SettingsSection>
    </Page>
  );
}
