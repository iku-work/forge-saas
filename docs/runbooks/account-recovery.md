# Admin-assisted lost-2FA account recovery (T-218)

What to do when a user has lost their TOTP device *and* every backup code,
and can no longer complete login. Closes [ADR-0001](../adr/0001-security-baseline.md)
row V2.5.7 ("Lost-MFA recovery re-verifies identity at enrollment level").

## Trigger

- The user has `totp_enabled` and no registered WebAuthn/security-key
  credential — if they have a security key, they don't need this: WebAuthn is
  an alternative second factor at the same login gate (T-217), not an
  additional one, so they can just sign in with the key instead.
- Self-service `disable_2fa` (`POST /auth/2fa/disable`) is unreachable because
  it requires both the current password *and* a valid TOTP/backup code — the
  exact two things a genuinely locked-out user doesn't have.

If the user still has backup codes or a working authenticator, direct them to
self-service `disable_2fa` instead — this runbook is a last resort, not the
default path.

## Pre-checks

**Identity verification — all of this happens before a superadmin touches the
account, and none of it is enforced by code.**

This step happens **outside the product** — there is no automated identity
check for "is the person emailing support actually the account owner."
Support must independently verify identity before a superadmin takes any
action, at minimum:

1. Confirm the request comes from the email address on the account, or from
   a channel already tied to a verified identity for that account (e.g. an
   org owner vouching for a teammate, if your support process allows that).
2. For anything beyond a low-value individual account (an org owner, a
   billing contact, an account with elevated org roles), get a second
   support engineer to independently confirm before proceeding — same spirit
   as [T-140](../../agents.md)'s "two-person" posture for high-impact admin
   actions, even though this specific endpoint doesn't technically enforce
   it (see "Limitations" below).
3. Record how identity was verified (channel, what was checked) somewhere
   support already tracks case notes — the audit event this action writes
   (see below) records *that* a superadmin acted, not *how* they confirmed
   the requester was who they claimed to be, so this step has to be captured
   separately or that context is lost.

## Steps

A superadmin calls:

```
POST /api/v1/admin/users/{user_id}/disable-totp
{ "reason": "<free text — ticket link or a real justification, min 10 chars>" }
```

(`backend/app/modules/admin/router.py`'s `disable_user_totp`, backed by
`AdminAccountRecoveryService.disable_totp_for_user` in
`backend/app/modules/admin/service.py`.) This:

- Clears the user's `TOTPSecret` row and flips `User.totp_enabled` to
  `False` — after this, the user can log in with just their password (or
  finish a password reset first, if they've lost that too).
- Writes an `AuditEvent` (`action="admin.user.disable_totp"`,
  `severity="elevated"`) carrying the acting superadmin's id, the `reason`
  text, and a timestamp — visible in the admin audit feed
  (`GET /api/v1/admin/audit`) and the dedicated `admin-audit.log` sink
  (T-140).
- Sends the user a `security_notification` email
  (`totp_disabled_by_admin` event) to their address on file, telling them an
  administrator disabled 2FA on their account and to contact support
  immediately if they didn't request it. This is the primary safety net
  against a social-engineered false recovery request — if the real owner's
  inbox is still theirs, they find out right away.

The endpoint itself only requires `require_superadmin` — which, as of
T-218, itself requires the acting superadmin to have 2FA enrolled — plus a
non-empty `reason`. It does **not** additionally require `require_recent_auth`
step-up the way email-change/billing/member-removal do, since impersonation
(a comparably sensitive admin action) doesn't either; support workflows are
assumed to already sit behind their own session-length policy.

## Verification

Three things, and the third is the one that matters:

1. The endpoint returned `200` with an empty-data envelope. It returns nothing
   about the user (`Envelope[None]`), and `GET /api/v1/admin/users/{id}`
   doesn't expose `totp_enabled` either — so there is no admin-side read that
   confirms the flag flipped. The audit event below is the confirmation.
2. The `admin.user.disable_totp` event is in the admin audit feed
   (`GET /api/v1/admin/audit`) with the reason text you supplied. A missing
   event means the action didn't happen, whatever the response said.
3. **The user received the `security_notification` email.** This is the safety
   net against a social-engineered recovery, so a delivery failure is not a
   cosmetic problem — if the send failed (check the worker logs / Resend
   dashboard), the recovery is un-notified and should be treated as the
   escalation case below until the real owner has been reached some other way.

## After recovery

Tell the user to re-enable 2FA (`POST /auth/2fa/enable` +
`POST /auth/2fa/verify`, or register a WebAuthn key) as soon as they're back
in — an account that just went through a lost-2FA recovery is, until then,
back to password-only.

## Rollback

**There isn't one, and the point of no return is the request itself.** The
`TOTPSecret` row is deleted, not disabled — the shared secret is gone, so
"turn 2FA back on" is not an operation anyone can perform on the user's
behalf. Only the user can re-enrol, from a device they still have.

What that means in practice: the reversible part of this procedure is
everything *before* the call, which is why the identity verification is in
Pre-checks and not a footnote. Once the endpoint returns, the account is
password-only until the user acts.

If the recovery turns out to have been fraudulent, the response is
containment, not rollback — see below.

## Escalation

- **The real owner says they didn't request this** (or the notification email
  bounces and support can't reach them): treat it as an account compromise.
  Get a password reset onto the account and have the user complete it —
  `confirm_password_reset` revokes every session, which is the only lever that
  ends an attacker's access. Note that there is **no admin endpoint that
  revokes another user's sessions**; if the user cannot complete a reset,
  session revocation is a direct database action, and that is the point to
  involve whoever owns production data access.
- **The account is an org owner, a billing contact, or holds elevated org
  roles.** Don't run this on the strength of one support conversation — the
  second-engineer confirmation in Pre-checks is a process control with nothing
  enforcing it, so it is only real if someone insists on it.
- **The user has only a WebAuthn credential and no TOTP.** This endpoint does
  not help and there is no `disable-webauthn` equivalent; see Limitations.
  File a task rather than improvising a database edit.
- **Repeat recovery requests for the same account.** Two lost-2FA recoveries
  in a short window is a pattern, not a coincidence. Review the audit feed for
  that user before acting a second time.

## Limitations / what this runbook does *not* give you

- **No two-person approval enforced in-product.** The identity-verification
  step above is a process control, not a code control — nothing stops one
  superadmin from unilaterally disabling a user's 2FA. If your organization
  needs this technically enforced (e.g. a second superadmin's sign-off before
  the endpoint will act), that's unbuilt — track it as a follow-up rather
  than assuming this doc's "get a second engineer to confirm" step is
  actually gated anywhere.
- **No account-wide lockout/freeze during recovery.** The account remains
  usable (existing sessions keep working) throughout — this action only
  touches the 2FA requirement, it doesn't revoke sessions or otherwise pause
  the account. If the recovery request itself is the attack (an attacker
  impersonating the real user to support), an already-logged-in real owner
  wouldn't be locked out while support investigates.
- **Doesn't cover WebAuthn-only lockout.** If a user has *only* a WebAuthn
  credential (no TOTP) and loses their security key with no backup, this
  endpoint doesn't help — there's no equivalent `disable-webauthn` admin
  action yet. File a follow-up task if this becomes a real support case.
