import { describe, expect, it } from "vitest";

import { getErrorMessage } from "./getErrorMessage";

// Mirrors apps/web/src/test/getErrorMessage.test.ts. T-218: message_key
// (e.g. "errors.email_unchanged") lets otherwise-generic error codes (many
// DomainError subclasses share one `code` -- every ConflictError is
// "conflict", every AuthenticationError is "authentication_failed")
// surface their actual, more specific catalog entry. src/test/setup.ts's
// beforeAll already calls initI18n(), so these exercise the real
// packages/i18n catalog, not a stub.
describe("getErrorMessage", () => {
  it("prefers message_key over the coarser code when both have translations", () => {
    const error = {
      error: { code: "conflict", message_key: "errors.email_unchanged", message: "unused" },
    };

    expect(getErrorMessage(error, "auth.change_email.error_default")).toBe(
      "This is already your current email address.",
    );
  });

  it("falls back to code when message_key has no catalog entry", () => {
    const error = {
      error: { code: "conflict", message_key: "errors.not_a_real_key", message: "unused" },
    };

    expect(getErrorMessage(error, "auth.change_email.error_default")).toBe(
      "This action conflicts with the resource's current state.",
    );
  });

  it("falls back to code when message_key is absent entirely (older/mocked responses)", () => {
    const error = { error: { code: "conflict", message: "unused" } };

    expect(getErrorMessage(error, "auth.change_email.error_default")).toBe(
      "This action conflicts with the resource's current state.",
    );
  });

  it("distinguishes two errors that share the same code via message_key", () => {
    const emailUnchanged = {
      error: { code: "conflict", message_key: "errors.email_unchanged", message: "unused" },
    };
    const totpAlreadyEnabled = {
      error: {
        code: "conflict",
        message_key: "errors.totp_already_enabled",
        message: "unused",
      },
    };

    expect(getErrorMessage(emailUnchanged, "auth.change_email.error_default")).not.toBe(
      getErrorMessage(totpAlreadyEnabled, "auth.change_email.error_default"),
    );
  });
});
