import { z } from "zod";

// Every .min()/.regex()/.email() message below is a @forge/i18n translation
// key, not English text -- react-hook-form's resolver surfaces these as
// `error.message`, and packages/ui's <FormMessage> is what actually runs
// them through t() at render time (T-069's "translate at the form level").
// Composition rules mirror backend/app/modules/auth/schemas.py so the web
// and mobile forms can reject obviously-bad input before round-tripping to
// the API. The backend remains authoritative: it additionally rejects the
// top-1000 breached-password denylist, which isn't duplicated here.
export const passwordSchema = z
  .string()
  .min(12, "validation.password.min_length")
  .regex(/[A-Z]/, "validation.password.uppercase")
  .regex(/[a-z]/, "validation.password.lowercase")
  .regex(/\d/, "validation.password.digit");

export const loginSchema = z.object({
  email: z.string().email("validation.email_invalid"),
  password: z.string().min(1, "validation.required"),
});

export const registerSchema = z.object({
  email: z.string().email("validation.email_invalid"),
  password: passwordSchema,
});

export const passwordResetSchema = z.object({
  token: z.string().min(1, "validation.required"),
  newPassword: passwordSchema,
});

export const forgotPasswordSchema = z.object({
  email: z.string().email("validation.email_invalid"),
});

export const magicLinkSchema = z.object({
  email: z.string().email("validation.email_invalid"),
});

export const profileSchema = z.object({
  fullName: z.string().min(1, "validation.required").max(255).optional(),
  avatarUrl: z.string().url().optional(),
  locale: z.string().min(2).max(10).optional(),
});

export type LoginInput = z.infer<typeof loginSchema>;
export type RegisterInput = z.infer<typeof registerSchema>;
export type PasswordResetInput = z.infer<typeof passwordResetSchema>;
export type ForgotPasswordInput = z.infer<typeof forgotPasswordSchema>;
export type MagicLinkInput = z.infer<typeof magicLinkSchema>;
export type ProfileInput = z.infer<typeof profileSchema>;
