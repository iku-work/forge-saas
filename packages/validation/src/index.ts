import { z } from "zod";

// Every .min()/.max()/.email() message below is a @forge/i18n translation
// key, not English text -- react-hook-form's resolver surfaces these as
// `error.message`, and packages/ui's <FormMessage> is what actually runs
// them through t() at render time (T-069's "translate at the form level").
// Length bounds mirror backend/app/modules/auth/schemas.py so the web and
// mobile forms can reject obviously-bad input before round-tripping to the
// API. T-216: no composition regex (no forced upper/lower/digit) -- ASVS
// V2.1.9/NIST SP 800-63B recommend length + breach-list checks over
// composition rules. The backend remains authoritative: it additionally
// rejects the top-1000 breached-password denylist, which isn't duplicated
// here.
export const passwordSchema = z
  .string()
  .min(12, "validation.password.min_length")
  .max(128, "validation.password.max_length");

// Typed twice wherever a password is chosen, so a slip of the finger is caught
// here rather than at the next sign-in. Client-side only: the API takes the
// password once, and nothing here is sent to it.
const confirmPasswordSchema = z.string().min(1, "validation.required");

const MISMATCH = {
  message: "validation.password.mismatch",
  path: ["confirmPassword"],
};

export const loginSchema = z.object({
  email: z.string().email("validation.email_invalid"),
  password: z.string().min(1, "validation.required"),
});

export const registerSchema = z
  .object({
    email: z.string().email("validation.email_invalid"),
    password: passwordSchema,
    confirmPassword: confirmPasswordSchema,
  })
  .refine((v) => v.password === v.confirmPassword, MISMATCH);

export const passwordResetSchema = z
  .object({
    token: z.string().min(1, "validation.required"),
    newPassword: passwordSchema,
    confirmPassword: confirmPasswordSchema,
  })
  .refine((v) => v.newPassword === v.confirmPassword, MISMATCH);

export const changePasswordSchema = z
  .object({
    currentPassword: z.string().min(1, "validation.current_password_required"),
    newPassword: passwordSchema,
    confirmPassword: confirmPasswordSchema,
  })
  .refine((v) => v.newPassword === v.confirmPassword, MISMATCH);

export const forgotPasswordSchema = z.object({
  email: z.string().email("validation.email_invalid"),
});

export const changeEmailSchema = z.object({
  // T-226: optional, unlike changePasswordSchema's currentPassword -- an
  // OAuth-only account (no password set) legitimately submits this blank,
  // and the backend (not this schema) is what knows which case applies.
  currentPassword: z.string().optional(),
  newEmail: z.string().email("validation.email_invalid"),
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
export type ChangePasswordInput = z.infer<typeof changePasswordSchema>;
export type ForgotPasswordInput = z.infer<typeof forgotPasswordSchema>;
export type ChangeEmailInput = z.infer<typeof changeEmailSchema>;
export type MagicLinkInput = z.infer<typeof magicLinkSchema>;
export type ProfileInput = z.infer<typeof profileSchema>;
