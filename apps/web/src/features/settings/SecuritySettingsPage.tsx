import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { startRegistration } from "@simplewebauthn/browser";
import type { PublicKeyCredentialCreationOptionsJSON } from "@simplewebauthn/browser";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import {
  changePasswordApiV1AuthChangePasswordPost,
  disable2FaApiV1Auth2FaDisablePost,
  enable2FaApiV1Auth2FaEnablePost,
  listCredentialsApiV1AuthWebauthnCredentialsGet,
  listCredentialsApiV1AuthWebauthnCredentialsGetQueryKey,
  listOauthAccountsApiV1AuthOauthAccountsGet,
  listOauthAccountsApiV1AuthOauthAccountsGetQueryKey,
  listSessionsApiV1AuthSessionsGet,
  listSessionsApiV1AuthSessionsGetQueryKey,
  oauthUnlinkApiV1AuthOauthProviderUnlinkPost,
  registerApiV1AuthWebauthnRegisterPost,
  registerOptionsApiV1AuthWebauthnRegisterOptionsPost,
  requestEmailChangeApiV1AuthChangeEmailPost,
  revokeCredentialApiV1AuthWebauthnCredentialsCredentialIdDelete,
  revokeSessionApiV1AuthSessionsSessionIdDelete,
  verify2FaApiV1Auth2FaVerifyPost,
} from "@forge/api-client";
import { useAuth, useModules } from "@forge/hooks";
import {
  Alert,
  AlertDescription,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  PasswordInput,
  PasswordStrengthMeter,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@forge/ui";
import { formatDate } from "@forge/utils";
import {
  changeEmailSchema,
  changePasswordSchema,
  type ChangeEmailInput,
  type ChangePasswordInput,
} from "@forge/validation";

import { getErrorMessage } from "../auth/getErrorMessage";

const _OAUTH_PROVIDERS = ["google", "github"] as const;

function SessionsCard() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  // Raw SDK calls directly in queryFn/mutationFn rather than the generated
  // *GetOptions()/*Mutation() helpers, for two compounding reasons: (1)
  // under this project's exactOptionalPropertyTypes, queryOptions()'s
  // return type doesn't structurally satisfy useQuery's parameter type, and
  // (2) @forge/api-client ships a pre-bundled dist build, so those helpers'
  // internal calls into the SDK are resolved at build time and can't be
  // intercepted by vi.mock("@forge/api-client", ...) in tests. The query
  // key function alone has neither problem, so it's still reused here to
  // keep this in sync with invalidateQueries below.
  const sessionsQuery = useQuery({
    queryKey: listSessionsApiV1AuthSessionsGetQueryKey(),
    queryFn: async () => {
      const { data } = await listSessionsApiV1AuthSessionsGet({
        throwOnError: true,
      });
      return data;
    },
  });
  const revokeSession = useMutation({
    mutationFn: async (sessionId: string) => {
      await revokeSessionApiV1AuthSessionsSessionIdDelete({
        path: { session_id: sessionId },
        throwOnError: true,
      });
    },
  });
  const [revokeError, setRevokeError] = useState<string | null>(null);

  const handleRevoke = async (sessionId: string): Promise<void> => {
    setRevokeError(null);
    try {
      await revokeSession.mutateAsync(sessionId);
      await queryClient.invalidateQueries({
        queryKey: listSessionsApiV1AuthSessionsGetQueryKey(),
      });
    } catch (error) {
      setRevokeError(getErrorMessage(error, "auth.sessions.error_revoke"));
    }
  };

  const sessions = sessionsQuery.data?.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardDescription>{t("auth.sessions.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {revokeError ? (
          <Alert variant="destructive">
            <AlertDescription>{revokeError}</AlertDescription>
          </Alert>
        ) : null}

        {sessionsQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : sessionsQuery.isError ? (
          <Alert variant="destructive">
            <AlertDescription>
              {getErrorMessage(sessionsQuery.error, "auth.sessions.error_load")}
            </AlertDescription>
          </Alert>
        ) : sessions.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("auth.sessions.no_active")}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("auth.sessions.device_col")}</TableHead>
                <TableHead>{t("auth.sessions.last_seen_col")}</TableHead>
                <TableHead className="text-end">
                  {t("auth.sessions.action_col")}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sessions.map((session) => (
                <TableRow key={session.id}>
                  <TableCell>
                    <div>
                      {session.device_label ??
                        t("auth.sessions.unknown_device")}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {session.ip_address}
                    </div>
                  </TableCell>
                  <TableCell>
                    {formatDate(session.last_seen_at, i18n.language)}
                  </TableCell>
                  <TableCell className="text-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={revokeSession.isPending}
                      onClick={() => {
                        void handleRevoke(session.id);
                      }}
                    >
                      {t("auth.sessions.revoke")}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

const verifyCodeSchema = z.object({
  code: z
    .string()
    .length(6, "validation.code_length")
    .regex(/^\d+$/, "validation.code_digits_only"),
});
type VerifyCodeInput = z.infer<typeof verifyCodeSchema>;

const disable2faSchema = z.object({
  password: z.string().min(1, "validation.required"),
  code: z
    .string()
    .length(6, "validation.code_length")
    .regex(/^\d+$/, "validation.code_digits_only"),
});
type Disable2faInput = z.infer<typeof disable2faSchema>;

function TwoFactorCard() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [totpEnabled, setTotpEnabled] = useState(user?.totp_enabled ?? false);
  const [provisioning, setProvisioning] = useState<{
    provisioningUri: string;
    secret: string;
  } | null>(null);
  const [backupCodes, setBackupCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Raw SDK calls rather than the generated *Mutation() helpers -- see the
  // comment above SessionsCard's sessionsQuery for why.
  const enable2fa = useMutation({
    mutationFn: async () => {
      const { data } = await enable2FaApiV1Auth2FaEnablePost({
        throwOnError: true,
      });
      return data;
    },
  });
  const verify2fa = useMutation({
    mutationFn: async (code: string) => {
      const { data } = await verify2FaApiV1Auth2FaVerifyPost({
        body: { code },
        throwOnError: true,
      });
      return data;
    },
  });
  const disable2fa = useMutation({
    mutationFn: async (input: Disable2faInput) => {
      await disable2FaApiV1Auth2FaDisablePost({
        body: input,
        throwOnError: true,
      });
    },
  });

  const verifyForm = useForm<VerifyCodeInput>({
    resolver: zodResolver(verifyCodeSchema),
    defaultValues: { code: "" },
  });
  const disableForm = useForm<Disable2faInput>({
    resolver: zodResolver(disable2faSchema),
    defaultValues: { password: "", code: "" },
  });

  const handleStartEnable = async (): Promise<void> => {
    setError(null);
    try {
      const envelope = await enable2fa.mutateAsync();
      const data = envelope.data;
      if (data) {
        setProvisioning({
          provisioningUri: data.provisioning_uri,
          secret: data.secret,
        });
      }
    } catch (caught) {
      setError(getErrorMessage(caught, "auth.two_factor.error_enable"));
    }
  };

  const handleVerify = async (values: VerifyCodeInput): Promise<void> => {
    setError(null);
    try {
      const envelope = await verify2fa.mutateAsync(values.code);
      setBackupCodes(envelope.data?.backup_codes ?? []);
      setTotpEnabled(true);
      setProvisioning(null);
      verifyForm.reset();
    } catch (caught) {
      setError(getErrorMessage(caught, "auth.two_factor.error_verify"));
    }
  };

  const handleDisable = async (values: Disable2faInput): Promise<void> => {
    setError(null);
    try {
      await disable2fa.mutateAsync(values);
      setTotpEnabled(false);
      setBackupCodes(null);
      disableForm.reset();
    } catch (caught) {
      setError(getErrorMessage(caught, "auth.two_factor.error_disable"));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardDescription>{t("auth.two_factor.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {backupCodes ? (
          <Alert>
            <AlertDescription>
              <p className="mb-2 font-medium">
                {t("auth.two_factor.backup_codes_notice")}
              </p>
              <ul className="grid grid-cols-2 gap-1 font-mono text-xs">
                {backupCodes.map((code) => (
                  <li key={code}>{code}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        ) : null}

        {totpEnabled ? (
          <Form {...disableForm}>
            <form
              className="space-y-3"
              onSubmit={(event) => {
                void disableForm.handleSubmit(handleDisable)(event);
              }}
              noValidate
            >
              <p className="text-sm">{t("auth.two_factor.enabled_notice")}</p>
              <FormField
                control={disableForm.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {t("auth.two_factor.current_password_label")}
                    </FormLabel>
                    <FormControl>
                      <Input
                        type="password"
                        autoComplete="current-password"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={disableForm.control}
                name="code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{t("auth.two_factor.code_label")}</FormLabel>
                    <FormControl>
                      <Input
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button
                type="submit"
                variant="destructive"
                disabled={disableForm.formState.isSubmitting}
              >
                {t("auth.two_factor.disable")}
              </Button>
            </form>
          </Form>
        ) : provisioning ? (
          <Form {...verifyForm}>
            <form
              className="space-y-3"
              onSubmit={(event) => {
                void verifyForm.handleSubmit(handleVerify)(event);
              }}
              noValidate
            >
              <p className="text-sm">{t("auth.two_factor.scan_instruction")}</p>
              <p className="break-all rounded-md bg-muted p-2 font-mono text-xs">
                {provisioning.provisioningUri}
              </p>
              <p className="text-xs text-muted-foreground">
                {t("auth.two_factor.secret_label", {
                  secret: provisioning.secret,
                })}
              </p>
              <FormField
                control={verifyForm.control}
                name="code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {t("auth.two_factor.code_input_label")}
                    </FormLabel>
                    <FormControl>
                      <Input
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button
                type="submit"
                disabled={verifyForm.formState.isSubmitting}
              >
                {t("auth.two_factor.verify_and_enable")}
              </Button>
            </form>
          </Form>
        ) : (
          <Button
            type="button"
            onClick={() => {
              void handleStartEnable();
            }}
            disabled={enable2fa.isPending}
          >
            {t("auth.two_factor.enable")}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function SecurityKeysCard() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [isRegistering, setIsRegistering] = useState(false);

  // Raw SDK calls rather than the generated *Mutation()/*Options() helpers --
  // see the comment above SessionsCard's sessionsQuery for why.
  const credentialsQuery = useQuery({
    queryKey: listCredentialsApiV1AuthWebauthnCredentialsGetQueryKey(),
    queryFn: async () => {
      const { data } = await listCredentialsApiV1AuthWebauthnCredentialsGet({
        throwOnError: true,
      });
      return data;
    },
  });
  const revokeCredential = useMutation({
    mutationFn: async (credentialId: string) => {
      await revokeCredentialApiV1AuthWebauthnCredentialsCredentialIdDelete({
        path: { credential_id: credentialId },
        throwOnError: true,
      });
    },
  });

  const handleRegister = async (): Promise<void> => {
    setError(null);
    setIsRegistering(true);
    try {
      const { data: optionsEnvelope } =
        await registerOptionsApiV1AuthWebauthnRegisterOptionsPost({
          throwOnError: true,
        });
      const optionsData = optionsEnvelope.data;
      if (!optionsData) {
        throw new Error("WebAuthn register options response was empty.");
      }
      const credential = await startRegistration({
        optionsJSON:
          optionsData.options as unknown as PublicKeyCredentialCreationOptionsJSON,
      });
      await registerApiV1AuthWebauthnRegisterPost({
        body: {
          challenge_token: optionsData.challenge_token,
          credential: credential as unknown as Record<string, unknown>,
          nickname: null,
        },
        throwOnError: true,
      });
      await queryClient.invalidateQueries({
        queryKey: listCredentialsApiV1AuthWebauthnCredentialsGetQueryKey(),
      });
    } catch (caught) {
      setError(getErrorMessage(caught, "auth.security_keys.error_register"));
    } finally {
      setIsRegistering(false);
    }
  };

  const handleRevoke = async (credentialId: string): Promise<void> => {
    setError(null);
    try {
      await revokeCredential.mutateAsync(credentialId);
      await queryClient.invalidateQueries({
        queryKey: listCredentialsApiV1AuthWebauthnCredentialsGetQueryKey(),
      });
    } catch (caught) {
      setError(getErrorMessage(caught, "auth.security_keys.error_revoke"));
    }
  };

  const credentials = credentialsQuery.data?.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardDescription>{t("auth.security_keys.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {credentialsQuery.isLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : credentialsQuery.isError ? (
          <Alert variant="destructive">
            <AlertDescription>
              {getErrorMessage(
                credentialsQuery.error,
                "auth.security_keys.error_load",
              )}
            </AlertDescription>
          </Alert>
        ) : credentials.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("auth.security_keys.no_keys")}
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("auth.security_keys.name_col")}</TableHead>
                <TableHead>{t("auth.security_keys.last_used_col")}</TableHead>
                <TableHead className="text-end">
                  {t("auth.security_keys.action_col")}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {credentials.map((credential) => (
                <TableRow key={credential.id}>
                  <TableCell>
                    {credential.nickname ?? t("auth.security_keys.unnamed_key")}
                  </TableCell>
                  <TableCell>
                    {credential.last_used_at
                      ? formatDate(credential.last_used_at, i18n.language)
                      : t("auth.security_keys.never_used")}
                  </TableCell>
                  <TableCell className="text-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={revokeCredential.isPending}
                      onClick={() => {
                        void handleRevoke(credential.id);
                      }}
                    >
                      {t("auth.security_keys.revoke")}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}

        <Button
          type="button"
          onClick={() => {
            void handleRegister();
          }}
          disabled={isRegistering}
        >
          {t("auth.security_keys.register")}
        </Button>
      </CardContent>
    </Card>
  );
}

function ChangePasswordCard() {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const changePassword = useMutation({
    mutationFn: async (input: ChangePasswordInput) => {
      await changePasswordApiV1AuthChangePasswordPost({
        body: {
          current_password: input.currentPassword,
          new_password: input.newPassword,
        },
        throwOnError: true,
      });
    },
  });

  const form = useForm<ChangePasswordInput>({
    resolver: zodResolver(changePasswordSchema),
    defaultValues: { currentPassword: "", newPassword: "" },
  });

  const onSubmit = async (values: ChangePasswordInput): Promise<void> => {
    setFormError(null);
    setSuccess(false);
    try {
      await changePassword.mutateAsync(values);
      setSuccess(true);
      form.reset();
    } catch (caught) {
      setFormError(getErrorMessage(caught, "auth.change_password.error_default"));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardDescription>{t("auth.change_password.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {formError ? (
          <Alert variant="destructive">
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}
        {success ? (
          <Alert>
            <AlertDescription>
              {t("auth.change_password.success_message")}
            </AlertDescription>
          </Alert>
        ) : null}

        <Form {...form}>
          <form
            className="space-y-3"
            onSubmit={(event) => {
              void form.handleSubmit(onSubmit)(event);
            }}
            noValidate
          >
            <FormField
              control={form.control}
              name="currentPassword"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {t("auth.change_password.current_password_label")}
                  </FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete="current-password" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="newPassword"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {t("auth.change_password.new_password_label")}
                  </FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete="new-password" {...field} />
                  </FormControl>
                  <p className="text-xs text-muted-foreground">
                    {t("auth.change_password.password_helper")}
                  </p>
                  <PasswordStrengthMeter password={field.value} />
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" disabled={form.formState.isSubmitting}>
              {form.formState.isSubmitting
                ? t("auth.change_password.submit_pending")
                : t("auth.change_password.submit")}
            </Button>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}

function ChangeEmailCard() {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // The backend gates this request on require_recent_auth, so a session
  // idling past that window surfaces as errors.step_up_auth_required here --
  // shown via getErrorMessage's normal code->copy mapping, same as any other
  // error, rather than a dedicated re-auth prompt. current_password is
  // re-verified server-side too (T-226); an OAuth-only account submits it
  // blank and the backend skips that check for them (AuthService.
  // request_email_change).
  const requestEmailChange = useMutation({
    mutationFn: async (input: ChangeEmailInput) => {
      await requestEmailChangeApiV1AuthChangeEmailPost({
        body: {
          new_email: input.newEmail,
          current_password: input.currentPassword === "" ? null : input.currentPassword,
        },
        throwOnError: true,
      });
    },
  });

  const form = useForm<ChangeEmailInput>({
    resolver: zodResolver(changeEmailSchema),
    defaultValues: { currentPassword: "", newEmail: "" },
  });

  const onSubmit = async (values: ChangeEmailInput): Promise<void> => {
    setFormError(null);
    setSuccess(false);
    try {
      await requestEmailChange.mutateAsync(values);
      setSuccess(true);
      form.reset();
    } catch (caught) {
      setFormError(getErrorMessage(caught, "auth.change_email.error_default"));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardDescription>{t("auth.change_email.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {formError ? (
          <Alert variant="destructive">
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}
        {success ? (
          <Alert>
            <AlertDescription>{t("auth.change_email.success_message")}</AlertDescription>
          </Alert>
        ) : null}

        <Form {...form}>
          <form
            className="space-y-3"
            onSubmit={(event) => {
              void form.handleSubmit(onSubmit)(event);
            }}
            noValidate
          >
            <FormField
              control={form.control}
              name="currentPassword"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("auth.change_email.current_password_label")}</FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete="current-password" {...field} />
                  </FormControl>
                  <p className="text-xs text-muted-foreground">
                    {t("auth.change_email.current_password_helper")}
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="newEmail"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t("auth.change_email.new_email_label")}</FormLabel>
                  <FormControl>
                    <Input type="email" autoComplete="email" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" disabled={form.formState.isSubmitting}>
              {form.formState.isSubmitting
                ? t("auth.change_email.submit_pending")
                : t("auth.change_email.submit")}
            </Button>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}

function LinkedAccountsCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // Raw SDK calls rather than the generated *Mutation() helpers -- see the
  // comment above SessionsCard's sessionsQuery for why.
  const accountsQuery = useQuery({
    queryKey: listOauthAccountsApiV1AuthOauthAccountsGetQueryKey(),
    queryFn: async () => {
      const { data } = await listOauthAccountsApiV1AuthOauthAccountsGet({
        throwOnError: true,
      });
      return data;
    },
  });
  const unlink = useMutation({
    mutationFn: async (provider: string) => {
      await oauthUnlinkApiV1AuthOauthProviderUnlinkPost({
        path: { provider },
        throwOnError: true,
      });
    },
  });
  const [unlinkError, setUnlinkError] = useState<string | null>(null);

  const handleUnlink = async (provider: string): Promise<void> => {
    setUnlinkError(null);
    try {
      await unlink.mutateAsync(provider);
      await queryClient.invalidateQueries({
        queryKey: listOauthAccountsApiV1AuthOauthAccountsGetQueryKey(),
      });
    } catch (error) {
      setUnlinkError(getErrorMessage(error, "auth.linked_accounts.error_unlink"));
    }
  };

  const linkedProviders = new Set(
    (accountsQuery.data?.data ?? []).map((account) => account.provider),
  );

  return (
    <Card>
      <CardHeader>
        <CardDescription>{t("auth.linked_accounts.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {unlinkError ? (
          <Alert variant="destructive">
            <AlertDescription>{unlinkError}</AlertDescription>
          </Alert>
        ) : null}

        {accountsQuery.isLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : (
          _OAUTH_PROVIDERS.map((provider) => {
            const linked = linkedProviders.has(provider);
            return (
              <div key={provider} className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">
                    {t(`auth.linked_accounts.${provider}`)}
                  </p>
                  {linked ? null : (
                    <p className="text-xs text-muted-foreground">
                      {t("auth.linked_accounts.not_linked")}
                    </p>
                  )}
                </div>
                {linked ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={unlink.isPending}
                    onClick={() => {
                      void handleUnlink(provider);
                    }}
                  >
                    {t("auth.linked_accounts.unlink")}
                  </Button>
                ) : null}
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}

export function SecuritySettingsPage() {
  const { t } = useTranslation();
  const modules = useModules();
  return (
    <div className="max-w-xl space-y-6 p-6">
      <h1 className="text-xl font-semibold">{t("common.nav.security")}</h1>
      <ChangePasswordCard />
      <ChangeEmailCard />
      <SessionsCard />
      {modules.totp2fa ? <TwoFactorCard /> : null}
      {modules.totp2fa ? <SecurityKeysCard /> : null}
      {modules.oauth ? <LinkedAccountsCard /> : null}
    </div>
  );
}
