import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import i18next from "i18next";

import {
  listOauthAccountsApiV1AuthOauthAccountsGet,
  reauthenticateApiV1AuthReauthPost,
  setAccessToken,
} from "@forge/api-client";
import { useAuth } from "@forge/hooks";
import {
  Alert,
  AlertDescription,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  PasswordInput,
} from "@forge/ui";

import { localeOf, withLocale } from "../../lib/locale-url";
import { getErrorMessage } from "./getErrorMessage";
import { getOAuthStartUrl, OAUTH_PROVIDER_LABEL_KEYS } from "./oauth";
import { onReauthRequest } from "./reauthPrompt";

// Added by the backend when a re-auth through Google or GitHub failed (see
// _reauth_redirect in app/modules/auth/oauth.py). Its value is a message key.
const REAUTH_ERROR_PARAM = "reauth_error";

function readReauthError(): string | null {
  const key = new URLSearchParams(window.location.search).get(
    REAUTH_ERROR_PARAM,
  );
  if (key === null) {
    return null;
  }
  // Only the backend's own error keys: a link someone crafted gets the
  // general wording, not text of their choosing.
  return key.startsWith("errors.") && i18next.exists(key)
    ? i18next.t(key)
    : i18next.t("errors.reauth_failed");
}

function removeReauthError(): void {
  const params = new URLSearchParams(window.location.search);
  if (!params.has(REAUTH_ERROR_PARAM)) {
    return;
  }
  params.delete(REAUTH_ERROR_PARAM);
  const query = params.toString();
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${query ? `?${query}` : ""}`,
  );
}

/**
 * "Confirm it's you", for actions behind the backend's require_recent_auth.
 *
 * When such an action answers 403 step_up_auth_required, the api-client's
 * interceptor asks reauthPrompt (registered in AppRoot), which hands the
 * question to this dialog, and replays the request with the token it
 * resolves to. So the person's click goes through once they have
 * confirmed, without signing out. They confirm the way their account can:
 * the password, a 2FA code, or the Google or GitHub account linked to it.
 * Signing out and in again is there for an account with none of those.
 */
export function ReauthDialog() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  // The interceptor's pending question, answered with a token or null.
  const [answer, setAnswer] = useState<((token: string | null) => void) | null>(
    null,
  );
  const [error, setError] = useState<string | null>(readReauthError);
  const [useCode, setUseCode] = useState(false);
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [providers, setProviders] = useState<string[]>([]);

  const open = answer !== null || error !== null;

  useEffect(() => {
    removeReauthError();
    // reauthPrompt holds a question asked before this lazy component loaded.
    return onReauthRequest((resolve) => {
      setAnswer(() => resolve);
    });
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }
    void listOauthAccountsApiV1AuthOauthAccountsGet().then(({ data }) => {
      setProviders(
        (data?.data ?? [])
          .map((account) => account.provider)
          .filter((provider) => provider in OAUTH_PROVIDER_LABEL_KEYS),
      );
    });
  }, [open]);

  const hasPassword = user?.has_password ?? false;
  const hasCode = user?.totp_enabled ?? false;
  const codeMode = hasCode && (useCode || !hasPassword);

  const close = (token: string | null): void => {
    answer?.(token);
    setAnswer(null);
    setError(null);
    setValue("");
  };

  const handleSubmit = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const { data } = await reauthenticateApiV1AuthReauthPost({
        body: codeMode ? { code: value.trim() } : { password: value },
        throwOnError: true,
      });
      const token = data.data?.access_token ?? null;
      if (token) {
        setAccessToken(token);
      }
      close(token);
    } catch (err) {
      setError(getErrorMessage(err, "errors.reauth_failed"));
    } finally {
      setSubmitting(false);
    }
  };

  const startProviderReauth = (provider: string): void => {
    const next = encodeURIComponent(window.location.pathname);
    window.location.assign(
      `${getOAuthStartUrl(provider)}?reauth=true&next=${next}`,
    );
  };

  const signInAgain = async (): Promise<void> => {
    close(null);
    await logout();
    const locale = localeOf(window.location.pathname) ?? "en";
    window.location.assign(withLocale("/login", locale));
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          close(null);
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("auth.reauth.title")}</DialogTitle>
          <DialogDescription>{t("auth.reauth.description")}</DialogDescription>
        </DialogHeader>
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        {hasPassword || hasCode ? (
          <form
            className="space-y-4"
            onSubmit={(event) => {
              void handleSubmit(event);
            }}
            noValidate
          >
            <div className="space-y-2">
              <Label htmlFor="reauth-value">
                {codeMode
                  ? t("auth.reauth.code_label")
                  : t("auth.reauth.password_label")}
              </Label>
              {codeMode ? (
                <Input
                  id="reauth-value"
                  autoComplete="one-time-code"
                  autoFocus
                  value={value}
                  onChange={(event) => {
                    setValue(event.target.value);
                  }}
                />
              ) : (
                <PasswordInput
                  id="reauth-value"
                  autoComplete="current-password"
                  autoFocus
                  value={value}
                  onChange={(event) => {
                    setValue(event.target.value);
                  }}
                />
              )}
              {codeMode ? (
                <p className="text-xs text-muted-foreground">
                  {t("auth.reauth.code_hint")}
                </p>
              ) : null}
            </div>
            {hasPassword && hasCode ? (
              <Button
                type="button"
                variant="link"
                className="h-auto p-0"
                onClick={() => {
                  setUseCode(!codeMode);
                  setValue("");
                }}
              >
                {codeMode
                  ? t("auth.reauth.use_password")
                  : t("auth.reauth.use_code")}
              </Button>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  close(null);
                }}
              >
                {t("auth.reauth.cancel")}
              </Button>
              <Button type="submit" disabled={submitting || value === ""}>
                {submitting
                  ? t("auth.reauth.submit_pending")
                  : t("auth.reauth.submit")}
              </Button>
            </div>
          </form>
        ) : null}
        {providers.length > 0 ? (
          <div className="grid gap-2">
            {providers.map((provider) => (
              <Button
                key={provider}
                type="button"
                variant="outline"
                className="w-full"
                onClick={() => {
                  startProviderReauth(provider);
                }}
              >
                {t(OAUTH_PROVIDER_LABEL_KEYS[provider] ?? "")}
              </Button>
            ))}
          </div>
        ) : null}
        <Button
          type="button"
          variant="link"
          className="h-auto justify-start p-0 text-muted-foreground"
          onClick={() => {
            void signInAgain();
          }}
        >
          {t("auth.reauth.sign_in_again")}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
