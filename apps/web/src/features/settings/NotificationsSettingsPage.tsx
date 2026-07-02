import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { updateNotificationPreferencesApiV1MeNotificationPreferencesPatch } from "@forge/api-client";
import { useAuth } from "@forge/hooks";
import {
  Alert,
  AlertDescription,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  Checkbox,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@forge/ui";
import {
  DEFAULT_NOTIFICATION_PREFERENCES,
  NOTIFICATION_CHANNELS,
  NOTIFICATION_TYPES,
} from "@forge/types";
import type { NotificationChannel, NotificationType } from "@forge/types";

type PreferencesMatrix = Record<NotificationType, Record<NotificationChannel, boolean>>;

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
};

// The saved column for a type may be a partial row (only the channels the
// user has touched before) -- a missing channel falls back to that type's
// own default rather than reading as "off", mirroring
// backend/app/modules/notifications/preferences.py's channels_for().
function buildInitialMatrix(
  saved: Record<string, Record<string, boolean>> | undefined,
): PreferencesMatrix {
  const matrix = {} as PreferencesMatrix;
  for (const type of NOTIFICATION_TYPES) {
    const defaults = DEFAULT_NOTIFICATION_PREFERENCES[type];
    const overrides = saved?.[type];
    matrix[type] = { ...defaults, ...overrides };
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

  const toggleChannel = (type: NotificationType, channel: NotificationChannel): void => {
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
    <div className="max-w-2xl space-y-6 p-6">
      <h1 className="text-xl font-semibold">{t("notifications.preferences.title")}</h1>
      <Card>
        <CardHeader>
          <CardDescription>{t("notifications.preferences.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          {saved ? (
            <Alert>
              <AlertDescription>{t("notifications.preferences.saved")}</AlertDescription>
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
                  {NOTIFICATION_CHANNELS.map((channel) => (
                    <TableCell key={channel} className="text-center">
                      <Checkbox
                        checked={matrix[type][channel]}
                        aria-label={`${t(TYPE_LABEL_KEYS[type])} ${t(CHANNEL_LABEL_KEYS[channel])}`}
                        onCheckedChange={() => {
                          toggleChannel(type, channel);
                        }}
                      />
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <Button
            type="button"
            disabled={savePreferences.isPending}
            onClick={() => {
              void handleSave();
            }}
          >
            {t("notifications.preferences.save")}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
