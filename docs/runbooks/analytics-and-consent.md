# Wiring analytics on the marketing site (T-308)

Choosing an analytics provider for `apps/marketing` and, if it needs one,
turning on the consent banner. The template ships with **no analytics at all**
— that is the default, and a fork that never opens this file sets zero
cookies and contacts zero third parties.

The policy behind it is [ADR-0004](../adr/0004-marketing-framework.md)'s
fifth decision (nothing third-party runs on this origin without a written
justification) and
[`docs/security/threat-model.md`](../security/threat-model.md). The code is
`apps/marketing/src/config/analytics.ts`, which is the only file most products
will edit.

## Trigger

Reach for this when someone asks for numbers about the marketing site — page
views, campaign attribution, conversion to sign-up — and there is currently no
provider configured.

**Not this runbook:** product analytics *inside* the app (`apps/web`), which
is a different origin, a different consent conversation, and not something the
template has wired at all. Also not this runbook: `docs/runbooks/log-shipping.md`,
if what is actually wanted is server-side request volume, which needs no
consent and no third party because the requests already reach your own edge.

## Pre-checks

- **Decide which of the two paths you are on before writing any code.** The
  question is not "does it collect personal data" but the narrower one the
  ePrivacy Directive asks: *does it read or write anything on the visitor's
  device?*

  | | Path 1 — cookieless | Path 2 — cookie-setting |
  |---|---|---|
  | Stores anything on the device | No | Yes |
  | Consent banner | None | Required |
  | Work to wire it | Two lines | Two lines, then read all of this |
  | What you get | Page views, referrers, rough geography | The same, plus cross-visit identity |

  A provider that derives a daily visitor hash server-side is path 1 even
  though it "identifies" repeat visits within a day. A provider that sets a
  cookie is path 2 however privacy-preserving its marketing page is.

  **Path 1 is worth real effort to reach.** A banner avoided is not just a
  banner: it is the layout shift, the consent record, the withdrawal flow and
  the legal review that do not happen.

- **The ADR-0004 justification, written down.** One sentence naming what
  the provider collects, who it is shared with, and why the numbers are worth
  it. It goes in the `justification` field, and
  `apps/marketing/tests/consent.test.ts` fails the build if a provider that
  loads a script has an empty one.

- **On path 2 only:** confirm `gdpr` is enabled in `product.config.ts`.
  Consent is recorded in that module's `consent_record` table, and the banner
  refuses to render without it — a banner that collects an answer it cannot
  store is worse than no banner, because it looks like compliance.

- **On path 2 only:** know the exact names of every cookie the provider sets.
  Withdrawal deletes them by name, and a withdrawal that leaves the cookies in
  place has not withdrawn anything.

## Steps

### 1. Describe the provider

Edit `apps/marketing/src/config/analytics.ts`. Two worked examples are already
in that file — `COOKIELESS_EXAMPLE` and `COOKIE_EXAMPLE` — and they are the
shape to copy, not to use.

```ts
const OUR_PROVIDER: AnalyticsProvider = {
  id: "acme-analytics",
  scriptSrc: "https://cdn.acme-analytics.com/script.js",
  scriptAttributes: { "data-site": "abc123", defer: "" },
  // Every host it contacts. Most providers load from one and beacon to
  // another; both belong here or the beacon is blocked in production and
  // works fine locally.
  hosts: ["cdn.acme-analytics.com", "events.acme-analytics.com"],
  consent: "not-required",   // or "required" — see the Pre-checks table
  cookies: [],               // exact names, or `prefix*`, for path 2
  justification: "Page-view counts only; no cookie, no cross-site identifier.",
};
```

Then change the one line that decides which one is live:

```ts
export const analytics: AnalyticsProvider = OUR_PROVIDER;
```

**Expected:** nothing yet — this is a source edit.

### 2. Build, and read what the build says

```bash
pnpm --filter marketing build
```

**Expected:** the build succeeds, and the last integration prints the hosts it
picked up:

```
[forge:caddy-site] wrote 2 Caddy site block(s), script-src pinned to 3 inline
script hash(es), plus 2 analytics host(s)
```

If instead it fails with a `check-budget` message naming your provider's host,
the host is in `dist/` but not in `hosts` above. That gate is the point: a
third-party request this repo has not been told about fails the build rather
than shipping.

### 3. Check what the edge will send

```bash
grep Content-Security-Policy apps/marketing/caddy/dev/marketing.caddy
```

