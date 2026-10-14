import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { startAuthentication } from "@simplewebauthn/browser";
import type { PublicKeyCredentialRequestOptionsJSON } from "@simplewebauthn/browser";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import {
  login2FaApiV1Auth2FaLoginPost,
  loginApiV1AuthWebauthnLoginPost,
  loginOptionsApiV1AuthWebauthnLoginOptionsPost,
} from "@forge/api-client";
import type { UserPublic } from "@forge/api-client";
import {
  Alert,
  AlertDescription,
  Button,
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  Input,
  Separator,
} from "@forge/ui";

import { getErrorMessage } from "./getErrorMessage";

const codeSchema = z.object({
  code: z.string().min(6, "validation.code_length"),
});
type CodeInput = z.infer<typeof codeSchema>;

interface TwoFactorChallengeProps {
  tempToken: string;
  onSuccess: (accessToken: string, user: UserPublic) => void;
}

// T-217: the second leg of a 2FA-gated login -- AuthService.login() (T-018)
// hands the tempToken this component receives back instead of real tokens
// whenever the account has TOTP or a WebAuthn credential enrolled (either is
// accepted here; the backend doesn't say which the account has, so both
// options are always offered).
export function TwoFactorChallenge({
  tempToken,
  onSuccess,
}: TwoFactorChallengeProps) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [isUsingSecurityKey, setIsUsingSecurityKey] = useState(false);

  const form = useForm<CodeInput>({
    resolver: zodResolver(codeSchema),
    defaultValues: { code: "" },
  });

  const handleCodeSubmit = async (values: CodeInput): Promise<void> => {
    setError(null);
    try {
      const { data: envelope } = await login2FaApiV1Auth2FaLoginPost({
        body: { temp_token: tempToken, code: values.code },
        throwOnError: true,
      });
      const data = envelope.data;
      if (!data) {
        throw new Error("2FA login response was empty.");
      }
      onSuccess(data.access_token, data.user);
    } catch (caught) {
      setError(getErrorMessage(caught, "auth.two_factor_challenge.error_code"));
    }
  };

  const handleUseSecurityKey = async (): Promise<void> => {
    setError(null);
    setIsUsingSecurityKey(true);
    try {
      const { data: optionsEnvelope } =
        await loginOptionsApiV1AuthWebauthnLoginOptionsPost({
          body: { temp_token: tempToken },
          throwOnError: true,
        });
      const optionsData = optionsEnvelope.data;
      if (!optionsData) {
        throw new Error("WebAuthn login options response was empty.");
      }
      const assertion = await startAuthentication({
        optionsJSON:
          optionsData.options as unknown as PublicKeyCredentialRequestOptionsJSON,
      });
      const { data: loginEnvelope } = await loginApiV1AuthWebauthnLoginPost({
        body: {
          challenge_token: optionsData.challenge_token,
          credential: assertion as unknown as Record<string, unknown>,
        },
        throwOnError: true,
      });
      const loginData = loginEnvelope.data;
      if (!loginData) {
        throw new Error("WebAuthn login response was empty.");
      }
      onSuccess(loginData.access_token, loginData.user);
    } catch (caught) {
      setError(
        getErrorMessage(caught, "auth.two_factor_challenge.error_security_key"),
      );
    } finally {
      setIsUsingSecurityKey(false);
    }
  };

  return (
    <>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <Form {...form}>
        <form
          className="space-y-3"
          onSubmit={(event) => {
            void form.handleSubmit(handleCodeSubmit)(event);
          }}
          noValidate
        >
          <FormField
            control={form.control}
            name="code"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {t("auth.two_factor_challenge.code_label")}
                </FormLabel>
                <FormControl>
                  <Input
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    autoFocus
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button
            type="submit"
            size="lg"
            className="w-full"
            disabled={form.formState.isSubmitting}
          >
            {t("auth.two_factor_challenge.submit")}
          </Button>
        </form>
      </Form>

      <div className="flex items-center gap-2">
        <Separator className="flex-1" />
        <span className="text-xs text-muted-foreground">
          {t("auth.two_factor_challenge.separator")}
        </span>
        <Separator className="flex-1" />
      </div>

      <Button
        type="button"
        variant="outline"
        size="lg"
        className="w-full"
        disabled={isUsingSecurityKey}
        onClick={() => {
          void handleUseSecurityKey();
        }}
      >
        {t("auth.two_factor_challenge.use_security_key")}
      </Button>
    </>
  );
}
