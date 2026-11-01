# ADR-0009: A declared data-sensitivity level that the deploy enforces

## Status

Accepted

**Date:** 2026-11-01

## Context

Every product built from this template inherits the same infrastructure
defaults, and some of them are trade-offs that fit one kind of product and
not another. The off-host backup is the one that forced this: the default is a
Cloudflare R2 bucket, chosen because every product already has a Cloudflare
account, and an R2 token that can write can also list and delete. A 30-day
bucket lock protects the newest backups from a compromised box; older ones it
does not. For ordinary account data that is a fair price for one account
fewer. For health records or financial data it is not.

The hard part is not writing the stricter rule, but making sure a product that
needs it gets it: a default inherited silently is the failure mode, and a
check that trusts a declared provider name ("we use B2") rather than the
property ("this key cannot delete") passes the day someone misconfigures it.

Most of the rules a sensitivity level might carry already hold for every
product: Sentry never receives personal data (`send_default_pii=False`, no
setting turns it on), and impersonation already needs a written reason and is
audited. The level only needs rules that differ between products.

## Decision

`DATA_SENSITIVITY` is a setting (`app/core/config.py`) with two values and
**no default**:

- `standard`: ordinary account data, names and emails, files users choose to
  upload.
- `high`: GDPR special-category data (health, biometrics, beliefs),
  financial account data, children's data, or customers' own secrets.
  `docs/security/data-classification.md` says which applies.

The stricter level is `high`, not `sensitive`, because
`data-classification.md` already uses "sensitive" for individual columns, where
it means personal data: `User.email` is a sensitive column in a `standard`
product. The column scale says how each field is protected; this one says what
the product as a whole holds.

It is an environment setting, in each product's Bitwarden project, rather than
a constant in `backend/product/config.py`: a committed value would be
inherited from the template by every product that forks it, which is exactly
the decision this exists to force.

What it enforces:

| Rule | `standard` | `high` | Where |
|---|---|---|---|
| The level is declared | required | required | backend refuses to boot outside dev |
| Backups leave the box (`BACKUP_REMOTE` set) | required | required | deploy refuses to migrate |
| The box's backup key cannot list or delete | – | required | deploy runs `scripts/check-backup-remote.sh` |
| Impersonation | allowed (reason, audit) | refused | `AdminImpersonationService.start` |

The key check tests the property on every deploy: it uploads a small file,
then requires listing the bucket and deleting that file to be refused. R2
fails it; a Backblaze B2 application key with `writeFiles` only passes it.

## Consequences

- A product cannot reach production without deciding its level, and a
  high-sensitivity product cannot ship on the default backup setup.
- **A high-sensitivity product needs a second account** (Backblaze B2 or another
  provider with upload-only keys), and its backup script has to work without
  listing the bucket. The passing side of the key check is covered by tests
  with a stand-in `rclone` only; it has not met a real upload-only key yet,
  because no product has one. The first high-sensitivity product proves it.
- **Support loses impersonation** on high-sensitivity products and works from what
  the user shows them. That is the point, and it is a real cost to support.
- Each high-sensitivity deploy leaves one small file in the backup bucket (the
  upload the check needs); the bucket's expiry rule removes it.
- Every deployment needs one more Bitwarden value. Missing, the backend says
  which and why.

## Alternatives considered

- **A boolean `SENSITIVE_DATA_PRESENT`.** Nearly every SaaS stores personal
  data, so it would be true everywhere and mean nothing, or set to false to
  get past a block. Two defined levels say what changes.
- **A committed constant in the product config.** Reviewable, but inherited
  from the template by default, which defeats the declaration.
- **Checking the provider name.** Simpler, and wrong the day a B2 key is
  created with delete rights. The deploy tests what the key can do instead.
- **More rules now** (Sentry, data residency). Each one either already holds
  for every product or has no checkable form yet. Rules are added here when
  they exist.
