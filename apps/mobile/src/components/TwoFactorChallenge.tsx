import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { Text, View } from "react-native";
import { z } from "zod";

import type { UserPublic } from "@forge/api-client";

import { completeTwoFactorLogin } from "../lib/auth";
import { getErrorMessage } from "../lib/getErrorMessage";
import { Banner } from "./Banner";
import { Button } from "./Button";
import { FormField } from "./FormField";

// Deliberately looser than six digits: the same field accepts a backup
// code, which is longer and not numeric. The backend tries both
// (AuthService.confirm_login_2fa), so validating for one shape here would
// reject the other before it was ever sent -- which is exactly the kind of
// client-side guess that locks out the user who needs the fallback most.
const challengeSchema = z.object({
  code: z.string().min(6, "validation.code_length"),
});
type ChallengeInput = z.infer<typeof challengeSchema>;

interface TwoFactorChallengeProps {
  tempToken: string;
  onSuccess: (user: UserPublic) => void;
}

/**
 * The second leg of a 2FA-gated sign-in on mobile.
 *
 * Mirrors web's TwoFactorChallenge with one deliberate omission: no
 * security-key option. WebAuthn is a browser API and React Native has no
 * equivalent without a native passkey module and a custom dev build, so
 * the screen says where to finish instead of offering a button that cannot
 * work.
 */
export function TwoFactorChallenge({
  tempToken,
  onSuccess,
}: TwoFactorChallengeProps) {
  const { t } = useTranslation();
  const [formError, setFormError] = useState<string | null>(null);

  const { control, handleSubmit, formState } = useForm<ChallengeInput>({
    resolver: zodResolver(challengeSchema),
    defaultValues: { code: "" },
  });

  const onSubmit = async (values: ChallengeInput): Promise<void> => {
    setFormError(null);
    try {
      const user = await completeTwoFactorLogin(tempToken, values.code);
      onSuccess(user);
    } catch (error) {
      setFormError(
        getErrorMessage(error, "auth.two_factor_challenge.error_code"),
      );
    }
  };

  return (
    <>
      {formError ? <Banner variant="error">{formError}</Banner> : null}

      <View className="gap-4">
        <FormField
          control={control}
          name="code"
          label={t("auth.two_factor_challenge.code_label")}
          hint={t("auth.two_factor_challenge.code_hint")}
          autoComplete="one-time-code"
          keyboardType="number-pad"
        />
        <Button
          label={
            formState.isSubmitting
              ? t("auth.two_factor_challenge.submit_pending")
              : t("auth.two_factor_challenge.submit")
          }
          hint={t("auth.two_factor_challenge.submit_hint")}
          disabled={formState.isSubmitting}
          onPress={() => {
            void handleSubmit(onSubmit)();
          }}
        />
        <Text className="text-center text-xs text-slate-500">
          {t("auth.two_factor_challenge.security_key_hint")}
        </Text>
      </View>
    </>
  );
}
