import { Text, TextInput, type TextInputProps, View } from "react-native";
import {
  Controller,
  type Control,
  type FieldValues,
  type Path,
} from "react-hook-form";
import { useTranslation } from "react-i18next";

interface FormFieldProps<
  TFieldValues extends FieldValues,
  TName extends Path<TFieldValues>,
> {
  control: Control<TFieldValues>;
  name: TName;
  label: string;
  hint?: string;
  secureTextEntry?: boolean;
  autoComplete?: TextInputProps["autoComplete"];
  keyboardType?: TextInputProps["keyboardType"];
}

export function FormField<
  TFieldValues extends FieldValues,
  TName extends Path<TFieldValues>,
>({
  control,
  name,
  label,
  hint,
  secureTextEntry,
  autoComplete,
  keyboardType,
}: FormFieldProps<TFieldValues, TName>) {
  const { t } = useTranslation();

  return (
    <Controller
      control={control}
      name={name}
      render={({ field, fieldState }) => (
        <View className="gap-1">
          <Text className="text-sm font-medium text-slate-700">{label}</Text>
          <TextInput
            accessibilityLabel={label}
            accessibilityHint={
              hint ??
              t("common.form.enter_value_hint", { field: label.toLowerCase() })
            }
            value={typeof field.value === "string" ? field.value : ""}
            onChangeText={field.onChange}
            onBlur={field.onBlur}
            secureTextEntry={secureTextEntry}
            autoComplete={autoComplete}
            keyboardType={keyboardType}
            autoCapitalize="none"
            className="rounded-md border border-slate-300 px-3 py-2 text-base text-slate-900"
          />
          {hint ? <Text className="text-xs text-slate-400">{hint}</Text> : null}
          {fieldState.error ? (
            <Text accessibilityRole="alert" className="text-xs text-red-600">
              {/* @forge/validation's zod resolvers return translation keys,
                  not English sentences -- mirrors packages/ui's FormMessage. */}
              {t(fieldState.error.message ?? "")}
            </Text>
          ) : null}
        </View>
      )}
    />
  );
}
