/**
 * Whether this site measures anything, and what that costs a visitor (T-308).
 *
 * **The default is `none`, and that is the feature.** A marketing site is
 * where tracking lands, and a template that shipped a `<script>` tag as an
 * exercise for the reader would be shipping a consent banner, a CLS
 * regression, a `consent_record` row and a legal review to everyone who ever
 * forks it. What ships instead is the *integration point*: one object, three
 * documented shapes, and a build that proves the default sets no cookie and
 * contacts no third party.
 *
 * A banner avoided is worth more than a banner implemented well. So the two
 * paths below are deliberately unequal in effort:
 *
 * 1. **Cookieless** (`consent: "not-required"`). A provider that measures
 *    without identifying anyone -- no cookie, no cross-site identifier, no
 *    personal data -- needs no consent under ePrivacy Article 5(3) and no
 *    banner. Wiring one is two lines here and nothing else changes.
 * 2. **Cookie-setting** (`consent: "required"`). Everything the law asks for
 *    is then already built: nothing loads before consent, the banner is
 *    pre-rendered so it costs no layout shift, the answer is recorded, and
 *    withdrawing is a permanent footer link away.
 *
 * Whichever is chosen, ADR-0004's fifth decision applies: a third-party
 * script is code from someone else's server, executing on this origin, on the
 * critical path,
 * changeable without a deploy. It needs a written justification, and
 * "marketing asked for it" is not one. `scripts/check-budget.mjs` reads
 * `hosts` below as its subresource allowlist and
 * `scripts/caddy-site.mjs` reads it into the CSP, so a provider added here is
 * a provider the build and the edge both know about -- and one added anywhere
 * *else* fails the build.
 *
 * See `docs/runbooks/analytics-and-consent.md` for the decision as a product
 * owner has to make it, without reading any of this.
 */

/**
 * Does loading this provider require consent first?
 *
 * The question is not "does it collect personal data" but the narrower one
 * the ePrivacy Directive actually asks: does it read or write anything on the
 * visitor's device? A cookieless provider that derives a daily visitor hash
 * server-side stores nothing on the device and is `not-required`; anything
 * that sets a cookie, writes `localStorage`, or reads a device fingerprint is
 * `required`, whatever its marketing page says about privacy.
 */
export type ConsentRequirement = "not-required" | "required";

export interface AnalyticsProvider {
  /**
   * A short identifier for this provider, used in the consent record and in
   * the banner's copy. Not a display name: it is stored, so it must be stable
   * across a rename.
   */
  readonly id: string;
  /**
   * The script to load, absolute. `null` means "nothing to load" -- either
   * there is no provider (`NONE` below) or the measurement happens somewhere
   * this site does not: an edge worker, a log pipeline, the CDN's own
   * analytics.
   */
  readonly scriptSrc: string | null;
  /**
   * Attributes to put on that `<script>` tag. Most providers want a site id
   * here (`data-domain`, `data-website-id`, ...). Values are rendered into
   * HTML, so keep them to literals.
   */
  readonly scriptAttributes: Readonly<Record<string, string>>;
  /**
   * Every host this provider contacts -- the script's own, and any it calls
   * afterwards. Both must be listed: `check-budget.mjs` allows the first and
   * the CSP's `connect-src` allows the second, and a provider that beacons to
   * a different host than it loads from (most of them do) is otherwise
   * silently blocked in production and works fine locally.
   */
  readonly hosts: readonly string[];
  /** See ConsentRequirement. */
  readonly consent: ConsentRequirement;
  /**
   * Cookie names this provider sets, as exact names or `name*` prefixes.
   *
   * Withdrawal deletes them (`src/lib/consent.ts`), which is the half of
   * "withdrawal is as easy as granting" that most implementations skip: a
   * banner that flips a flag and leaves the cookies in place has not
   * withdrawn anything. Empty for a cookieless provider, and
   * `tests/consent.test.ts` fails the build on a `required` provider that
   * declares none -- if it needs consent, it stores *something*, and the name
   * of that something belongs here.
   */
  readonly cookies: readonly string[];
  /**
   * The ADR-0004 justification, in one sentence. Not decoration: it is what
   * the next person reads when they ask why this site talks to someone else's
   * server.
   */
  readonly justification: string;
}

