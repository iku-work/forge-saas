import type { ReactNode } from "react";
import { Text, View } from "react-native";

import { cn } from "@forge/utils";

interface BannerProps {
  variant?: "error" | "info";
  children: ReactNode;
}

export function Banner({ variant = "info", children }: BannerProps) {
  return (
    <View
      accessibilityRole="alert"
      className={cn(
        "rounded-md border p-3",
        variant === "error"
          ? "border-red-200 bg-red-50"
          : "border-slate-200 bg-slate-50",
      )}
    >
      <Text
        className={cn(
          "text-sm",
          variant === "error" ? "text-red-700" : "text-slate-700",
        )}
      >
        {children}
      </Text>
    </View>
  );
}
