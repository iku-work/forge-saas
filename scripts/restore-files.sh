#!/usr/bin/env bash
#
# Restores one of backup.sh's files archives into MinIO. Like
# restore-postgres.sh, its first use is the drill: it defaults to a scratch
# bucket, <bucket>-restore-check, and restoring over the live bucket takes
# an explicit --into with its name.
#
# Usage, from your laptop (the key is streamed, never written to the box):
#   ssh forge@<box> 'cd ~/forge && set -a && . ~/forge-backup.env && set +a &&
#     AGE_IDENTITY=/dev/stdin ./scripts/restore-files.sh <archive>' <key.txt
#   ... ./scripts/restore-files.sh --into <bucket> <archive>   the emergency
#
# Env:
#   AGE_IDENTITY   (required) path to the age private key; /dev/stdin works.
#   COMPOSE_FILE   docker compose's own colon-separated variable.

set -euo pipefail
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

die() {
    echo "restore-files: $*" >&2
    exit 1
}

target=""
if [ "${1:-}" = "--into" ]; then
    target="${2:?--into needs a bucket name}"
    shift 2
fi
archive="${1:?usage: restore-files.sh [--into BUCKET] ARCHIVE}"
[ -f "$archive" ] || die "no such archive: $archive"
[ -n "${AGE_IDENTITY:-}" ] || die "AGE_IDENTITY is not set (path to the age private key)"
[ -r "$AGE_IDENTITY" ] || die "cannot read identity file: $AGE_IDENTITY"
command -v age >/dev/null 2>&1 || die "age is not installed"
# Read once, up front: when it is stdin, the compose exec calls below would
# otherwise forward it into the container first.
identity="$(cat "$AGE_IDENTITY")"
[ -n "$identity" ] || die "identity file is empty: $AGE_IDENTITY"

mc() {
    # MC_HOST_*: credentials from MinIO's own environment, no config file.
    docker compose exec -T minio sh -c \
        'MC_HOST_s="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@127.0.0.1:9000" mc "$@"' mc "$@" </dev/null
}
live="$(docker compose exec -T minio sh -c 'echo "$MINIO_BUCKET"' </dev/null)"
target="${target:-$live-restore-check}"

if [ "$target" = "$live" ]; then
    echo "restore-files: *** restoring over the LIVE bucket '$live' ***" >&2
    echo "restore-files: files in the archive overwrite their current versions;" >&2
    echo "restore-files: files that are not in it are left alone. Ctrl-C now if unsure." >&2
    sleep 10
else
    # A scratch target starts empty, so the count below means something.
    mc rb --force "s/$target" >/dev/null 2>&1 || true
fi

started="$(date -u +%s)"
echo "restore-files: restoring $archive into '$target'"
docker compose exec -T minio sh -c 'rm -rf /tmp/files-restore && mkdir /tmp/files-restore' </dev/null
age -d -i <(printf '%s\n' "$identity") "$archive" |
    docker cp - "$(docker compose ps -q minio):/tmp/files-restore"
mc mb --ignore-existing "s/$target" >/dev/null
# --enc-s3: stored encrypted at rest like every upload the app makes.
mc mirror --quiet --overwrite --enc-s3 "s/$target" /tmp/files-restore/files-backup "s/$target" >/dev/null
in_archive="$(mc ls --recursive /tmp/files-restore/files-backup | wc -l)"
in_bucket="$(mc ls --recursive "s/$target" | wc -l)"
docker compose exec -T minio rm -rf /tmp/files-restore </dev/null
elapsed="$(($(date -u +%s) - started))"

echo "restore-files: $in_archive file(s) in the archive, $in_bucket in '$target', ${elapsed}s"
[ "$in_bucket" -ge "$in_archive" ] || die "the bucket holds fewer files than the archive"
[ "$target" = "$live" ] ||
    echo "restore-files: inspect it, then remove it: mc rb --force s/$target (inside the minio container)"
