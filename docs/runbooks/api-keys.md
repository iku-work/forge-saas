# API keys: minting, rotating, revoking (T-310, T-311, T-312)

Operating the `public_api` module's credentials — the keys a third party
presents instead of a user session. The design is in
`backend/app/modules/api_keys/models.py` (why the digest is SHA-256 and not
argon2) and `backend/app/core/principal.py` (why an endpoint is closed to keys
until somebody opens it deliberately).

The module ships **off**. A product that has never turned it on has no
`api_key` table and nothing here applies.

## Trigger

- An integration needs to call the API without a person signed in.
- A key has leaked, or is suspected to have leaked — go straight to
  [Revoking a key](#3-revoke-a-key-immediately), which is the only step of
  this runbook with a clock on it.
- Someone has left the organization and you are working through what they had
  access to. **Read [Offboarding](#offboarding) below before you assume their
  keys are gone — they are not.**

**Not this runbook:** a user who cannot sign in
(`docs/runbooks/account-recovery.md`), or an integration being rate-limited
where the fix is a bigger budget rather than a new key — that is step 4 here,
not a new credential.

## Pre-checks

- `public_api` is `True` in `backend/product/config.py` **and**
  `publicApi: true` in `product.config.ts`. Both, always: a half-applied
  toggle is a backend that 404s and a UI that still offers the page.
- The migration has been applied: `make migrate` reaches `public_api@head`.
  Check with `python -m app.core.migrations plan` — the line for `public_api`
  should read `enabled  -> applying`.
- You hold the **org-manage** permission (owner or admin). A plain member
  cannot even list keys: the list shows every prefix and every scope in the
  organization, which is what each credential can reach.
- You know which scopes the integration actually needs. The vocabulary is
  `backend/app/modules/api_keys/scopes.py`, and it is short on purpose — a
  scope that does not exist cannot be granted by accident.

## Steps

### 1. Mint a key

```bash
curl -sX POST https://app.example.com/api/v1/api-keys \
  -H "Authorization: Bearer $SESSION_TOKEN" \
  -H "X-Org-ID: $ORG_ID" \
  -H 'Content-Type: application/json' \
  -d '{"name":"billing-sync","scopes":["orgs:read","members:read"],"expires_at":"2027-01-01T00:00:00Z"}'
```

**Expected:** a 200 whose `data.key` is the full credential, exactly once:

```json
{"data":{"key":"fk_live_3f2a91c04d7b_x9…","api_key":{"prefix":"3f2a91c04d7b", …}}}
```

**Put it in the integration's secret store now.** It is not stored here —
only a SHA-256 digest is — so this response is the only time anyone will ever
see it. Re-reading the key later returns the prefix and nothing else, and
there is no endpoint that can recover it.

`expires_at` is optional and there is no default. That is deliberate: a key
that silently stops working ninety days after an integration shipped is an
outage nobody scheduled. Set one when you know the integration is temporary.

### 2. Rotate a key

There is no rotate endpoint, and that is the design rather than a gap.
Rotation is two operations with a window between them, and an endpoint that
did both at once would close the window:

1. Mint a second key with the same scopes (step 1).
2. Deploy it to the integration.
3. Confirm the old key has stopped being used: `last_used_at` on it stops
   advancing. It is throttled to one write per minute, so give it five.
4. Revoke the old key (step 3).

**Expected after step 3:** `GET /api/v1/api-keys` shows the old key's
`last_used_at` unchanged across two reads several minutes apart.

### 3. Revoke a key immediately

```bash
curl -sX POST https://app.example.com/api/v1/api-keys/$KEY_ID/revoke \
  -H "Authorization: Bearer $SESSION_TOKEN" -H "X-Org-ID: $ORG_ID"
```

**Expected:** a 200 with `revoked_at` set, and the very next request presenting
that key gets a 401. Not "within a minute" — the prefix lookup is cached for
60 seconds and revocation busts that entry before it returns, which is the
whole reason the cache-invalidation matrix in `app/core/cache_keys.py` lists
this path.

The row **stays visible** in the list, with `revoked_at` set. That is
different from deleting it, and it is what lets an incident review answer
"what could this key reach, and when did it stop".

### 4. Raise one integration's rate limit

The default is `API_KEY_RATE_LIMIT_PER_MINUTE` (600) for every key. One key
can be raised without raising everyone:

```bash
curl -sX PATCH https://app.example.com/api/v1/api-keys/$KEY_ID \
  -H "Authorization: Bearer $SESSION_TOKEN" -H "X-Org-ID: $ORG_ID" \
  -H 'Content-Type: application/json' -d '{"rate_limit_per_minute": 3000}'
```

**Expected:** the next response to that key carries
`X-RateLimit-Limit: 3000`. No restart, no deploy: the override is read off the
key's row, and the write busts the cached copy.

Before raising a limit, look at the `X-RateLimit-*` headers the integration is
already receiving. Every response carries them, including successful ones, so
"how close is it" is answerable without waiting for a 429.

## Verification

```bash
curl -si https://app.example.com/api/v1/orgs/$ORG_ID/members \
  -H "Authorization: Bearer $THE_KEY" -H "X-Org-ID: $ORG_ID" | head -20
```

**Expected:** a 200, with `X-RateLimit-Limit`, `X-RateLimit-Remaining` and
`X-RateLimit-Reset` on the response.

Three negative checks, each of which would still look fine if something had
silently gone wrong:

| Check | Expected |
|---|---|
| The same key against an endpoint you did **not** open (`/api/v1/auth/me`) | `401` — keys are opt-in per endpoint, never blanket |
| The same key with a wrong `X-Org-ID` | `403 errors.not_org_member` |
| The key with one character changed | `401 errors.invalid_api_key`, the *same* message an unknown key gets |

That last one matters: a different message for "right prefix, wrong secret"
would turn the prefix — which is printed in the management UI and is not a
secret — into an oracle for which keys are real.

## Rollback

**Revoking is not reversible.** `revoked_at` has no endpoint that clears it,
on purpose: "un-revoke" is not an operation anyone should be able to perform
under pressure. If a key was revoked in error, mint a new one and redeploy the
integration — which is the same work as a rotation, minus the overlap window.

**Turning the module off** (`public_api=False`) is reversible and is the wider
rollback. The routes disappear and every existing key stops authenticating
immediately — the auth dependency checks the toggle, not just the routing
table, because the rows survive the toggle by design (`MODULE_TABLES`). Turn
it back on and the same keys work again; nothing was deleted.

## Escalation

### Offboarding

**Deactivating a member does not revoke the keys they minted, and this is
deliberate.** A key belongs to the *organization*, not to the person who
created it; revoking it because a colleague left would take down an
integration that has nothing to do with them.

So when someone leaves, the checklist has an extra line:

```bash
curl -s https://app.example.com/api/v1/api-keys \
  -H "Authorization: Bearer $SESSION_TOKEN" -H "X-Org-ID: $ORG_ID" \
  | jq '.data[] | select(.created_by == "'"$DEPARTING_USER_ID"'") | {id, name, last_used_at}'
```

Review each one and decide. Most will be integrations that should keep
running; the ones that were personal automation should be revoked.

The same applies to a GDPR erasure (T-036): erasing a user **nulls**
`created_by` and leaves the key alive. After that the key can no longer be
attributed to anyone, which is why the review above has to happen *before* the
purge grace period elapses, not after.

### Other failure modes

- **A key stopped working and nothing was revoked.** Check `expires_at`, then
  check whether `public_api` is still enabled — the toggle is the one thing
  that kills every key in the product at once, and a config change on a
  different branch is an easy thing to miss.
- **429s that do not match the configured limit.** Two limiters can produce
  one: the per-key budget (`api_key:<id>`) and `@api_limit`'s per-address one,
  which an integration behind a single NAT can exhaust on its own. The
  response body's `error.code` tells them apart —
  `errors.api_key_rate_limited` is this runbook's, plain `rate_limited` is the
  other one, and the fix for the second is not a bigger key budget.
- **A leaked key in a public repository.** Revoke first (step 3), ask
  questions after. Then search the audit feed for `api_key.created` to find
  who minted it and what scopes it held; every key-authenticated write is
  logged with `actor_type=api_key` and the key's id, so the blast radius is
  answerable from the audit log rather than from guesswork.
