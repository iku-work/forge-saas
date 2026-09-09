# api_keys

Org-scoped API keys: a second kind of principal, so a third party can call the
API without a user session (T-310, T-311, T-312).

Gated by the `public_api` module toggle, which ships **off** — an org-scoped
credential is attack surface nobody asked for, so a product opts in.

Scaffolded from `backend/app/modules/_template` and then rewritten; the layer
shape it left (router → service → repository → models, imports flowing one
way) is intact and is what `.importlinter` enforces.

## Public interface

- `app.modules.api_keys.service.ApiKeyService` — mint, list, rename, revoke
  and soft-delete a key, org-scoped. **The only entry point another module may
  use for management** (§0.1: cross-module access goes through services).
- `app.modules.api_keys.service.ApiKeyAuthenticator` — resolve a presented
  key to a row, and stamp `last_used_at` at most once per key per minute.
  Used by exactly one caller, `app.core.principal.require_scope`.
- `app.modules.api_keys.scopes` — the scope vocabulary and its mapping onto
  the internal `Permission` set. A published contract: renaming a scope breaks
  every integration that stored it.
- `app.modules.api_keys.rate_limit` — T-312's per-key budget, keyed on the
  key's id.
- `app.modules.api_keys.router.router` — the *management* surface under
  `/api/v1/api-keys/`, session-only.
- `app.modules.api_keys.schemas` — the request/response contract the
  generated TypeScript client is built from.

`ApiKeyRepository`, `ApiKeyLookupRepository` and `ApiKey` are internal.

## The three decisions worth knowing before editing anything here

**The digest is SHA-256, not argon2.** This is a 256-bit random secret, not a
human-chosen password: there is no dictionary for a work factor to slow down,
and brute force is already out of reach. Argon2 would put ~100 ms of CPU on
the hot path of every API request — the exact failure T-223's load test caught
on login. Comparison is `hmac.compare_digest`. The full reasoning is in
`models.py`'s docstring; do not "harden" this without reading it.

**The prefix is hex.** `secrets.token_urlsafe` includes `_`, `_` separates the
prefix from the secret, and a prefix containing one cut the key in the wrong
place — roughly one mint in six was unusable. `tests/modules/test_api_keys.py`
mints two hundred keys per run because of it.

**The management surface is session-only.** Nothing under `/api/v1/api-keys`
carries `require_scope`. A key that could mint another key is a
privilege-escalation primitive.

## Events emitted

Audit events, via `app.modules.audit.service.AuditService`. None of them ever
carries the key — the prefix is public by construction, the secret is not
stored, and an audit feed is read by more people than a database is.

| action            | when                                                    |
| ----------------- | ------------------------------------------------------- |
| `api_key.created` | `ApiKeyService.create_key` (diff: name, prefix, scopes) |
| `api_key.updated` | `ApiKeyService.update_key` (diff: the changed fields)   |
| `api_key.revoked` | `ApiKeyService.revoke_key` (diff: prefix)               |
| `api_key.deleted` | `ApiKeyService.delete_key` (diff: prefix)               |

## What this module owns elsewhere in the tree

| Where | What |
|---|---|
| `alembic/versions/public_api/` | the `api_key` table, on its own branch |
| `app/core/cache_keys.py` | `api_key_lookup_key`, and its invalidation matrix entry |
| `app/core/principal.py` | `CurrentPrincipal` and `require_scope` — cross-cutting, so not here |
| `app/modules/tenancy/router.py` | the two routes a key can actually call |
| `docs/runbooks/api-keys.md` | minting, rotation, revocation, and offboarding |

## Cache invalidation

The prefix → row lookup is cached for 60 s. Every write that changes whether a
key authenticates, or with what, busts it: create, update (the rate-limit
override is read off the cached row), revoke and delete all call
`ApiKeyService._invalidate`. `last_used_at` deliberately does **not** — see
the matrix in `app/core/cache_keys.py`.

## Adding a scope

1. Add the member to `Scope` in `scopes.py`.
2. Map it to the `Permission` a *session* needs for the same door in
   `SCOPE_PERMISSIONS`. `tests/modules/test_api_key_scopes.py` fails on a
   member with no mapping, and on a `:read` scope mapped onto a `:manage`
   permission.
3. Put `Depends(require_scope(Scope.YOUR_SCOPE))` on the route that scope
   opens. `tests/modules/test_api_key_auth.py` pins the resulting surface, so
   the new route shows up as a one-line diff in that test — which is the
   review conversation, not an obstacle to it.
4. If the route mutates anything, read
   `test_no_key_callable_endpoint_writes_anything_yet`: the audit trail needs
   an `actor_type` column before the first key-authenticated write ships.
