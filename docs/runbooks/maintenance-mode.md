# Maintenance mode & global switches runbook (T-263)

Three switches live in the `product_settings` singleton row and take effect
without a redeploy: **maintenance mode** (503 everything except the surfaces
that let you undo it), the **global banner**, and the per-module **emergency
kill switch**. They are read by
`backend/app/middleware/maintenance.py` and written through
`/api/v1/admin/settings/product` — the admin UI at **Admin → Settings**.

Deploy-time configuration is a different thing and lives in
`product.config.py` (see `docs/runbooks/module-toggle-guide.md`); a
single customer's entitlements are a third thing and live in feature flags.
This runbook is only about the global, runtime switches.

Why it is built this way — in particular why the middleware fails *open* when
it cannot read its own settings, which is the behaviour most likely to surprise
you mid-incident — is [ADR-0005](../adr/0005-runtime-operator-switches.md).

## Trigger

Reach for this when:

- you need a planned write-blocking change (a long migration, a data backfill,
  a cutover) and users must not be writing during it → **maintenance mode**;
- one module is actively harming users or a vendor is melting down and you
  want that module off *now*, with the rest of the product up → **kill
  switch**;
- you need to tell every signed-in user something (an incident, a scheduled
  window, a status page link) → **global banner**;
- abuse or a botched launch means you need to stop new accounts →
  **`signup_disabled`**.

**Not this runbook.** A single third-party integration failing on its own is
already handled by circuit breakers — see `docs/runbooks/degraded-mode.md`,
and do not reach for a kill switch for something the breaker is already
degrading gracefully. A slow database is `docs/runbooks/slow-queries.md`. A
deploy that needs rolling back is `docs/runbooks/deploy-render.md`;
maintenance mode is not a substitute for a rollback, it only buys time for one.

## Pre-checks

- **A superadmin account with 2FA enrolled**, and its password/second factor
  to hand. `require_superadmin` refuses a superadmin without TOTP or a
  security key (T-218), so an account that has never enrolled one cannot turn
  maintenance mode back off. Verify this *before* you turn it on.
- **A second operator who can also reach the admin surface.** Maintenance mode
  is the one switch that can strand you; a colleague who can undo it is the
  cheapest possible mitigation.
- **Know the URL you will use to undo it**: `https://<your-host>/en/admin/settings`.
  Have it open in a second tab before step 1.
- **Decide the message first.** It is shown verbatim to every user, in one
  language, and it is not translated. "Back at 03:00 UTC — status:
  status.example.com" beats "Maintenance in progress".
- The migration in `alembic/versions/core/a7e4c2f91b60_product_settings_singleton.py`
  must be applied (it seeds the `id=1` row). If the row is missing, every
  switch reads as its default and the admin page will still work — the write
  path re-creates the row — but you will have no history.

## Steps

### 1. Announce, before you break anything

Admin → Settings → **Show a global banner**, severity `warning`, message
naming the window and where to look for updates. Save.

**Expected:** the toast reads "Settings saved.", and the banner appears at the
top of your own admin screen within a second (the page refreshes `/config`
itself after a save). Other users' open tabs pick it up within 60 seconds.

### 2. Turn maintenance mode on

Admin → Settings → tick **Maintenance mode**, fill in **Maintenance message**,
save.

**Expected:** a red "Maintenance mode is ON" alert appears at the top of the
settings page. From another browser (or a private window), any app request now
answers `503` with

```json
{"data": null,
 "error": {"code": "maintenance", "message_key": "errors.maintenance",
           "message": "<your message>"},
 "meta": {"request_id": "…"}}
```

and a `Retry-After: 60` header.

If you would rather do it without a browser — the admin API is reachable with
any superadmin bearer token:

```bash
curl -sS -X PATCH https://<your-host>/api/v1/admin/settings/product \
  -H "Authorization: Bearer <superadmin-access-token>" \
  -H "Content-Type: application/json" \
  -d '{"settings": {"maintenance_mode": true, "maintenance_message": "Back at 03:00 UTC"}}'
```

**Expected:** HTTP 200 and a body whose `data.settings.maintenance_mode` is
`true`.

### 3. Confirm what is still up

These stay reachable *by design*, and each one matters:

