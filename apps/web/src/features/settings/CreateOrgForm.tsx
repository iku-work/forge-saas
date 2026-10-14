import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { z } from "zod";

import { createOrgApiV1OrgsPost } from "@forge/api-client";
import { useOrg } from "@forge/hooks";
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
} from "@forge/ui";

import { getErrorMessage } from "../auth/getErrorMessage";

const createOrgSchema = z.object({
  name: z.string().min(1, "validation.org_name_required").max(255),
});
type CreateOrgInput = z.infer<typeof createOrgSchema>;

interface CreateOrgFormProps {
  onCreated?: () => void;
  /** "lg" on the full-page first-organization screen, to match the account
   *  pages' buttons; the default inside the switcher's dialog. */
  buttonSize?: "default" | "lg";
}

export function CreateOrgForm({
  onCreated,
  buttonSize = "default",
}: CreateOrgFormProps) {
  const { t } = useTranslation();
  const { refreshOrgs } = useOrg();
  const [formError, setFormError] = useState<string | null>(null);
  // Calls the raw SDK function directly rather than the generated
  // createOrgApiV1OrgsPostMutation() helper -- @forge/api-client ships a
  // pre-bundled dist build, so that helper's internal reference to
  // createOrgApiV1OrgsPost is resolved at build time, not at import time.
  // vi.mock("@forge/api-client", ...) can only intercept imports this file
  // itself makes, so going through the generated mutation helper would
  // silently hit the real, unmocked SDK call in tests.
  const createOrg = useMutation({
    mutationFn: async (name: string) => {
      const { data } = await createOrgApiV1OrgsPost({
        body: { name },
        throwOnError: true,
      });
      return data;
    },
  });

  const form = useForm<CreateOrgInput>({
    resolver: zodResolver(createOrgSchema),
    defaultValues: { name: "" },
  });

  const onSubmit = async (values: CreateOrgInput): Promise<void> => {
    setFormError(null);
    try {
      await createOrg.mutateAsync(values.name);
      // useOrg's store doesn't know about this org until the list is
      // re-fetched -- it then becomes the only (or newest) entry, so
      // bootstrap's own org-picking logic selects it without extra wiring.
      await refreshOrgs();
      form.reset();
      onCreated?.();
    } catch (error) {
      setFormError(getErrorMessage(error, "tenancy.org_create.error_default"));
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
              <FormLabel>{t("tenancy.org_create.name_label")}</FormLabel>
              <FormControl>
                <Input
                  autoComplete="organization"
                  data-testid="create-org-name-input"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button
          type="submit"
          size={buttonSize}
          className="w-full"
          disabled={form.formState.isSubmitting}
          data-testid="create-org-submit"
        >
          {form.formState.isSubmitting
            ? t("tenancy.org_create.submit_pending")
            : t("tenancy.org_create.submit")}
        </Button>
      </form>
    </Form>
  );
}
