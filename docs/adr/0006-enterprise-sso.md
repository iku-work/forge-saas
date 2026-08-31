# ADR-0006: Enterprise SSO — OIDC first, SAML on a trigger

## Status

Accepted — **OIDC is the decision; SAML is a conditional.** The `enterprise_sso`
module ships off, so nothing in this document is running in the committed
configuration. T-331 implements the OIDC service provider. T-332 (SAML) must
not be started until the trigger in [Decision 2](#2-saml-waits-for-a-named-trigger)
has actually fired.

**Date:** 2026-08-31

## Context

Selling to a company with an IT department eventually means being asked "does
it do SSO", and the honest answer has to be a protocol name. There are two,
they are not interchangeable, and the cost of supporting them is wildly
asymmetric.

**OIDC** is OAuth 2.0 with an identity layer: JSON over HTTPS, JWTs signed
with keys published at a JWKS URL, discovery through a well-known document.
This backend already speaks it. `authlib` is a dependency today — the `oauth`
module (T-025) uses its client registry for Google and GitHub sign-in — and
the account-linking path an SSO login has to end in is the same one those
providers already end in. Adding an OIDC SP is new code and no new
infrastructure.

**SAML 2.0** is signed XML over HTTP form posts, and the only credible Python
implementation is `python3-saml`. That package depends on `xmlsec`, which
depends on the native `libxmlsec1` library, which has to be present at
build *and* run time. Concretely, for this repository, that means:

- `libxmlsec1-dev` and `pkg-config` in the backend Dockerfile's builder stage,
  and `libxmlsec1` in the runtime stage — a native-dependency chain in an image
  that currently has none;
- a second CVE cadence to track, on a library whose whole job is parsing
  attacker-supplied XML;
- every base-image bump becoming a build-and-test exercise rather than a
  version number, because native wheels are what break on those.

And XML signature handling has a bug class of its own. XML Signature Wrapping
(XSW) is the attack where an assertion carries a validly-signed fragment
positioned so the signature checker and the assertion parser disagree about
which element they are looking at; every SAML service provider gets to
rediscover it, and the standard defence is a corpus of known-bad vectors in
the test suite rather than confidence.

The pressure to decide now rather than later is that both protocols end in the
same place — a session issued through T-025's account linking, JIT
provisioning, enforcement, and audit — and if that shared half is written
around one protocol's assumptions the second one costs twice. The parts that
are protocol-independent (per-org configuration, JIT provisioning, group
mapping, enforcement with a break-glass) are decided here, once, so T-331 can
build them and T-332 can reuse them verbatim.

The counter-pressure is real too: the customer who actually pays for SSO is
disproportionately likely to be on Entra ID or Okta, and **both of those speak
OIDC**. The IdPs that cannot are older on-premise deployments (ADFS in some
configurations, Shibboleth, a few university federations). "Enterprise
customers need SAML" is true about the population and frequently false about
the specific customer in front of you, which is exactly the shape of
requirement that should be answered by asking rather than by building.

## Decision

### 1. OIDC first, and it is the only protocol implemented

T-331 builds an OIDC relying party on `authlib`, per-organization. No SAML
code, no SAML dependency, no SAML extra installed by default.

### 2. SAML waits for a named trigger

T-332 starts when, and only when, **a customer under a signed contract (or a
contract blocked solely on this) has an identity provider that cannot issue
OIDC, confirmed by their IT contact rather than assumed from the word
"SAML" in a questionnaire.**

That is an observable condition on purpose. "Enterprise interest", "several
prospects asked", and "it looks unprofessional not to have it" are not it — a
security questionnaire asking about SAML is answered by asking which IdP, and
the answer is usually Entra ID or Okta, both of which speak OIDC.

If the trigger fires, T-332's scope is a second front door onto the same
house: SP metadata, ACS, SLO, and the security list in that task (signed
assertions required, replay cache on assertion ID, bounded clock skew, XSW
vectors in the suite). Session issuance, enforcement, JIT provisioning and
audit are reused unchanged.

### 3. SAML ships as an optional extra, so "off" means "not installed"

When T-332 lands, `python3-saml` goes in a `[project.optional-dependencies]`
group named `saml`, installed by `pip install .[saml]`, and the backend
Dockerfile gains the native build dependencies **only** in a SAML image
variant. A product with `enterprise_sso=False` — the default — must not have
`libxmlsec1` in its image at all.

This is the concrete payoff of making SSO a module rather than a feature flag.
A module toggle that only removes routes saves nothing an attacker cares
about; one that removes a native XML parser from the image removes a whole
class of CVE from the product's exposure. T-332's "Done when" states it as a
checkable fact: the default backend image contains no `libxmlsec1`.

### 4. Identity-provider configuration is per organization

One `SsoConnection` row per org (`org_id` unique), holding issuer, client id,
client secret (`EncryptedString` — §7's rule), discovery URL, email domain,
and the policy fields below. Migration on the `enterprise_sso` alembic branch;
`sso_connection` is already in `MODULE_TABLES`.

A single global IdP would mean one tenant's identity provider authenticating
another tenant's users, which is not a multi-tenant product. There is no
version of this that is worth the schema it saves.

### 5. JIT provisioning is on by default, at the lowest role

An authenticated user from a connected IdP whose email is not yet a member of
that org is created and added, with `default_role` — defaulting to `member`,
never `admin` or `owner`. `jit_provisioning` is a per-connection boolean for
organizations that want membership managed explicitly.

The default is on because the alternative ("your IdP authenticated you but you
are not in our database, ask someone") is the failure every SSO rollout hits on
day one. The default role is the lowest because the IdP has told us *who* this
person is, and nothing at all about *what they may do here*.

### 6. Group-to-role mapping: shape now, implementation later

The connection carries a `group_role_mappings: dict[str, MemberRole]` — an IdP
group name (from the `groups` claim) to a role in this product — plus
`group_claim` naming the claim to read, since IdPs disagree about it. T-331
persists and validates the mapping and applies it at JIT provisioning time;
continuous reconciliation (demoting someone whose group membership changed
after their first login) is explicitly **not** in T-331.

Defining the shape now is what stops it from becoming a migration later. Not
implementing reconciliation now is honest: doing it properly means reacting to
group changes, and the mechanism for that is SCIM.

### 7. SCIM is out of scope, and named as such

No SCIM 2.0 provisioning endpoints, in this ADR or in T-331/T-332. Deprovisioning
happens through the existing member-removal path, and an org that needs a
removed user's access to stop *immediately* revokes their sessions the way any
other member removal does.

This is recorded rather than omitted because "we have SSO" is routinely heard
as "we have SCIM", and the two are separate products. If SCIM becomes the
requirement, it gets its own ADR and its own task; it is not a follow-up
commit on T-331.

### 8. Enforcement always keeps a break-glass

`enforced=True` on a connection makes password login fail for members of that
org — except for the last remaining owner, who can always use a password.

An org can otherwise lock itself out permanently by enforcing SSO against a
misconfigured IdP, and the only fix is a DBA at a psql prompt. One
password-capable owner is a smaller hole than that is an outage.

### 9. Web only

The redirect flow T-331 builds is for browsers. A native app cannot use it
as-is: it needs `ASWebAuthenticationSession` / Custom Tabs and a deep-link
return, on top of the deep-link infrastructure T-261 already built. Mobile SSO
is a separate task, stated here so it is discovered by reading rather than by
a customer asking.

### 10. Domain hinting is opt-in per organization

Typing an email whose domain matches an enabled connection can replace the
password field with "Continue with &lt;IdP&gt;". That leaks whether a domain has
an account here, to anyone who can type an email address.

For a template the default is off (`domain_hint_enabled=False`), with
`/auth/sso/{org_slug}/start` as the entry point that always works. A product
whose customer list is public anyway can turn it on and lose nothing.

## Consequences

**What gets better.** SSO ships without a native dependency, on a library the
codebase already depends on and already has account-linking code for. The
default backend image is unchanged — same size, same CVE surface. The
protocol-independent half (per-org config, JIT, group mapping shape,
enforcement, audit) is designed once and is not owed to whichever protocol
happened to be built first.

**What gets worse, or at least harder.**

- **A prospect on a SAML-only IdP is a "no" until someone does T-332.** That is
  the cost of this decision and it is deliberate; the trigger in Decision 2 is
  the escape hatch, and it is written so that answering it does not require
  re-having this argument.
- **A second front door onto authentication.** Every property the password
  path maintains — enforcement, 2FA, audit, rate limiting, session
  attributes — has to be maintained on the SSO path too, and the two can
  drift. T-331's tests carry the four rejection cases (`nonce`, `aud`, `exp`,
  signature) precisely because a redirect flow's failure modes are silent.
- **A per-org secret to rotate.** `client_secret` is encrypted at rest, which
  means it is one more column the key-rotation job (T-245) walks, and one more
  thing whose rotation is a customer-visible operation.
- **`alembic check` needs every branch applied.** `enterprise_sso` is the
  second module to ship off with tables of its own; T-310's notes and
  TEMPLATE.md already record the workaround, and T-331 will hit it identically.
- **Two documents that must stay in sync.** `enterprise_sso` requires `oauth`,
  in `backend/app/core/modules.py` and in `product.config.ts`. `validate_modules()`
  fails the boot on the illegal combination, which is the mitigation, but the
  parity test is what keeps the two files agreeing in the first place.

**What is now checkable.** T-332's "Done when" — no `libxmlsec1` in the default
image — is a `docker run ... ldconfig -p` away, and Decision 3 is why it can be
asked at all.

## Alternatives considered

**SAML first, OIDC later.** The historical order, and the one "enterprise SSO"
still connotes. It loses on cost per unit of coverage: SAML is the expensive
protocol (native dependency, XML signature bug class, an image variant) and
covers a shrinking minority of IdPs, while OIDC is the cheap one and covers
Entra ID and Okta — i.e. most of the customers who would ask. Building the
expensive half first also means the shared half gets shaped by SAML's
assumptions (attribute statements, NameID formats) and then bent for OIDC.

**Both at once, in T-331.** Rejected on scope rather than on principle: it
doubles a task that already touches authentication, and it puts the native
dependency into the image on the day the module is still off for every
product. If the trigger fires while T-331 is in flight, doing them together is
a reasonable amend — the shared half is designed for it.

**A hosted identity provider in front of the whole product** (Auth0, WorkOS,
Clerk). Genuinely the least code: they terminate SAML and OIDC and hand back
one protocol. Rejected because this is a *template*, and a template that makes
a vendor account a prerequisite for signing in has changed what it is. It also
inverts the module story — you cannot toggle off a dependency the login page
is built on. Worth revisiting for a product (not the template) whose SSO
requirements outgrow one connection per org: WorkOS in particular is close to
free at low volume and absorbs exactly the maintenance this ADR is trying to
avoid.

**`pysaml2` instead of `python3-saml`.** The other maintained Python SAML
library, and it avoids nothing that matters: it still needs `xmlsec1` (as a
binary rather than a linked library, which is not an improvement — it is a
subprocess parsing attacker-controlled XML). Its configuration surface is
larger and its documentation thinner. If T-332 happens, `python3-saml` is the
choice, and this is why.

**Enforcing SSO with no break-glass.** Cleaner to reason about and defensible
on a security questionnaire. Rejected because the failure mode is a tenant
that cannot sign in at all and a support path that ends in manual database
surgery — see ADR-0005, which made the same call about the maintenance switch
for the same reason: the safety valve is worth more than the purity.
