# sso

Enterprise single sign-on: one organization, one OIDC identity provider, its
own people signing in with their own credentials (T-331).

Gated by the `enterprise_sso` module toggle, which ships **off** and
**requires `oauth`**. Off, there are no `/api/v1/auth/sso` routes, no
`sso_connection` table, and — the part that matters most — no enforcement:
`password_login_blocked` returns `None` before it queries anything, so a
product that never turns this on pays nothing on its login path.

Not to be confused with `app/modules/oauth`, which is social sign-in (one
Google or GitHub app for the whole product, configured in the environment).
This module is per-organization, configured through the API, and can *refuse*
a password. Same protocol family, different question.

ADR-0006 is the decision record: OIDC now, SAML only on a trigger that has not
fired. `docs/runbooks/sso-onboarding.md` is how to connect a real IdP.

## Public interface

- `app.modules.sso.enforcement.password_login_blocked(session, user_id)` —
  **the only thing another module calls.** `AuthService.login` calls it after
  the password verifies. Safe to call unconditionally; returns `None` when the
  module is off.
- `app.modules.sso.router.router` — `/api/v1/auth/sso/…`: the connection CRUD
  (session-authenticated, `settings:manage`), the opt-in domain hint, and the
  two unauthenticated legs of the login itself.
- `app.modules.sso.service.sso_provider_name(connection_id)` — how an SSO
  identity is namespaced in `oauth_account` (`sso:<uuid>`), so a support query
  can tell a social login from an enterprise one.

`SsoConnection`, both repositories, `discovery`, `oidc` and the services are
internal.

## The five things to know before editing anything here

**`email_domain` is the tenant boundary, not a convenience.** A connection may
only authenticate addresses at its own domain (`_reject_foreign_domain` in
`service.py`). Without that check, an organization admin — who configures their
own connection, by design — could point it at an IdP under their control and
assert `ceo@a-competitor.com`, and JIT provisioning would hand them the
account. Every other check here is protocol correctness; this one is what makes
self-service configuration safe in a product that hosts rivals.

**The ID-token checks are five separate checks with five separate errors.**
`oidc.verify_id_token` refuses signature, `iss`, `aud`, `nonce` and `exp`/`iat`
independently, because collapsing them into one "invalid token" is the
difference between an operator fixing a client id in a minute and reading
packet captures for an afternoon. The error keys are in
`docs/runbooks/sso-onboarding.md`'s escalation section, one heading each.

**Algorithms are allow-listed, and the list is asymmetric-only.**
`oidc.ALLOWED_ALGORITHMS`. A verifier that trusts the token's own `alg` header
will happily verify an HMAC computed with a key the attacker already has — the
client secret, or the public key — which is the classic JWT algorithm-confusion
attack. `alg: none` is refused by joserfc natively; `HS*` is refused by us.

**Discovery and JWKS are cached, and re-fetched exactly once.** An hour's TTL
in Redis (`cache_keys.sso_discovery_key` / `sso_jwks_key`), and on a signature
failure `_verified_claims` force-refetches the JWKS and tries once more —
`for force in (False, True)`, a bounded loop rather than a retry policy. That
single retry is what makes an IdP's key rotation self-heal instead of paging
somebody, and the bound is what stops a forged token turning into unlimited
outbound requests to the IdP.

**`client_secret` is `EncryptedString`, not a digest.** We have to *present* it
to the IdP's token endpoint, so it has to come back. It is in
`__redacted_fields__`, stripped from every audit payload by `_auditable()`, and
absent from `SsoConnectionPublic` — no read path returns it, ever. It is also
now discovered by the key-rotation job (`tests/test_encryption_job.py` asserts
the full column list), so it rotates with everything else.

## The enforcement break-glass

An enforced connection refuses passwords to every member — **except the last
remaining owner**, who can always use one. Not any owner: the last. An org that
enforces SSO against a broken IdP is then inconvenienced rather than locked
out, and the recovery is that person signing in and flipping the flag, not an
operator with a psql prompt. `enforcement.py`'s docstring has the reasoning;
ADR-0006 decision 8 is the record.

Deleting an enforced connection is refused (409) for the same reason: the
window between "the row is gone" and "enforcement is off" is a window in which
a JIT-provisioned member has no password and no IdP.

## Where the module is wired in

- `AuthService.login` — the enforcement check, after the password verifies.
- `AuthService.sso_login` — mints the session. Deliberately *not* a second
  session-minting primitive: this module owns the protocol, `auth` still owns
  what a session is, and 2FA still applies unless the IdP asserted MFA itself
  (`amr`/`acr`, which can raise the bar but never lower it).
- `oauth_account` — the identity link, `provider = "sso:<connection id>"`.
- `MODULE_TABLES` / `MODULE_SURFACE` / `MODULE_BRANCHES` — the toggle,
  its routes and its Alembic branch (`enterprise_sso`, off `a7e4c2f91b60`).

## Testing

`tests/modules/test_sso.py` runs a mock IdP with **real RSA keys** over an
`httpx.MockTransport`, so signature verification is exercised rather than
stubbed. It patches `discovery.new_client`, and that seam exists for a reason
worth not rediscovering: patching `httpx.AsyncClient` globally also replaces
the transport of the *test suite's own* client, and every request to the app
then lands on the mock IdP's catch-all 404. The docstring on `new_client` says
so at the point where somebody would be tempted.

`tests/modules/test_sso_enforcement.py` covers the refusal and the break-glass.
Both use `acme.example`: pydantic's `EmailStr` rejects `.test` as a
special-use TLD.