| Path | Why it is exempt |
|---|---|
| `/health`, `/health/live`, `/health/ready` | An orchestrator that reads a maintenance 503 as "unhealthy" restarts the container in a loop. |
| `/metrics` | Maintenance is when you most want the graphs moving. |
| `/api/v1/config` | How web and mobile tell a maintenance window apart from a network outage, and where they read your message from. |
| `/api/v1/admin/*` | The surface that undoes this. Still behind `require_superadmin` — exempting it grants nobody anything. |
| `/api/v1/auth/login`, `/refresh`, `/logout`, `/2fa/*` | So an operator who was *not* already signed in can sign in and reach the admin surface. Ordinary users can sign in too, and are then 503'd by everything else — which is the honest outcome, and gives the frontend a session to render the maintenance screen with. |
| `OPTIONS` (CORS preflight) | A blocked preflight means the browser never sends the real request, so the user sees a CORS error instead of your message. |

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<your-host>/health
curl -s https://<your-host>/api/v1/config | jq .data.maintenance
```

**Expected:** `200`, and `{"enabled": true, "message": "Back at 03:00 UTC"}`.

### 4. Do the work

Nothing in this runbook. Note that **background workers and scheduled jobs are
not affected** — maintenance mode is HTTP-request middleware only. If your
maintenance requires the workers stopped too, stop them separately (scale the
worker service to zero); there is no switch for it here.

### 5. Turn it back off

Untick **Maintenance mode**, save. Then clear the banner (untick **Show a
global banner**) once you are confident.

**Expected:** the red alert on the settings page disappears; a request to any
app endpoint answers normally again. Clients on the maintenance screen recover
by themselves within 60 seconds — they poll `/config`.

### 6. Read back the audit trail

Admin → Audit log, filter action `product_settings.changed`.

**Expected:** one `elevated`-severity row per save, each with a diff naming
only the keys that moved:

```json
{"maintenance_mode": {"from": null, "to": true}}
```

## Verification

From outside the system, not from the settings page:

1. **Positive:** an unauthenticated `GET /api/v1/config` reports
   `maintenance.enabled: false` and `banner: null`.
2. **Negative — the check that catches a silent no-op:** sign in as an ordinary
   user in a private window and load the app. A stale `maintenance_mode` that
   never got written would look identical from the admin page (which reads the
   database directly) while every user is still being 503'd. This is the check
   that distinguishes them.
3. **The audit trail** has a row turning it off as well as the row turning it
   on. A window with only an "on" row is a window somebody ended by editing
   the database by hand, which is worth knowing.

## Rollback

Every switch here is a single boolean and reverses instantly; there is no
point of no return and nothing to restore.

The genuine failure mode is not "cannot undo", it is **"cannot reach the thing
that undoes it"**. In order of preference:

1. **The admin API**, with a superadmin token — see step 2's `curl`. Works
   even when the web app cannot render.
2. **Directly in the database.** The middleware reads through a 60s Redis
   cache, so a direct write takes up to a minute to take effect unless you also
   drop the key:

   ```sql
   UPDATE product_settings SET settings = settings - 'maintenance_mode' WHERE id = 1;
   ```

   ```bash
   redis-cli DEL product_settings:all
   ```

   The `CHECK (id = 1)` constraint means there is exactly one row to fix; if a
   `SELECT` here returns anything other than one row, stop and escalate.
3. **Clearing everything**: `UPDATE product_settings SET settings = '{}'::jsonb
   WHERE id = 1;` — resets every switch, including the banner and the kill
   switches, to its default.

A write made in the database this way leaves **no audit event**. Say so in the
incident notes; the audit trail is the only record these switches have.

## Escalation

**The whole product is 503ing and nobody turned maintenance mode on.** Check
`GET /api/v1/config` first — if `maintenance.enabled` is `false` there, this
middleware is not what is answering, and you are looking at
`docs/runbooks/degraded-mode.md` (a `service_unavailable` code) or the
`/health/ready` dependency check instead. The `code` in the error envelope
tells you which: `maintenance` is this runbook, `service_unavailable` is not.

**A kill switch was set for a module and nothing changed.** Only modules whose
whole HTTP surface sits under a prefix of their own can be killed —
`billing`, `file_storage`, `notifications`. `gdpr` and `feature_flags` mount
under paths shared with auth and tenancy, so killing them by prefix would take
a user's ability to read their own account with it, and the server rejects
those with a 422 rather than accepting a switch that does nothing. See
`KILLABLE_MODULE_PREFIXES` in
`backend/app/modules/product_settings/keys.py`.

**Redis is down during a maintenance window.** The settings read degrades to a
database query *per request* (one indexed primary-key lookup) instead of a
cached one. The switches keep working; the connection pool is what to watch.
If both Redis and Postgres are unreachable, the middleware fails **open** —
deliberately — and stops enforcing maintenance mode at all: a switch whose
backing store is gone must not also become the outage. You will see
`maintenance.settings_unreadable` in the logs.

**You are locked out** (no superadmin with 2FA, no database access). This is
the case the pre-checks exist to prevent. Escalate to whoever holds production
database credentials and use rollback option 2; there is no other path in, by
design.

Before restarting any attempt, capture: the `request_id` from a 503 response,
the `product_settings.changed` audit rows for the window, and the output of
`SELECT settings, updated_at, updated_by FROM product_settings WHERE id = 1;`.
