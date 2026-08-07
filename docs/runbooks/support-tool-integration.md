# Support tool integration runbook (T-262)

How to wire a real support tool — Intercom, Crisp, Plain, Help Scout, or
something else — onto this template's tool-agnostic support layer, and how to
verify it before it goes in front of users.

The template deliberately ships **no vendor SDK**. It provides three things
and stops: the user's context (`GET /api/v1/me/support-context` and
`useSupportContext()`), the product's choice of tool
(`SUPPORT_TOOL`/`SUPPORT_URL` in `backend/product/config.py`, served on
`GET /api/v1/config`), and a "Contact support" entry on web and mobile that
opens whatever you wire up. Everything vendor-specific is product-owned code
that lives in your fork — see `TEMPLATE.md` for the product/template split.

## Trigger

Reach for this when:

- a product forked from this template is adding a support tool for the first
  time, or switching from one to another;
- "Contact support" opens the wrong thing (a mail client where a widget was
  expected, or the reverse);
- a support agent reports that conversations arrive without account details,
  or — worse — that a conversation is attributed to a user who never wrote it
  (that last one is the impersonation case: see step 4, it is a bug in the
  wrapper, not in the vendor).

Not this runbook: changing the *address* support mail goes to (that is
`SUPPORT_EMAIL` in `backend/product/config.py`, no code involved), and
anything about the impersonation session itself
(`docs/runbooks/mobile-impersonation.md`).

## Pre-checks

- An account with the vendor, and whichever key their browser snippet needs.
  Note whether it is a *public* key (Intercom's `app_id`, Crisp's website id)
  or a secret — a secret must not reach the browser bundle.
- Decide the fallback before wiring the widget: `SUPPORT_URL` is what users
  get when the widget is blocked (ad blockers routinely block support
  widgets), when they are on mobile, and before your wrapper mounts. Leave it
  unset to fall back to `mailto:SUPPORT_EMAIL`.
- `pnpm --filter @forge/api-client build` is current — the generated client
  must already contain `getSupportContextApiV1MeSupportContextGet`. Regenerate
  with `make gen-client` if it doesn't.
