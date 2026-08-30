/**
 * What a visitor decided about analytics, and where that decision lives
 * (T-308).
 *
 * Two stores, because there are two kinds of visitor:
 *
 * - **Anonymous.** A first-party cookie on this site. It has to be a cookie
 *   rather than `localStorage` because the app on the other host has to be
 *   able to see it at all -- see `COOKIE_DOMAIN` below -- and because a
 *   consent decision that vanished in a private window would re-prompt
 *   forever.
 * - **Signed in.** The `gdpr` module's `consent_record` table, through
 *   `POST /api/v1/me/consents`. That table is append-only by design (T-032):
 *   granting and revoking are two rows, never an edit, which is what makes
 *   "what had they agreed to on the 3rd" answerable.
 *
 * **They reconcile at login**, and the direction is deliberate: the cookie is
 * what a person clicked most recently on a page they were reading, so on the
 * visit where a session appears, `apps/web` reads it and posts it. From then
 * on the record is authoritative for that user, and the cookie is a cache of
 * it. `apps/web/src/lib/consent.ts` is the other half of this file and is
 * kept deliberately small for that reason.
 *
 * Everything here runs in the browser except `consentScriptSource()`, which
 * renders it into a page at build time. There is no framework: the banner is
 * static markup and this is the handful of lines that make its two buttons
 * work, which is the whole reason it can be pre-rendered.
 */
import { analytics, type AnalyticsProvider } from "../config/analytics";

/**
 * The cookie's name.
 *
 * No `__Host-` prefix, and that is the one place this file departs from the
 * session cookie's rules (T-307): the prefix forbids a `Domain` attribute,
 * and this cookie's whole job is to be readable on the app's host as well as
 * this one. It carries nothing secret -- the string "granted" or "denied" --
 * so the prefix would be buying nothing in exchange for breaking the feature.
 */
export const COOKIE_NAME = "forge_consent";

/**
 * How long a decision stands before the visitor is asked again.
 *
 * Six months, not a year: the CNIL and most other supervisory authorities
 * treat "no more than 13 months" as the ceiling for a consent record, and a
 * shorter interval means a stale "yes" from a policy two versions ago expires
 * on its own. Withdrawal is instant and does not wait for this.
 */
export const COOKIE_MAX_AGE_DAYS = 182;

/**
 * The registrable domain to scope the cookie to, or `null` for host-only.
 *
 * **This is the one line a product changes to make consent follow a visitor
 * into the app.** T-307 puts marketing at the apex and the app on `app.`, so
 * a cookie scoped to `.example.com` is visible to both and the reconciliation
 * at login works. Left `null`, the cookie is host-only: consent still works
 * on this site, and a visitor who signs in is simply asked again inside the
 * app -- which is correct but repetitive, and is the safe direction for a
 * template that does not know the product's domain.
 *
 * Read from the environment rather than hardcoded, because the value is a
 * deployment fact (it differs between staging and production) rather than a
 * product decision.
 */
export const COOKIE_DOMAIN: string | null =
  import.meta.env.PUBLIC_CONSENT_COOKIE_DOMAIN ?? null;

/** The two answers, as stored. */
export type ConsentDecision = "granted" | "denied";

/**
 * What `consent_record` calls this. The gdpr module's `consent_type` is a
 * free string; "analytics" is what T-032's docstring already names, and using
 * a different word here would split one person's history across two values.
 */
export const CONSENT_TYPE = "analytics";

/**
 * The version of the policy a decision was made against.
 *
 * Bumping this re-asks everyone: a stored decision whose version is not this
 * one is treated as absent. That is the mechanism behind "we changed what we
 * collect, so previous consent no longer covers it", and it is one string
 * rather than a migration.
 */
export const POLICY_VERSION = "2026-08-30";

/** A stored decision, once parsed. */
export interface StoredConsent {
  decision: ConsentDecision;
  version: string;
}

/**
 * `granted|2026-08-30` -- two fields, one separator, no JSON.
 *
 * A cookie is a header on every request to this origin, and a JSON blob would
 * be percent-encoded into three times the bytes for two values. It is also
 * read by hand more often than it is read by code, in a browser's storage
 * panel, during exactly the kind of conversation this feature exists for.
 */
export function serialize(consent: StoredConsent): string {
  return `${consent.decision}|${consent.version}`;
}

