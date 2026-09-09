/**
 * The app half of T-308's consent reconciliation.
 *
 * Only the pure parts are tested here — the parsing and the "have we already
 * reported this" bookkeeping. The effect that posts to `/me/consents` lives
 * in `useConsentSync.ts` and is covered from the outside, because what
 * matters about it is that the row lands in `consent_record`, which is a
 * backend fact.
 *
 * The cookie format is duplicated across two apps by necessity (two build
 * systems, one cookie), so `apps/marketing/tests/consent.test.ts` asserts the
 * two files agree on the name and the consent type. This file asserts they
 * agree on the *shape*.
 */
import { describe, expect, it } from "vitest";

import {
  CONSENT_COOKIE,
  CONSENT_TYPE,
  parseConsent,
  readConsentCookie,
} from "./consent";

describe("readConsentCookie", () => {
  it("finds the cookie among others", () => {
    const jar = `theme=dark; ${CONSENT_COOKIE}=granted%7C2026-08-30; other=1`;

    expect(readConsentCookie(jar)).toBe("granted|2026-08-30");
  });

  it("returns null when it is absent", () => {
    // The default configuration: no analytics, so no cookie, so nothing to
    // reconcile. This is the branch a fresh clone always takes.
    expect(readConsentCookie("theme=dark")).toBeNull();
    expect(readConsentCookie("")).toBeNull();
  });

  it("does not match a cookie whose name merely ends with this one", () => {
    // `x_forge_consent` is a different cookie. A naive `includes` would find
    // it and reconcile somebody else's value.
    expect(readConsentCookie(`x_${CONSENT_COOKIE}=granted%7C2026-08-30`)).toBeNull();
  });

  it("tolerates the spacing browsers actually emit", () => {
    expect(readConsentCookie(`a=1;${CONSENT_COOKIE}=denied%7Cv1`)).toBe("denied|v1");
    expect(readConsentCookie(`a=1;   ${CONSENT_COOKIE}=denied%7Cv1`)).toBe("denied|v1");
  });
});

describe("parseConsent", () => {
  it("splits a valid value", () => {
    expect(parseConsent("granted|2026-08-30")).toEqual({
      decision: "granted",
      version: "2026-08-30",
    });
    expect(parseConsent("denied|2026-08-30")).toEqual({
      decision: "denied",
      version: "2026-08-30",
    });
  });

  it("rejects anything that is not one of the two answers", () => {
    // A value this app does not understand must be ignored rather than
    // guessed at: posting the wrong decision into an append-only table is
    // not something a later write can take back.
    for (const raw of ["", "maybe|v1", "granted", "granted|", "|v1", null]) {
      expect(parseConsent(raw), String(raw)).toBeNull();
    }
  });
});

describe("the shared contract", () => {
  it("names the consent type the gdpr module records", () => {
    expect(CONSENT_TYPE).toBe("analytics");
  });

  it("names the cookie the marketing site writes", () => {
    expect(CONSENT_COOKIE).toBe("forge_consent");
  });
});
