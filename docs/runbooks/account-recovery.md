# Admin-assisted lost-2FA account recovery (T-218)

What to do when a user has lost their TOTP device *and* every backup code,
and can no longer complete login. Closes [ADR-0001](../adr/0001-security-baseline.md)
row V2.5.7 ("Lost-MFA recovery re-verifies identity at enrollment level").

## When this applies

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

## Identity verification (do this before touching the account)

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

## The action

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

## After recovery

Tell the user to re-enable 2FA (`POST /auth/2fa/enable` +
`POST /auth/2fa/verify`, or register a WebAuthn key) as soon as they're back
in — an account that just went through a lost-2FA recovery is, until then,
back to password-only.

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
