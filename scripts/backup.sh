#!/usr/bin/env bash
#
# PROD-05: a scheduled, encrypted, off-host backup of the self-hosted path
# (docker-compose.prod.yml): the Postgres database and the uploaded files in
# MinIO.
#
# The audit found one dump, four days old, mode 0664, no schedule, no
# off-host copy, and no restore ever executed. The runbook said as much
# itself -- an operator "is expected to wire up their own". That is a
# reasonable thing to say about retention policy and a bad thing to say
# about whether backups happen at all, so this is the default the template
# ships and `docs/runbooks/backup-restore.md` schedules.
#
# Decisions worth not re-deriving:
#
#   * `pg_dump -Fc`, not a volume tarball. Custom format is consistent
#     against a running database, compresses, and restores selectively with
#     `pg_restore`. Tarring a live volume produces a file that restores into
#     a corrupt cluster often enough to be worthless and rarely enough that
#     nobody notices until they need it.
#
#   * Files through MinIO's own API (`mc mirror`), not its volume. The
#     volume holds MinIO's internal format, encrypted with
#     MINIO_KMS_SECRET_KEY; the API hands back the files themselves, so the
#     archive restores into any S3 store without that key. The MinIO image
#     has `mc` and no `tar`: `docker cp ... -` streams the mirror out as a
#     tar. ponytail: a full copy every run, staged inside the container;
#     fine while the bucket is small, an incremental scheme when it is not.
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
#   * A full run reports to a Sentry cron monitor, so a backup that fails or
#     stops running reaches someone instead of a journal nobody reads.
#
# Usage:  ./scripts/backup.sh        the daily run: database, files, check-in
#         ./scripts/backup.sh db     the database only (the deploy, before it
#                                    migrates)
# Env (scripts/bootstrap-hetzner-box.sh writes them to ~/forge-backup.env):
#   BACKUP_AGE_RECIPIENT   (required) age public key. Generate the pair with
#                          `age-keygen -o key.txt` on a machine that is NOT
#                          this box, and keep key.txt off it.
#   BACKUP_DIR             default /var/backups/forge
#   BACKUP_RETENTION_DAYS  default 14 (local copies; the remote keeps its own)
#   BACKUP_REMOTE          rclone target, e.g. "r2:forge-prod-backups"
#                          (scripts/set-backup-remote.sh)
#   BACKUP_SENTRY_DSN      default SENTRY_DSN from backend/.env
#   COMPOSE_FILE           docker compose's own variable: colon-separated,
#                          e.g. "docker-compose.prod.yml:docker-compose.tls.yml"

set -euo pipefail

WHAT="${1:-all}"
case "$WHAT" in all | db) ;; *) echo "usage: backup.sh [db]" >&2; exit 2 ;; esac
BACKUP_DIR="${BACKUP_DIR:-/var/backups/forge}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
# Exported, not passed as -f: COMPOSE_FILE is docker compose's own
# variable and takes a colon-separated list, so a stack assembled from
# overlays (prod + tls + host-build, which is what the reference
# deployment runs) works without this script knowing how many files
# there are. A single -f would silently use the base file alone --
# which on that deployment is an edge with no site in it.
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

die() {
    echo "backup: $*" >&2
    exit 1
}

