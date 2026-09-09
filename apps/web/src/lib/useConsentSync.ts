/**
 * Posts an anonymous visitor's analytics consent into `consent_record` once
 * they have a session (T-308).
 *
 * Mounted in `AppRoot`, not hooked into the login mutation, and the
 * difference matters: a session can appear through a password login, an OAuth
 * callback, a magic link, a mobile handoff, or a refresh on a page the user
 * never left. All of them end up rendering this tree with a `user`, and none
 * of them share a code path. Watching for the user is the only place that
 * covers all five.
 *
 * The pure parts live in `./consent.ts`; this file is the effect.
 */
import { useEffect } from "react";

import { recordConsentApiV1MeConsentsPost } from "@forge/api-client";
import { useAuth } from "@forge/hooks";

import {
  CONSENT_COOKIE,
  CONSENT_TYPE,
  alreadySynced,
  markSynced,
  parseConsent,
  readConsentCookie,
} from "./consent";

/**
 * Reconciles the consent cookie with the signed-in user's consent record.
 *
 * Renders nothing and returns nothing: it is an effect with no UI, which is
 * why it is a hook rather than a component. Every branch below is a
 * deliberate no-op:
 *
 *  - no user yet (still bootstrapping, or nobody signed in) -- there is
 *    nothing to attach a decision to;
 *  - no cookie -- either no analytics are configured (the template's default)
 *    or the cookie is scoped to this host only and the decision was made on
 *    the marketing one, which is what `PUBLIC_CONSENT_COOKIE_DOMAIN` exists
 *    to fix;
 *  - already reported -- `consent_record` is append-only, so a hook that
 *    posted on every mount would write a row per page load.
 *
 * A failed request is swallowed. The cookie is untouched, so the next visit
 * tries again, and the visitor's actual experience of the site is decided by
 * the cookie either way -- a backend that is down must not become a consent
 * banner that reappears.
 */
export function useConsentSync(): void {
  const { user } = useAuth();

  useEffect(() => {
    if (!user) {
      return;
    }
    const raw = readConsentCookie(document.cookie, CONSENT_COOKIE);
    const consent = parseConsent(raw);
    if (!consent || !raw || alreadySynced(raw)) {
      return;
    }

    let cancelled = false;
    void recordConsentApiV1MeConsentsPost({
      body: {
        consent_type: CONSENT_TYPE,
        granted: consent.decision === "granted",
        version: consent.version,
      },
    })
      .then((response) => {
        // The generated client resolves rather than throws on a non-2xx, so
        // "no error" is the condition, not "the promise settled".
        if (!cancelled && !response.error) {
          markSynced(raw);
        }
      })
      .catch(() => {
        // See the docstring: the visitor's experience is driven by the
        // cookie, and the next visit retries.
      });

    return () => {
      cancelled = true;
    };
  }, [user]);
}
