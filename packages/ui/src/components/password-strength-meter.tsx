import { useTranslation } from "react-i18next";

import { cn } from "@forge/utils";

// T-216/ASVS V2.1.8: a purely informational strength hint -- the backend's
// actual gate is length (>=12, <=128 chars) plus a breached-password
// denylist (backend/app/modules/auth/schemas.py), *not* character-class
// composition (ASVS V2.1.9 recommends against composition rules), so this
// meter rewards length and variety without requiring either.
export type PasswordStrengthScore = 0 | 1 | 2 | 3;

const _MIN_LENGTH = 12;

export function scorePasswordStrength(password: string): PasswordStrengthScore {
  if (password.length < _MIN_LENGTH) return 0;

  let variety = 0;
  if (/[a-z]/.test(password)) variety += 1;
  if (/[A-Z]/.test(password)) variety += 1;
  if (/\d/.test(password)) variety += 1;
  if (/[^a-zA-Z0-9]/.test(password)) variety += 1;

  if (password.length >= 16 && variety >= 3) return 3;
  if (password.length >= 14 || variety >= 3) return 2;
  return 1;
}

const _SEGMENT_COLORS: Record<PasswordStrengthScore, string> = {
  0: "bg-destructive",
  1: "bg-destructive",
  2: "bg-warning",
  3: "bg-success",
};

const _LABEL_KEYS: Record<PasswordStrengthScore, string> = {
  0: "auth.password_field.strength_weak",
  1: "auth.password_field.strength_fair",
  2: "auth.password_field.strength_good",
  3: "auth.password_field.strength_strong",
};

export interface PasswordStrengthMeterProps {
  password: string;
}

export function PasswordStrengthMeter({
  password,
}: PasswordStrengthMeterProps) {
  const { t } = useTranslation();
  if (password.length === 0) return null;

  const score = scorePasswordStrength(password);
  const segments = [0, 1, 2, 3] as const;

  return (
    <div className="space-y-1" aria-live="polite">
      <div className="flex gap-1">
        {segments.map((segment) => (
          <div
            key={segment}
            className={cn(
              "h-1.5 flex-1 rounded-full bg-muted",
              segment <= score && _SEGMENT_COLORS[score],
            )}
          />
        ))}
      </div>
      <p className="text-xs text-muted-foreground">{t(_LABEL_KEYS[score])}</p>
    </div>
  );
}
