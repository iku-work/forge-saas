#!/usr/bin/env bash
#
# PROD-05, the half that matters: restoring one of backup-postgres.sh's
# archives.
#
# An untested backup is a belief, not a backup -- so this script's primary
# use is the *drill*, not the emergency. Run it against a scratch database
# on a schedule, time it, and write the number in
# docs/runbooks/backup-restore.md. The first time anyone runs a restore
# should not be the day they need one.
#
# It defaults to a scratch target for exactly that reason: restoring over
# the live database takes an explicit --into with the live name, and says
# so before it starts.
#
# Usage:
#   # the drill -- restores into forge_restore_check and reports timing
#   AGE_IDENTITY=key.txt ./scripts/restore-postgres.sh /var/backups/forge/forge-....dump.age
#
#   # the emergency -- overwrites the named database
#   AGE_IDENTITY=key.txt ./scripts/restore-postgres.sh --into forge <archive>
#
# Env:
#   AGE_IDENTITY   (required) path to the age private key. Kept off the box
#                  in normal operation -- bring it, use it, take it away.
#   COMPOSE_FILE   default docker-compose.prod.yml

set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
TARGET_DB="forge_restore_check"

die() {
    echo "restore-postgres: $*" >&2
    exit 1
}

if [ "${1:-}" = "--into" ]; then
    TARGET_DB="${2:?--into needs a database name}"
    shift 2
fi

archive="${1:?usage: restore-postgres.sh [--into DB] ARCHIVE}"
[ -f "$archive" ] || die "no such archive: $archive"
[ -n "${AGE_IDENTITY:-}" ] || die "AGE_IDENTITY is not set (path to the age private key)"
[ -f "$AGE_IDENTITY" ] || die "no such identity file: $AGE_IDENTITY"
command -v age >/dev/null 2>&1 || die "age is not installed"

live_db="${POSTGRES_DB:-forge}"
if [ "$TARGET_DB" = "$live_db" ]; then
    echo "restore-postgres: *** restoring over the LIVE database '$live_db' ***" >&2
    echo "restore-postgres: stop backend and worker first, or you will restore" >&2
    echo "restore-postgres: underneath open connections. Ctrl-C now if unsure." >&2
    sleep 10
fi

started="$(date -u +%s)"
echo "restore-postgres: restoring $archive into '$TARGET_DB'"

psql_run() {
    docker compose -f "$COMPOSE_FILE" exec -T postgres \
        psql -U "${POSTGRES_USER:-postgres}" -v ON_ERROR_STOP=1 "$@"
}

# Dropped and recreated rather than restored into: pg_restore into a
# database that already has the schema produces a wall of "already exists"
# noise and a half-merged result, which is the worst possible outcome to
# discover during an incident.
psql_run -d postgres -c "DROP DATABASE IF EXISTS \"$TARGET_DB\";"
psql_run -d postgres -c "CREATE DATABASE \"$TARGET_DB\";"

age -d -i "$AGE_IDENTITY" "$archive" |
    docker compose -f "$COMPOSE_FILE" exec -T postgres \
        pg_restore -U "${POSTGRES_USER:-postgres}" -d "$TARGET_DB" --no-owner --no-privileges

elapsed="$(($(date -u +%s) - started))"
echo "restore-postgres: restored in ${elapsed}s"

# The spot-check the runbook asks for, run here so the drill produces
# evidence rather than a shell prompt. A restore that "succeeded" into an
# empty database is the failure this catches.
echo "restore-postgres: verifying"
psql_run -d "$TARGET_DB" -c "SELECT count(*) AS users FROM \"user\";"
psql_run -d "$TARGET_DB" -c "SELECT version_num AS migration FROM alembic_version;"

cat <<EOF

restore-postgres: done in ${elapsed}s.

  Record that number in docs/runbooks/backup-restore.md -- an untimed
  restore tells you a backup is readable but not whether you can meet
  whatever recovery target you have promised.

  Encrypted columns came back as ciphertext. They are readable only with
  the ENCRYPTION_KEY that was current when this dump was taken; if it has
  rotated since, that key must still be in ENCRYPTION_KEY_OLD. See
  docs/runbooks/encryption-key-rotation.md.
EOF