- Know your vendor's identity API and whether it takes a *verification hash*
  (Intercom's `user_hash`, computed server-side from a secret). If it does,
  that hash needs a product-side endpoint; `/me/support-context` deliberately
  does not mint one, because the template has no vendor secret.

## Steps

### 1. Declare the tool

In `backend/product/config.py`:

```python
SUPPORT_TOOL: SupportTool = "intercom"   # or crisp | plain | helpscout | custom | none
SUPPORT_URL: str | None = "https://help.example.com"   # None -> mailto:SUPPORT_EMAIL
```

**Expected:** `curl http://localhost:8000/api/v1/config | jq '.data.support_tool, .data.support_url'`
prints `"intercom"` and the URL. A typo fails at boot instead
(`SUPPORT_TOOL 'intercomm' is not a supported value.`) — that is
`app/core/support.py`'s `validate_support`, and it is the intended behaviour.

With `SUPPORT_TOOL = "none"` you are done: "Contact support" is a plain link
to `SUPPORT_URL`, with the user's account details appended when it is a
`mailto:`. The remaining steps only apply to a widget tool.

### 2. Write the product-side widget wrapper (web)

Create `apps/web/src/features/support/SupportWidget.tsx` **in your fork** —
the template does not ship this file, and will not overwrite it:

```tsx
import { useEffect } from "react";
import { useSupportContext } from "@forge/hooks";
import { SUPPORT_OPEN_EVENT } from "./SupportLink";

declare global {
  interface Window {
    Intercom?: (command: string, payload?: unknown) => void;
  }
}

export function SupportWidget() {
  const ctx = useSupportContext();

  useEffect(() => {
    // Null while auth is loading, and during an impersonation session the
    // wrapper must NOT identify the user — see step 4.
    if (!ctx || ctx.isImpersonating) return;
    window.Intercom?.("boot", {
      app_id: import.meta.env.VITE_INTERCOM_APP_ID,
      user_id: ctx.userId,
      email: ctx.email,
      name: ctx.fullName ?? undefined,
      created_at: Math.floor(new Date(ctx.signupAt).getTime() / 1000),
      company: { id: ctx.orgId, name: ctx.orgName, plan: ctx.plan },
    });
    return () => {
      window.Intercom?.("shutdown");
    };
  }, [ctx]);

  useEffect(() => {
    const open = (event: Event) => {
      window.Intercom?.("show");
      event.preventDefault(); // "handled" — suppresses the SUPPORT_URL fallback
    };
    window.addEventListener(SUPPORT_OPEN_EVENT, open);
    return () => {
      window.removeEventListener(SUPPORT_OPEN_EVENT, open);
    };
  }, []);

  return null;
}
```

Mount it once, inside the authenticated shell (`AppLayout`).

**Expected:** the widget's launcher appears for a signed-in user, and the
vendor's dashboard shows the conversation attached to the right account with
the org and plan on it.

The two halves are independent on purpose. `boot`/`identify` is what the
vendor needs; the `forge:support-open` listener is what makes the sidebar's
"Contact support" open *your* widget. Skip the listener and the entry still
works — it just navigates to `SUPPORT_URL` instead.

**The other vendors, same two halves:**

| Tool | Identify | Open |
|---|---|---|
| Crisp | `$crisp.push(["set", "user:email", [ctx.email]])`, `["set", "session:data", [[["org", ctx.orgName], ["plan", ctx.plan]]]]` | `$crisp.push(["do", "chat:open"])` |
| Plain | `Plain.init({ customerDetails: { email: ctx.email, fullName: ctx.fullName, ... } })` | `Plain.open()` |
| Help Scout | `Beacon("identify", { email: ctx.email, name: ctx.fullName, company: ctx.orgName })` | `Beacon("open")` |

### 3. Wire mobile

A phone can't run the browser widget. By default `openSupport()`
(`apps/mobile/src/lib/support.ts`) opens `SUPPORT_URL` — a `mailto:` arrives
prefilled with the same context block the web link uses. If your vendor has a
native SDK, register a handler once at app start (e.g. in
`apps/mobile/app/_layout.tsx`):

```ts
import { setSupportHandler } from "../src/lib/support";

setSupportHandler(async (ctx) => {
  if (!ctx || ctx.isImpersonating) return false; // fall through to the URL
  await Intercom.loginUserWithUserAttributes({ email: ctx.email });
  await Intercom.presentMessenger();
  return true; // handled — suppresses the URL fallback
});
```

**Expected:** tapping "Contact support" in mobile Settings opens the SDK.
Returning `false` (or throwing) falls back to the URL, which is what you want
when the SDK fails to initialise.

### 4. Check the impersonation case before shipping

`isImpersonating` is `true` whenever a superadmin is acting as the user
(T-142). The wrapper **must not** call the vendor's identify/boot API in that
case. Otherwise the operator's clicking around is recorded in the real user's
conversation history, and the vendor may reply to a user who never asked
anything — a support-tool-shaped data leak, from the user's point of view.

**Expected:** impersonate a user (admin console → Impersonate), open the app,
and confirm the widget either does not boot or boots anonymous, and that the
vendor's dashboard records nothing against that user.

## Verification

1. **The context endpoint answers with the caller's own account:**

   ```bash
   curl -H "Authorization: Bearer $TOKEN" -H "X-Org-ID: $ORG" \
     http://localhost:8000/api/v1/me/support-context | jq .data
   ```

   **Expected:** `email`, `org_name`, `plan`, `subscription_status`,
   `signup_at`, `locale`, `is_impersonating: false`.

2. **The negative check — cross-tenant:** repeat the call with an `X-Org-ID`
   for an org the user does not belong to. **Expected:** `403`, with no trace
   of that org's name in the body. This is the check that would still look
   fine if the endpoint were quietly returning whatever org it was asked for.

3. **The fallback still works with the widget dead:** block the vendor's
   script (uBlock, or DevTools → Network → block request domain) and click
   "Contact support". **Expected:** the mail client (or help centre) opens.
   A support button that does nothing when a widget is blocked is the single
   most common way this integration fails in production.

4. **The prefilled mail carries the account:** with `SUPPORT_TOOL = "none"`,
   the composed mail body ends with `User:` / `Organization:` / `Plan:` /
   `Locale:` lines. Those are deliberately untranslated — a support agent
   reads them, not the user.

## Rollback

Set `SUPPORT_TOOL = "none"` and redeploy: "Contact support" reverts to the
plain `SUPPORT_URL` link on both surfaces, and the wrapper's
`forge:support-open` listener is simply never reached. No data migration, no
state to unwind — the template stores nothing about the support tool.

Unmounting the wrapper (or removing its `<script>`) is the faster stopgap if
you cannot redeploy the backend: the fallback link is what remains, and it
works.

Point of no return: none on our side. What you cannot undo is data already
sent to the vendor — a widget booted with real identities has already copied
those users into a third-party system, and removing the widget does not
retract them. That is a DPA/GDPR question (`docs/security/`), not a code one,
so decide it before step 2, not after.

## Escalation

- **Widget never appears, no console error:** almost always the vendor key
  (wrong environment's `app_id`) or a Content-Security-Policy rejection.
  Check the browser console for a CSP violation and `Caddyfile`'s headers
  before suspecting this template.
- **Conversations arrive anonymous:** the wrapper booted before
  `useSupportContext()` resolved. It returns `null` until auth *and* the
  context request have both landed — re-run `boot` in an effect keyed on the
  context, as in step 2, rather than once on mount.
- **A conversation is attributed to the wrong user:** stop and check step 4
  first. If impersonation is not involved, the vendor is caching an identity
  across logouts — call their `shutdown`/`logout` API in the effect cleanup.
- **`/me/support-context` returns 400:** the request carried no `X-Org-ID`.
  On web that header comes from `@forge/api-client`'s `orgIdRequestInterceptor`
  (set by `useOrg`); on mobile from `ensureCurrentOrg()`.
- Capture before retrying: the failing request's `request_id` (every envelope
  carries one in `meta`), the vendor's own error id, and whether the session
  was impersonated. Without those three the next attempt is blind.
