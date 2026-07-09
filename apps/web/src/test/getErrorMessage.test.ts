import { describe, expect, it } from "vitest";

import { getErrorMessage } from "../features/auth/getErrorMessage";

// T-218: message_key (e.g. "errors.email_unchanged") lets otherwise-generic
// error codes (many DomainError subclasses share one `code` -- every
// ConflictError is "conflict", every AuthenticationError is
// "authentication_failed") surface their actual, more specific catalog
// entry. setup.ts's beforeAll already calls initI18n(), so these exercise
// the real packages/i18n catalog, not a stub.
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

  it("falls back to the caller's fallbackKey when neither message_key nor code resolve", () => {
    const error = { error: { code: "not_a_real_code", message: "unused" } };

    expect(getErrorMessage(error, "auth.change_email.error_default")).toBe(
      "Unable to start the email change. Please try again.",
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

  it("returns the Error instance's own message when the value isn't an API envelope", () => {
    expect(getErrorMessage(new Error("network down"), "auth.change_email.error_default")).toBe(
      "network down",
    );
  });

  it("falls back to fallbackKey for a completely unrecognized value", () => {
    expect(getErrorMessage("not an error at all", "auth.change_email.error_default")).toBe(
      "Unable to start the email change. Please try again.",
    );
  });
});
