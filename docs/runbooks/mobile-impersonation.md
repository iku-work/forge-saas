# Mobile impersonation handoff (T-261)

How a superadmin reproduces a mobile-only problem on their *own* phone, as the
affected user, without ever touching that user's device. The authorization
posture behind it — who may impersonate, for how long, and what impersonation
may not do — is [T-142](../../agents.md) and
[T-260](../../agents.md); this document is the operating procedure for the
mobile leg only. The security rationale for the token design lives in
`backend/app/modules/admin/impersonation_handoff.py`'s module docstring.

## Trigger

Reach for this when **all** of these hold:

- A user reports a defect you cannot reproduce from the web app, and the
  behaviour is plausibly device-specific: a native picker, push delivery,
  camera/file upload, a layout that only breaks at a phone width, an
  iOS/Android-only crash.
- You have already tried, and failed, to reproduce it on your own account on a
  phone. Impersonating is for state you cannot construct yourself — their org,
  their plan, their files — not for "I don't have the app installed".
- The impersonation itself is justified under the ordinary T-142 rules: there
  is a support ticket, and you can write a reason someone auditing this later
  would accept.

**Not this runbook:**

| Situation | Use instead |
|---|---|
| Reproducible in a desktop browser | Regular impersonation ("Impersonate" on the admin user page) |
| You need to *see what the user sees on their device* | A screen-share (Zoom/Meet/Teams) with the user driving — you see their real device, real network, real OS version, and they can stop at any moment |
| The user must perform an action (change a password, confirm a payment) | Screen-share and talk them through it. Impersonation blocks every destructive action by design (T-260) — a support agent must not do these *as* them |
| A lost/stolen device, or the user cannot log in at all | [account-recovery.md](account-recovery.md) |

Screen-share is the lower-privilege option and should be the default when it
works. Handoff exists for the case where it doesn't: an intermittent bug, a
long reproduction, a user in another timezone, or a defect that needs you to
poke at the app rather than watch someone else poke at it.

## Pre-checks

1. **You are a superadmin with 2FA enrolled.** `require_superadmin` refuses
   otherwise (T-218) — a superadmin without TOTP gets a 403 from the
   impersonate endpoint, not from this flow.
2. **The mobile app is installed on your phone and is signed out, or signed in
   as you.** The handoff replaces whatever session is on the device: the app
   holds one session per install. If you are logged in as yourself and mid-way
   through something, finish it first.
3. **The app build registers the `forge://` scheme** (`expo.scheme` in
   `apps/mobile/app.json`, and `MOBILE_DEEP_LINK_SCHEME` on the backend must
   match it). A TestFlight/internal build is fine; Expo Go is not — it
   registers its own scheme and will not receive `forge://` links.
4. **You have a ticket reference and a reason.** The reason is stored
   server-side for the two-minute life of the link and shown on the phone's
   confirmation screen; it is also in the audit event forever.
5. **Push notifications:** nothing to prepare. Your device is never registered
   against the impersonated user (`apps/mobile/app/(app)/_layout.tsx` skips
   registration while impersonating), and notifications *raised by* your
   activity are suppressed rather than delivered (T-260). Their real devices
   keep receiving their own notifications throughout.

## Steps

### 1. Open the user in the admin console

Admin → Users → search for the account → open it.

**Expected:** the user detail page, with two buttons at the top right:
"Impersonate" and "Hand off to my phone".

### 2. Create the handoff link

Click **Hand off to my phone**. Fill in the reason (10 characters minimum —
write the ticket number and the symptom, e.g. `Ticket 8812: upload sheet
crashes on iOS 18`) and the duration in minutes (default 30, hard maximum 60).
Click **Create handoff link**.

**Expected:** a QR code, the deep link in plain text underneath it
(`forge://impersonate?token=…&user_email=…`), and a countdown reading
`Link expires in 120s` and falling.

Two things are worth knowing at this step:

- **This starts the impersonation session immediately**, even though your
  browser stays signed in as you. The clock on the *session* (your 30 minutes)
  starts now, not when you scan. If you change your mind, use **Discard
  session** rather than closing the dialog — that is what actually revokes it,
  and once the dialog is closed nothing can (see Rollback).
- **The link is a credential.** Anyone who can read your screen for the next
  two minutes can take this session. Don't create one on a shared screen, and
  don't paste it into a ticket.

### 3. Open it on your phone

Scan the QR code with your phone's camera, or copy the text link and open it on
the device (AirDrop, a DM to yourself, `xcrun simctl openurl booted "<link>"`
on the iOS Simulator, `adb shell am start -a android.intent.action.VIEW -d
"<link>"` on an Android emulator).

**Expected:** the app opens on a "Confirm impersonation" screen showing the
user's email, the reason you typed, and how long the session has left.

If it shows *"This handoff link is invalid or has expired"*, the two minutes
ran out or the link was already used — go back to step 2 and make a new one.
Nothing is wrong.

### 4. Confirm on the phone

Read the email on screen and check it is the account you meant. Tap **Confirm**.

