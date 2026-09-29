#!/usr/bin/env bash
#
# PROD-05: a scheduled, encrypted, off-host Postgres backup for the
# self-hosted path (docker-compose.prod.yml).
#
# The audit found one dump, four days old, mode 0664, no schedule, no
# off-host copy, and no restore ever executed. The runbook said as much
# itself -- an operator "is expected to wire up their own". That is a
# reasonable thing to say about retention policy and a bad thing to say
# about whether backups happen at all, so this is the default the template
# ships and `docs/runbooks/backup-restore.md` schedules.
#
# Three decisions worth not re-deriving:
#
#   * `pg_dump -Fc`, not a volume tarball. Custom format is consistent
#     against a running database, compresses, and restores selectively with
#     `pg_restore`. Tarring a live volume produces a file that restores into
#     a corrupt cluster often enough to be worthless and rarely enough that
#     nobody notices until they need it.
#
#   * age with a *public* recipient, not a passphrase. The box encrypts; it
#     cannot decrypt. A compromised host therefore cannot read its own
#     backup history, and the private key lives wherever the operator keeps
#     it -- which is the same place ENCRYPTION_KEY's recovery path lives,
#     because a restored database is ciphertext without it.
#
#   * It fails loudly rather than writing plaintext. A backup script whose
#     encryption silently degrades is worse than no script: it produces a
#     file that looks like a backup, and an unencrypted copy of every row in
#     the product, off-host, forever.
#
# Usage:  BACKUP_AGE_RECIPIENT=age1... ./scripts/backup-postgres.sh
# Env:
#   BACKUP_AGE_RECIPIENT   (required) age public key. Generate the pair with
#                          `age-keygen -o key.txt` on a machine that is NOT
#                          this box, and keep key.txt off it.
#   BACKUP_DIR             default /var/backups/forge
#   BACKUP_RETENTION_DAYS  default 14
#   BACKUP_REMOTE          optional rclone target, e.g. "b2:forge-backups"
#   COMPOSE_FILE           default docker-compose.prod.yml

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/forge}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

die() {
    echo "backup-postgres: $*" >&2
    exit 1
}

[ -n "${BACKUP_AGE_RECIPIENT:-}" ] ||
    die "BACKUP_AGE_RECIPIENT is not set. Refusing to write an unencrypted backup.
     Generate a keypair off this host with 'age-keygen -o key.txt', keep
     key.txt somewhere this box cannot reach, and set this to the public
     key it prints."

command -v age >/dev/null 2>&1 ||
    die "age is not installed (https://github.com/FiloSottile/age).
     Refusing to write an unencrypted backup."

# 0600 on everything this script creates, including the directory. The
# audit found 0664 -- world-readable backups of every row in the product.
umask 077
mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/forge-$stamp.dump.age"

# Written to a .partial first and renamed only on success, so a run killed
# half way through (the box rebooting, the disk filling) never leaves a
# truncated file that looks like a backup to the retention prune below.
partial="$target.partial"
trap 'rm -f "$partial"' EXIT

echo "backup-postgres: dumping to $target"

# -T: no TTY, this is a pipe. `pg_dump` runs inside the container so the
# host needs no postgres client, and connects over the container's own
# socket so it bypasses pgbouncer -- transaction pooling and a long-running
# dump do not mix (docs/adr/0002-pgbouncer.md).
docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_dump -Fc -U "${POSTGRES_USER:-postgres}" "${POSTGRES_DB:-forge}" |
    age -r "$BACKUP_AGE_RECIPIENT" -o "$partial"

# A pg_dump that fails mid-stream still produces bytes, and `set -o
# pipefail` catches the exit code -- but an empty or absurdly small file is
# the other failure mode (a dump of a database that is not there yet).
size="$(wc -c <"$partial")"
[ "$size" -gt 1024 ] || die "dump is only $size bytes -- refusing to keep it"

mv "$partial" "$target"
trap - EXIT
echo "backup-postgres: wrote $target ($size bytes)"

if [ -n "${BACKUP_REMOTE:-}" ]; then
    command -v rclone >/dev/null 2>&1 ||
        die "BACKUP_REMOTE is set but rclone is not installed.
     The local copy at $target is fine; it is just not off-host yet."
    echo "backup-postgres: copying to $BACKUP_REMOTE"
    rclone copy "$target" "$BACKUP_REMOTE"
    echo "backup-postgres: copied"
else
    echo "backup-postgres: BACKUP_REMOTE unset -- local copy only." >&2
    echo "backup-postgres: a backup on the same disk as the database is not a backup." >&2
fi

# Prune last, and only after a successful run: an expiry that fires when
# today's backup failed deletes the good ones and keeps nothing.
deleted="$(find "$BACKUP_DIR" -name 'forge-*.dump.age' -type f \
    -mtime "+$BACKUP_RETENTION_DAYS" -print -delete | wc -l)"
echo "backup-postgres: pruned $deleted backup(s) older than $BACKUP_RETENTION_DAYS days"