# The Sentry cron check-in, derived from the DSN the deploy already ships.
# Best effort: Sentry being down must not fail a backup that worked.
checkin_url=""
if [ "$WHAT" = all ]; then
    dsn="${BACKUP_SENTRY_DSN:-$(sed -n 's/^SENTRY_DSN=//p' backend/.env 2>/dev/null | tr -d "\"'")}"
    if [[ "$dsn" =~ ^https://([^@/]+)@([^/]+)/([0-9]+)$ ]]; then
        checkin_url="https://${BASH_REMATCH[2]}/api/${BASH_REMATCH[3]}/cron/forge-backup/${BASH_REMATCH[1]}/"
    else
        echo "backup: no SENTRY_DSN -- nobody is told if this stops working." >&2
    fi
fi
checkin() {
    [ -n "$checkin_url" ] || return 0
    # The monitor is created on the first check-in, with the timer's schedule.
    curl -fsS -m 10 -o /dev/null -X POST "$checkin_url" -H "Content-Type: application/json" \
        --data "{\"status\":\"$1\",\"monitor_config\":{\"schedule\":{\"type\":\"crontab\",\"value\":\"30 3 * * *\"},\"checkin_margin\":120,\"max_runtime\":60,\"timezone\":\"UTC\"}}" ||
        echo "backup: could not reach Sentry for the '$1' check-in" >&2
}
# Any failure from here on, `die` included, is reported as the script exits.
# (archive() runs as a pipeline stage, a subshell, so its own EXIT trap does
# not replace this one.)
trap 'rc=$?; [ "$rc" = 0 ] || checkin error' EXIT

[ -n "${BACKUP_AGE_RECIPIENT:-}" ] ||
    die "BACKUP_AGE_RECIPIENT is not set. Refusing to write an unencrypted backup.
     Generate a keypair off this host with 'age-keygen -o key.txt', keep
     key.txt somewhere this box cannot reach, and set this to the public
     key it prints."

command -v age >/dev/null 2>&1 ||
    die "age is not installed (https://github.com/FiloSottile/age).
     Refusing to write an unencrypted backup."

if [ -n "${BACKUP_REMOTE:-}" ]; then
    command -v rclone >/dev/null 2>&1 ||
        die "BACKUP_REMOTE is set but rclone is not installed."
fi

# 0600 on everything this script creates, including the directory. The
# audit found 0664 -- world-readable backups of every row in the product.
umask 077
mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

# archive NAME MIN_BYTES: encrypts stdin to $BACKUP_DIR/NAME.age, copies it
# off-host, prunes old local copies of the same kind.
archive() {
    local name="$1" min="$2" target partial size
    target="$BACKUP_DIR/$name.age"
    # Written to a .partial first and renamed only on success, so a run
    # killed half way through (the box rebooting, the disk filling) never
    # leaves a truncated file that looks like a backup to the prune below.
    partial="$target.partial"
    trap 'rm -f "$partial"' EXIT
    age -r "$BACKUP_AGE_RECIPIENT" -o "$partial"
    # A dump that fails mid-stream still produces bytes, and `set -o
    # pipefail` catches the exit code -- but an empty or absurdly small file
    # is the other failure mode (a dump of a database that is not there yet).
    size="$(wc -c <"$partial")"
    [ "$size" -gt "$min" ] || die "$name is only $size bytes -- refusing to keep it"
    mv "$partial" "$target"
    trap - EXIT
    echo "backup: wrote $target ($size bytes)"

    if [ -n "${BACKUP_REMOTE:-}" ]; then
        rclone copy --retries 3 "$target" "$BACKUP_REMOTE"
        echo "backup: copied to $BACKUP_REMOTE"
    else
        echo "backup: BACKUP_REMOTE unset -- local copy only." >&2
        echo "backup: a backup on the same disk as the database is not a backup." >&2
    fi

    # Prune last, and only after a successful run: an expiry that fires when
    # today's backup failed deletes the good ones and keeps nothing.
    local kind="${name%%-*}" deleted
    deleted="$(find "$BACKUP_DIR" -name "$kind-*.age" -type f \
        -mtime "+$BACKUP_RETENTION_DAYS" -print -delete | wc -l)"
    echo "backup: pruned $deleted $kind backup(s) older than $BACKUP_RETENTION_DAYS days"
}

echo "backup: database"
# -T: no TTY, this is a pipe. `pg_dump` runs inside the container so the
# host needs no postgres client, and connects over the container's own
# socket so it bypasses pgbouncer -- transaction pooling and a long-running
# dump do not mix (docs/adr/0002-pgbouncer.md). </dev/null: compose exec
# forwards stdin, and a caller's stdin may be a script still being read.
docker compose exec -T postgres \
    pg_dump -Fc -U "${POSTGRES_USER:-postgres}" "${POSTGRES_DB:-forge}" </dev/null |
    archive "forge-$stamp.dump" 1024

if [ "$WHAT" = all ]; then
    echo "backup: files"
    # Everything but the two prefixes that expire within a day anyway:
    # unconfirmed uploads and regenerable GDPR exports. MC_HOST_* is mc's
    # credential-by-environment form, so nothing is written to a config file.
    docker compose exec -T minio sh -c '
        rm -rf /tmp/files-backup && mkdir /tmp/files-backup &&
        MC_HOST_src="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@127.0.0.1:9000" \
          mc mirror --quiet --exclude "incoming/*" --exclude "data-exports/*" \
          "src/$MINIO_BUCKET" /tmp/files-backup >/dev/null' </dev/null
    # An empty bucket is a valid state, so no size floor beyond the tar
    # header itself.
    docker cp "$(docker compose ps -q minio):/tmp/files-backup" - | archive "files-$stamp.tar" 512
    docker compose exec -T minio rm -rf /tmp/files-backup </dev/null

    checkin ok
fi