**Expected:** the app lands on the dashboard as the user, with a red banner
across the top of every screen reading
`Impersonating <email> · MM:SS left · End`. The banner cannot be dismissed.

Tapping **Cancel** instead invalidates the link immediately and returns to the
sign-in screen. The impersonation *session* behind it is a separate thing and
stays alive, unused: end it with **Discard session** in the browser dialog that
is still open, or it runs to its expiry.

### 5. Reproduce the problem

Use the app normally. Two limits apply and both are deliberate:

- **Destructive actions are refused** with "Action is blocked during
  impersonation" (T-260) — anything that sends mail, produces a charge, changes
  credentials or identity, or marks their notifications read. If the
  reproduction needs one of these, stop: that is a screen-share.
- **Nothing you do notifies them.** Push is suppressed for the duration.

**Expected:** every ordinary read works exactly as it does for the user.
Everything you do is in the audit feed, stamped with your id as
`impersonator_id`.

### 6. End the session

Tap **End** in the red banner.

**Expected:** the banner disappears and the app returns to the sign-in screen —
*not* to your own account. Mobile holds one session per install, so there is
nothing underneath the session you just ended; sign in again as yourself if you
want the app back.

If you close the app instead of tapping End, the session is still live
server-side until its expiry. Ending it properly is what revokes the token.

## Verification

From the admin console (as yourself), open the user's detail page or the global
audit feed and confirm the trail:

| Event | Written when |
|---|---|
| `admin.impersonate.start` | Step 2, with your reason and duration |
| `impersonation.mobile_handoff_created` | Step 2, when the link was minted |
| `impersonation.mobile_handoff` | Step 4, when the phone redeemed it |
| `impersonation.mobile_handoff_cancelled` | Step 4, if you tapped Cancel instead |
| `impersonation.mobile_ended` | Step 6 |

All of them are `severity="elevated"` and carry your id in `impersonator_id`.

The negative check — the one that would still look fine if the session had
silently stayed open: with the app in that state, call
`GET /api/v1/auth/me` with the token the phone held. After a proper End it must
answer **401** (the jti is blocklisted). If you have no easy way to do that, the
equivalent from the device is: force-quit and reopen the app. It must come back
to the **sign-in screen**, never to the impersonated account — the app clears
any impersonation state on launch.

## Rollback

There is nothing to undo, and one point of no return:

- **A link you created and then thought better of** — click **Discard session**
  in the handoff dialog. That is not just closing the dialog: it revokes the
  impersonation session behind the link, which matters because *creating the
  link started the session* (step 2). **New link** does the same thing on the
  way to minting a replacement, so clicking it repeatedly does not strand a
  live session per click.
- **A link you already closed the dialog on, unscanned** — this is the one case
  with no clean undo. The dialog held the only handle on that session, and
  closing it dropped that handle; the link itself dies in two minutes, but the
  session keeps running for the duration you chose. It cannot be revoked from
  the console. Options: scan it on your phone and tap End, or wait it out.
  **This is the reason to pick a short duration** — the default 30 minutes is a
  worst case you are choosing every time you click Create.
- **A session you want to stop early, once someone holds it** — tap End on the
  phone, or click "End impersonation" on web if the browser also adopted one.
  Either revokes the same jti, which stops it on every device at once.
- **Point of no return: step 5.** Reads performed as the user are performed;
  they are in the audit log and cannot be un-performed. This is the reason the
  reason field is mandatory. No write action can be taken at all (T-260), so
  there is no data change to roll back — the only lasting effect of a handoff
  is the record that it happened.
- **A lost or compromised phone mid-session** — end the session from that phone
  if you still can; otherwise, if the browser also adopted the session, "End
  impersonation" there revokes the same jti. If neither is available, the
  session cannot be revoked and expires on its own — escalate on the
  superadmin account itself (rotate its credentials, review the audit feed for
  what the session did) rather than waiting quietly for the clock.

## Escalation

Stop and escalate rather than retrying when:

- **The phone shows "invalid or expired" twice in a row on a fresh link.** The
  device clock, the deep-link scheme, or Redis is the suspect, in that order.
  Check `MOBILE_DEEP_LINK_SCHEME` matches the installed build's
  `expo.scheme`; check the backend can reach Redis (`/health/ready`), since the
  handoff record lives only there.
- **The link opens the browser or "no app can handle this" instead of the
  app.** The build does not register the scheme — that is a mobile release
  problem, not a support one. Use a screen-share for the ticket in front of you
  and file the build issue separately.
- **The banner is missing while impersonating.** Stop using the session and end
  it. A session without its banner is the one failure mode this feature must
  not have; capture the build number and file it as a security defect.
- **You need an action the blocklist refuses.** Do not look for a way around
  it. Either the user performs it themselves on a screen-share, or it is a
  deliberate admin action with its own endpoint and its own audit trail (plan
  overrides, TOTP reset — see [account-recovery.md](account-recovery.md)).

Capture before you retry: the audit events already written for the attempt, the
exact deep link (redact the `token` query parameter), the app build number, and
the OS version. A second attempt with no notes is a second attempt with nothing
learned.
