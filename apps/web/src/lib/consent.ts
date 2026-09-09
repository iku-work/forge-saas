/**
 * The app half of T-308's consent story: reconciling an anonymous visitor's
 * decision with their account, once they have one.
 *
 * The marketing site (`apps/marketing/src/lib/consent.ts`) writes a decision
 * to a first-party cookie, because an anonymous visitor has nowhere else to
 * put one. The moment a session exists, that decision belongs in the `gdpr`
 * module's `consent_record` table instead -- it is append-only (T-032), so it
 * is the thing that can answer "what had they agreed to, and when" months
 * later, which a cookie a person can clear cannot.
 *
 * This file is deliberately small, and deliberately one-directional. It reads
 * the cookie and posts it once per browser per decision. It does not write
 * the cookie, does not render a banner, and does not decide anything: the
 * marketing site owns the decision, and the record is where the decision goes
 * to be kept.
 *
 * **The cookie is only visible here if `PUBLIC_CONSENT_COOKIE_DOMAIN` was set
 * on the marketing build**, because T-307 puts the two on different hosts. It
 * is not set in the template's default configuration, so in a fresh clone
 * this hook reads nothing and does nothing -- which is correct, not broken:
 * the default configuration has no analytics, so there is no decision to
 * reconcile. `docs/runbooks/analytics-and-consent.md` is where that knob is
 * explained to whoever turns analytics on.
 */

/** Must match `COOKIE_NAME` in apps/marketing/src/lib/consent.ts. */
export const CONSENT_COOKIE = "forge_consent";

/** Must match `CONSENT_TYPE` there. `consent_record.consent_type` is a free
 * string, so a second spelling would split one person's history in two. */
export const CONSENT_TYPE = "analytics";

/**
 * Where "this browser has already been reconciled" is remembered.
 *
 * `localStorage`, not a cookie and not server state, and the value is the
 * decision-and-version that was posted. Two properties fall out of that:
 * posting is idempotent per browser (the table is append-only, so a hook that
 * fired on every mount would write a row per page load), and a *changed*
 * decision still posts, because the remembered value no longer matches.
 */
const SYNCED_KEY = "forge.consent.synced";

export interface ParsedConsent {
  decision: "granted" | "denied";
  version: string;
}

/** `granted|2026-08-30` -> its two halves, or null for anything else. */
export function parseConsent(raw: string | null): ParsedConsent | null {
  if (!raw) {
    return null;
  }
  const [decision, version] = raw.split("|");
  if ((decision !== "granted" && decision !== "denied") || !version) {
    return null;
  }
  return { decision, version };
}

/**
 * The consent cookie's raw value, or null.
 *
 * Takes the cookie string rather than reading `document.cookie` so the
 * parsing is testable without a DOM, which is most of what there is to get
 * wrong here.
 */
export function readConsentCookie(
  cookieString: string,
  name: string = CONSENT_COOKIE,
): string | null {
  for (const part of cookieString.split(";")) {
    const separator = part.indexOf("=");
    if (separator === -1) {
      continue;
    }
    if (part.slice(0, separator).trim() === name) {
      return decodeURIComponent(part.slice(separator + 1));
    }
  }
  return null;
}

/**
 * Has this browser already reported this exact decision?
 *
 * A `localStorage` that throws (a private window, storage disabled) is
 * treated as "not yet", which posts one extra row rather than losing the
 * decision -- the safe direction for a table whose purpose is evidence.
 */
export function alreadySynced(value: string): boolean {
  try {
    return window.localStorage.getItem(SYNCED_KEY) === value;
  } catch {
    return false;
  }
}

export function markSynced(value: string): void {
  try {
    window.localStorage.setItem(SYNCED_KEY, value);
  } catch {
    // Nothing to do and nothing to report: the consent record was written,
    // which is the part that matters. The cost of a failure here is one
    // duplicate row on the next visit.
  }
}
