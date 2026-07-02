import { Pressable, Text } from "react-native";

import { cn } from "@forge/utils";

interface ButtonProps {
  label: string;
  hint: string;
  onPress: () => void;
  disabled?: boolean;
  variant?: "primary" | "outline";
}

export function Button({
  label,
  hint,
  onPress,
  disabled = false,
  variant = "primary",
}: ButtonProps) {
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityHint={hint}
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      onPress={onPress}
      disabled={disabled}
      className={cn(
        "items-center rounded-md px-4 py-3",
        variant === "primary"
          ? "bg-slate-900"
          : "border border-slate-300 bg-white",
        disabled && "opacity-50",
      )}
    >
      <Text
        className={cn(
          "text-base font-medium",
          variant === "primary" ? "text-white" : "text-slate-900",
        )}
      >
        {label}
      </Text>
    </Pressable>
  );
}