/**
 * The default. No provider, no script, no cookie, no banner.
 *
 * `tests/consent.test.ts` holds the built output to it: zero third-party
 * hosts anywhere in `dist/`, and no consent banner in any page.
 */
export const NONE: AnalyticsProvider = {
  id: "none",
  scriptSrc: null,
  scriptAttributes: {},
  hosts: [],
  consent: "not-required",
  cookies: [],
  justification: "No analytics are wired up. This is the template's default.",
};

/**
 * A worked example of path 1, for a product that wants numbers without a
 * banner. Not active -- it is exported so the tests can drive the cookieless
 * branch, and so a product owner can see the shape rather than infer it.
 *
 * The host is a placeholder. Swap it for a real cookieless provider's, keep
 * `consent: "not-required"` only if that provider genuinely stores nothing on
 * the device, and write the justification yourself.
 */
export const COOKIELESS_EXAMPLE: AnalyticsProvider = {
  id: "cookieless-example",
  scriptSrc: "https://analytics.example.com/script.js",
  scriptAttributes: { "data-site": "REPLACE_ME", defer: "" },
  hosts: ["analytics.example.com"],
  consent: "not-required",
  cookies: [],
  justification:
    "Page-view counts with no cookie, no cross-site identifier and no personal data, " +
    "which is what makes it exempt from consent rather than merely tasteful.",
};

/**
 * A worked example of path 2, likewise inactive and likewise exported for the
 * tests -- the cookie path has more behaviour to prove than the other two put
 * together, and proving it needs a provider to point at.
 */
export const COOKIE_EXAMPLE: AnalyticsProvider = {
  id: "cookie-example",
  scriptSrc: "https://tags.example.net/loader.js",
  scriptAttributes: { "data-id": "REPLACE_ME", async: "" },
  // Two hosts, and that is the usual shape: the tag loads from one and
  // beacons to another.
  hosts: ["tags.example.net", "collect.example.net"],
  consent: "required",
  cookies: ["_ex_id", "_ex_ses*"],
  justification:
    "Placeholder for a cookie-setting provider. A real entry here needs an " +
    "ADR-0004 justification naming what it collects, who it is shared with, and " +
    "why the numbers are worth a consent banner.",
};

/**
 * The active configuration. **A product edits this line and nothing else.**
 */
export const analytics: AnalyticsProvider = NONE;

/** Is any provider wired up at all? */
export function isConfigured(provider: AnalyticsProvider = analytics): boolean {
  return provider.id !== NONE.id;
}

/**
 * Does this build need a consent banner?
 *
 * Two conditions, and both have to hold. A provider that needs consent is the
 * obvious one. The second is the module toggle: consent is *recorded* in the
 * `gdpr` module's `consent_record` table, so a product that turned that
 * module off has no endpoint to record it with -- and a banner that collects
 * an answer it cannot store is worse than no banner, because it looks like
 * compliance. T-308 gates this task on that module, not on `marketing` alone.
 */
export function needsBanner(
  provider: AnalyticsProvider,
  gdprEnabled: boolean,
): boolean {
  return isConfigured(provider) && provider.consent === "required" && gdprEnabled;
}

/**
 * Every host the built site is allowed to contact, for one provider.
 *
 * The single source `check-budget.mjs` and `caddy-site.mjs` both read, so the
 * allowlist and the CSP can never disagree about who this site talks to.
 */
export function allowedHosts(
  provider: AnalyticsProvider = analytics,
): readonly string[] {
  return provider.hosts;
}