export function parse(raw: string | null | undefined): StoredConsent | null {
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
 * The decision that currently applies, or `null` if the visitor must be
 * asked.
 *
 * A decision recorded against a different policy version is `null` on
 * purpose -- see POLICY_VERSION.
 */
export function current(raw: string | null | undefined): ConsentDecision | null {
  const stored = parse(raw);
  return stored && stored.version === POLICY_VERSION ? stored.decision : null;
}

/**
 * The cookie attributes a decision is written with, as a string.
 *
 * `SameSite=Lax` rather than `Strict`: a visitor arriving from a search
 * result or a campaign link is a cross-site navigation, and `Strict` would
 * withhold the cookie on exactly that request -- re-prompting the one person
 * who already answered. `Secure` unconditionally; this site is HTTPS on every
 * host it is served from (T-307), including `.localhost`, which Caddy issues
 * an internal certificate for.
 */
export function cookieAttributes(maxAgeSeconds: number): string {
  const parts = [
    "Path=/",
    `Max-Age=${maxAgeSeconds}`,
    "SameSite=Lax",
    "Secure",
  ];
  if (COOKIE_DOMAIN) {
    parts.push(`Domain=${COOKIE_DOMAIN}`);
  }
  return parts.join("; ");
}

/**
 * Cookie names to delete when consent is withdrawn.
 *
 * The provider's own, from its `cookies` declaration -- a `name*` entry is a
 * prefix, which is how most tag managers name a per-session cookie. Not the
 * consent cookie itself: "denied" is a decision worth remembering, and
 * deleting it would re-prompt the visitor who just said no.
 */
export function cookiesToClear(
  provider: AnalyticsProvider = analytics,
): readonly string[] {
  return provider.cookies;
}

/**
 * The script the banner ships, as text, generated at build time.
 *
 * Written as a string rather than as a `.ts` file Astro would bundle, for one
 * reason that decides it: T-307's CSP names every inline script by SHA-256
 * digest, computed from `dist/` by `scripts/caddy-site.mjs`. An inline block
 * gets a hash automatically; a bundled module would be a second request on
 * the critical path of a page whose whole point is that it costs nothing.
 * It is generated from this file's own constants so the two cannot disagree
 * about a cookie name.
 *
 * What it does, in order:
 *
 *  1. Reads the cookie. If a decision for the current policy version is
 *     already stored, it hides the banner and -- only if the answer was
 *     `granted` -- loads the provider. **Not "loads and waits": the script
 *     element is created after the decision, so a denial means the request is
 *     never made at all.**
 *  2. Otherwise it shows nothing new: the banner is already in the HTML, and
 *     it is the *hiding* that is conditional. A banner injected after paint
 *     is a guaranteed layout shift and would fail T-306's CLS budget.
 *  3. Wires the two buttons, and the footer's permanent "cookie settings"
 *     link, which re-opens the banner so withdrawing is the same two clicks
 *     as granting. Re-opening moves focus into the banner; the *initial*
 *     render deliberately does not, because a visitor who has just arrived is
 *     reading the page and having focus taken from them is worse than the
 *     banner being one Tab away. A person who asked for it, on the other
 *     hand, needs to be told it arrived -- otherwise a screen-reader user
 *     clicks "Cookie settings" and nothing at all is announced.
 *  4. On withdrawal, deletes the provider's cookies by name -- on this host
 *     and, if one is configured, on the shared domain, because a cookie set
 *     with a `Domain` is not removed by an expiry written without one.
 */
export function consentScriptSource(
  provider: AnalyticsProvider = analytics,
): string {
  const config = {
    cookieName: COOKIE_NAME,
    attributes: cookieAttributes(COOKIE_MAX_AGE_DAYS * 24 * 60 * 60),
    expiry: cookieAttributes(0),
    domain: COOKIE_DOMAIN,
    version: POLICY_VERSION,
    consentType: CONSENT_TYPE,
    scriptSrc: provider.scriptSrc,
    scriptAttributes: provider.scriptAttributes,
    clear: [...provider.cookies],
  };
  // JSON.stringify, then interpolated into a script: every value here comes
  // from committed configuration rather than from a request, and JSON is a
  // subset of JavaScript object syntax -- but `</script>` inside a string
  // would still end the block early, so the one sequence that can do that is
  // escaped. Same rule T-303's JsonLd.astro applies to its payloads.
  const json = JSON.stringify(config).replace(/</g, "\\u003c");
  return `(function(){var c=${json};
var banner=document.getElementById("consent-banner");
function read(){var m=document.cookie.match(new RegExp("(?:^|; )"+c.cookieName+"=([^;]*)"));return m?decodeURIComponent(m[1]):null;}
function decision(){var v=read();if(!v)return null;var p=v.split("|");return p[1]===c.version?p[0]:null;}
function write(value){document.cookie=c.cookieName+"="+encodeURIComponent(value+"|"+c.version)+"; "+c.attributes;}
function clear(){for(var i=0;i<c.clear.length;i++){var n=c.clear[i];var names=n.slice(-1)==="*"?matching(n.slice(0,-1)):[n];for(var j=0;j<names.length;j++){document.cookie=names[j]+"=; "+c.expiry;}}}
function matching(prefix){var out=[];var all=document.cookie.split("; ");for(var i=0;i<all.length;i++){var name=all[i].split("=")[0];if(name.indexOf(prefix)===0)out.push(name);}return out;}
function load(){if(!c.scriptSrc||document.getElementById("analytics-script"))return;var s=document.createElement("script");s.id="analytics-script";s.src=c.scriptSrc;for(var k in c.scriptAttributes){s.setAttribute(k,c.scriptAttributes[k]);}document.head.appendChild(s);}
function hide(){if(banner)banner.hidden=true;}
function show(focus){if(!banner)return;banner.hidden=false;if(focus){var first=document.getElementById("consent-decline");if(first)first.focus();}}
function answer(value){write(value);hide();if(value==="granted"){load();}else{clear();}}
var initial=decision();
if(initial){hide();if(initial==="granted")load();}
if(banner){
var grant=document.getElementById("consent-accept");if(grant)grant.addEventListener("click",function(){answer("granted");});
var deny=document.getElementById("consent-decline");if(deny)deny.addEventListener("click",function(){answer("denied");});
}
var manage=document.querySelectorAll('[data-consent-manage]');
for(var i=0;i<manage.length;i++){manage[i].addEventListener("click",function(e){e.preventDefault();show(true);});}
})();`;
}
