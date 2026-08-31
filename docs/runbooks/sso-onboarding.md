# Connecting an identity provider (T-331)

Getting one organization signed in through its own OIDC identity provider —
Okta, Entra ID, Auth0, Keycloak, anything that publishes a discovery document.
The design is in `backend/app/modules/sso/oidc.py` (which checks *what*, and
why each check is separate) and ADR-0006 (why OIDC and not SAML).

The module ships **off** and requires `oauth`. A product that has never turned
it on has no `sso_connection` table and nothing here applies.

## Trigger

- A customer asks to sign in with their own IdP.
- SSO logins started failing for one organization — go to
  [Escalation](#escalation), which is ordered by how
  often each cause actually occurs.
- An organization is locked out after enabling enforcement — the break-glass
  is [step 6](#6-turn-enforcement-on-when-they-ask-not-before), and it is the
  reason nobody needs a DBA for this.

**Not this runbook:** social sign-in with Google or GitHub (that is T-023's
`oauth` module, configured with two environment variables and no per-org
anything), or an API integration that needs no person at all
(`docs/runbooks/api-keys.md`).

## Pre-checks

- `enterprise_sso` is `True` in `backend/product/config.py` **and**
  `enterpriseSso: true` in `product.config.ts`. Both, always.
- `oauth` is on. `enterprise_sso` requires it, and `validate_modules()` fails
  the boot on the illegal combination rather than letting a login find half a
  stack.
- The migration is applied: `python -m app.core.migrations plan` shows
  `enterprise_sso -> enabled  -> applying`.
- You have the customer's **discovery URL**, **client ID** and **client
  secret**, and you know which **email domain** their IdP is authoritative
  for. All four come from their IdP admin; three of them you cannot guess.
- You hold `settings:manage` in the organization (owner or admin).

## Steps

### 1. Register this product as an application in their IdP

Their side, and the only part of this that is not in this repo. They create an
OIDC **web application** and give you the three values above. Tell them:

- **Redirect URI**: `https://<your app host>/api/v1/auth/sso/callback` —
  exactly, including the scheme. An IdP compares it literally and the error
  it gives for a mismatch is usually unhelpful.
- **Scopes**: `openid email profile`. Without `openid` the token endpoint
  returns an OAuth2 response with no ID token at all, which this product
  reports as `errors.sso_no_id_token` rather than as a crash.
- **Signing algorithm**: RS256 (or any of ES256/384/512, PS256, RS384/512).
  A symmetric algorithm is refused — see `oidc.ALLOWED_ALGORITHMS` for why.

### 2. Create the connection

```bash
curl -sX PUT https://app.example.com/api/v1/auth/sso/connection \
  -H "Authorization: Bearer $SESSION_TOKEN" \
  -H "X-Org-ID: $ORG_ID" \
  -H 'Content-Type: application/json' \
  -d '{
        "provider_label": "Acme Okta",
        "issuer": "https://acme.okta.com",
        "discovery_url": "https://acme.okta.com/.well-known/openid-configuration",
        "client_id": "0oa1b2c3d4",
        "client_secret": "'"$IDP_CLIENT_SECRET"'",
        "email_domain": "acme.com",
        "jit_provisioning": true,
        "default_role": "member"
      }'
```

**The request fetches the discovery document before it writes anything**, and
compares its `issuer` against the one you supplied. A mismatch is a 422 here
rather than a failed login for every member at once tomorrow morning — this
is the single most common misconfiguration, and it is worth reading the error
rather than retrying.

`email_domain` is not cosmetic. **This IdP will only ever be allowed to sign
in addresses at that domain.** An IdP that returns `ceo@somebody-else.com` is
refused, which is what makes it safe to let one customer's admin configure
their own connection in a product that hosts their competitors.

`default_role` may not be `owner`. An IdP tells this product who somebody is
and nothing about what they may do here.

### 3. Test it before telling anyone

Open `https://app.example.com/api/v1/auth/sso/<org-slug>/start` in a private
window. You should be redirected to the IdP, and back with a session.

That URL is the **always-available entry point**: it works whether or not the
organization has opted into domain hinting, and it is what to put in a
bookmark or an internal wiki.

### 4. Optionally, turn on the domain hint

```bash
curl -sX PUT .../connection -d '{..., "domain_hint_enabled": true}'
```

With it on, typing an `@acme.com` address on the login page replaces the
password field with "Continue with Acme Okta".

**It is off by default because it leaks whether a domain has an account
here** — anyone can type an email and watch the form change. ADR-0006
decision 10 records that trade. Turn it on for a customer whose relationship
with you is public anyway; leave it off otherwise and use step 3's URL.

### 5. Decide what happens to people who are not members yet

- `jit_provisioning: true` (the default) — anybody the IdP authenticates at
  the configured domain gets an account and a membership at `default_role`.
  This is what most customers mean by SSO.
- `jit_provisioning: false` — the IdP can only sign in people who are already
  members. Invite them first; an unknown identity is refused with
  `errors.sso_no_account`.

Group mapping, if they want roles from the IdP:

```json
{"group_claim": "groups", "group_role_mappings": {"acme-admins": "admin"}}
```

**Applied at first sign-in only.** Somebody moved out of `acme-admins`
tomorrow keeps the role they were given — continuous reconciliation needs to
react to the change, and that mechanism is SCIM, which ADR-0006 puts out of
scope. Say this to the customer rather than letting them assume otherwise.

### 6. Turn enforcement on when they ask, not before

```bash
curl -sX PUT .../connection -d '{..., "enforced": true}'
```

Members of the organization can no longer sign in with a password.

**The break-glass**: the *last remaining owner* can always use a password.
Not any owner — the last one. So an organization that enables enforcement
against a broken IdP is inconvenienced rather than locked out, and the fix is
that person signing in and setting `enforced: false`, not a DBA.

Before flipping it, check how many owners the org has:

```sql
SELECT count(*) FROM membership m
  JOIN organization o ON o.id = m.org_id
 WHERE o.slug = 'acme' AND m.role = 'OWNER';
```

If the answer is more than one, **nobody** gets the break-glass. That is
correct — a second owner is a second way in — but it means the recovery path
is "one owner steps down", so it is worth saying out loud first.

## Verification

- `GET /api/v1/auth/sso/connection` returns the connection, and **no
  `client_secret`** — no read ever returns it.
- A member signs in end to end from `/api/v1/auth/sso/<slug>/start`.
- A brand-new person at the domain is provisioned with `default_role`:
  `SELECT role FROM membership WHERE user_id = ...`.
- An existing account with the same email is *linked*, not duplicated:
  `SELECT count(*) FROM "user" WHERE email = '...'` is 1.
- The audit trail has `sso.user_provisioned`, `sso.membership_provisioned` and
  `user.sso_login`, and none of them carries a secret.
- With enforcement on: a member's password login is refused with
  `errors.sso_required`, and the last remaining owner's is not.

## Rollback

- **Turn enforcement off first, always.** `{"enforced": false}` is one
  request and restores password login immediately for everyone who has a
  password. JIT-provisioned accounts have none — they will need a password
  reset, which is why enforcement is the *first* thing to undo and the last
  thing to enable.
- **Disable rather than delete** while you are diagnosing: `{"enabled":
  false}` stops new SSO logins and leaves the configuration to look at.
  Deleting an *enforced* connection is refused outright (409), because
  enforcement outlives the row for as long as the delete takes and a member
  with no password would have nothing left.
- **Turning the module off** removes the routes and stops enforcement, and
  leaves `sso_connection` rows in place (`MODULE_TABLES`). Unlike
  `public_api`, an inert connection is not a credential — re-enabling exposes
  configuration, not a way in — but read the table before you flip it back
  anyway.

## Escalation

**"It worked yesterday and now every login fails."** Almost always a key
rotation at the IdP. The JWKS is cached for an hour, and the validator
re-fetches it **once** when a signature fails against the cached set, so this
should self-heal within one attempt. If it does not, the discovery document
itself has probably moved — `PUT` the connection again with the same values,
which re-fetches and re-validates both.

**`errors.sso_bad_audience`.** The ID token was minted for a different client
id. Either the connection has the wrong one, or their IdP admin gave you the
credentials for a different application in the same tenant.

**`errors.sso_foreign_domain`.** The IdP asserted an address outside
`email_domain`. Working as designed. If the customer genuinely has two
domains, that is two organizations or a change to this column — not something
to work around, because the check is the boundary between tenants.

**`errors.sso_bad_nonce` on a *retried* login.** A stale tab. The `state` and
its nonce are good for ten minutes and for one attempt; starting again is the
fix.

**A user is refused with `errors.sso_subject_conflict`.** The IdP's `sub` is
already linked to a different local account. This happens when somebody's
email changed here but not there, or two accounts were merged upstream.
Resolve it by hand — the row to look at is `oauth_account` where `provider`
starts with `sso:` — because guessing which account is right is how an
account takeover gets automated.

**A native app needs SSO.** It cannot use this flow as-is: the redirect chain
assumes a browser. It needs `ASWebAuthenticationSession` / Custom Tabs and a
deep-link return, on top of the deep-link infrastructure T-261 built. That is
a separate task, not a configuration change.