**Expected:** your hosts appear in three directives — `script-src` (so the tag
loads), `connect-src` (so its beacon is not blocked with no visible error) and
`img-src` (so a tracking pixel still works where `sendBeacon` is
unavailable). You do not edit the CSP; it is generated from `hosts`.

### 4. Path 2 only: set the cookie domain

The consent cookie is written on the marketing host. T-307 puts marketing at
the apex and the app on `app.<domain>`, so for consent to follow a visitor
into the product it has to be scoped to the registrable domain:

```bash
# apps/marketing's build environment
PUBLIC_CONSENT_COOKIE_DOMAIN=.example.com
```

**Expected:** the cookie is written with `Domain=.example.com`, visible in the
browser's storage panel on both hosts.

Left unset, everything still works — consent given on the marketing site is
simply invisible to the app, and the visitor is asked again once inside. That
is correct, and repetitive. It is the safe default for a template that does
not know your domain.

### 5. Path 2 only: bump the policy version if you changed what you collect

`POLICY_VERSION` in `apps/marketing/src/lib/consent.ts` is a date string.
Changing it invalidates every stored decision and asks everybody again, which
is what "we now collect X, so your previous consent no longer covers it"
means in practice. One string, no migration.

**Expected:** after a rebuild, a browser that had already answered sees the
banner again.

## Verification

Path 1 and path 2 both:

```bash
pnpm --filter marketing test
```

**Expected:** green. `tests/consent.test.ts` reads the real build and asserts
what is and is not in it.

Path 2, in a browser, against the running stack (`make dev`):

1. Open `https://marketing.localhost/en/` in a private window with the network
   panel open. **Expected:** the banner is visible, and there is **no request
   to the provider's host**. Not a blocked request, not a queued one — none.
   This is the check that distinguishes real consent from "consent mode".
2. Click **Decline**. **Expected:** the banner disappears, still no request,
   and `forge_consent=denied|<version>` is in the cookie jar.
3. Reload. **Expected:** no banner, still no request.
4. Click **Cookie settings** in the footer, then **Accept**. **Expected:** the
   provider's script is requested now, and its cookies appear.
5. Click **Cookie settings** again, then **Decline**. **Expected:** the
   provider's cookies are *gone* from the jar, and `forge_consent` is now
   `denied`. This is the step most implementations skip.
6. Sign in to the app. **Expected:** `GET /api/v1/me/consents` returns a row
   with `consent_type: "analytics"` and the `granted` value you last chose.
   (Only if step 4 set `PUBLIC_CONSENT_COOKIE_DOMAIN`.)

The negative check — the thing that would still look fine if this had silently
done nothing:

```bash
grep -rl "document.cookie" apps/marketing/dist | head
```

**Expected on path 1:** no output at all. A cookieless provider that turns out
to set a cookie shows up here and nowhere else.

## Rollback

Set the one line back:

```ts
export const analytics: AnalyticsProvider = NONE;
```

and rebuild. The banner, the script tag, the CSP entries and the budget
allowlist all disappear together, because all four are derived from that
value.

**What rollback does not undo:** cookies already on visitors' devices, and
rows already in `consent_record`. The first expire on their own schedule (the
provider's, not yours) and can only be cleared from a page the visitor loads —
so if the reason for rolling back is that the provider should never have been
enabled, leave the banner up for one deploy with `consent: "required"` so
withdrawal can run, *then* set `NONE`. The second is append-only by design and
is not deleted: a record that someone consented on the 3rd is still true after
you stop using the provider, and T-036's erasure path is what removes it when
the person asks.

## Escalation

- **The build fails on a host you did put in `hosts`.** The value in `dist/`
  and the value in the config differ — usually a `www.` or a regional
  subdomain the provider substitutes at runtime. Load the script by hand and
  read the network panel; add every host it actually touches.
- **The tag loads but reports nothing, only in production.** Almost always
  `connect-src`: the beacon host is not in `hosts`. A CSP block is silent by
  design in the browser console's default filter. Check the `Content-Security-Policy`
  header on a real response, not the generated file.
- **A regulator or a customer asks what a specific person consented to.** That
  is `consent_record`, not this runbook: query by `user_id` and read the rows
  in `ts` order. Every grant and every withdrawal is its own row (T-032), so
  the history is the answer.
- **The provider wants a tag manager rather than a script.** Stop and go back
  to ADR-0004's fifth decision. A tag manager is a facility for loading
  arbitrary future
  third-party code without a deploy, which is the exact thing the
  justification requirement exists to prevent; it needs its own ADR, not an
  entry in this file.
