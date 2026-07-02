import type { ReactNode } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Text,
  View,
} from "react-native";

interface AuthCardProps {
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
}

export function AuthCard({
  title,
  description,
  children,
  footer,
}: AuthCardProps) {
  return (
    <KeyboardAvoidingView
      className="flex-1 bg-white"
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView
        contentContainerClassName="flex-1 items-center justify-center p-6"
        keyboardShouldPersistTaps="handled"
      >
        <View className="w-full max-w-sm gap-4">
          <View className="gap-1">
            <Text
              accessibilityRole="header"
              className="text-xl font-semibold text-slate-900"
            >
              {title}
            </Text>
            {description ? (
              <Text className="text-sm text-slate-500">{description}</Text>
            ) : null}
          </View>
          <View className="gap-4">{children}</View>
          {footer ? <View className="gap-2">{footer}</View> : null}
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
