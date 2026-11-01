#!/usr/bin/env bash
# DATA_SENSITIVITY=high (docs/adr/0009-data-sensitivity.md): proves the
# box's backup key can add an archive but can neither list the bucket nor
# delete from it, so a compromised box cannot find or destroy the backups.
# The deploy runs it before migrating, with ~/forge-backup.env loaded.
#
# It tests the property, not a provider name. A Cloudflare R2 token fails it
# (an R2 token that can write can also list and delete); a Backblaze B2
# application key with writeFiles only passes it.
set -euo pipefail

: "${BACKUP_REMOTE:?BACKUP_REMOTE is not set -- see scripts/set-backup-remote.sh}"
command -v rclone >/dev/null || { echo "check-backup-remote: rclone is not installed" >&2; exit 1; }

fail() {
    echo "check-backup-remote: $* With DATA_SENSITIVITY=high the box's backup key" >&2
    echo "check-backup-remote: must be upload-only (backup-restore.md, 'Off-host copy')." >&2
    exit 1
}

canary=".deploy-check/$(hostname)-$(date -u +%Y%m%dT%H%M%SZ)"
printf 'upload-only check by scripts/check-backup-remote.sh\n' |
    rclone rcat "$BACKUP_REMOTE/$canary" ||
    fail "The key could not upload to $BACKUP_REMOTE, so backups would fail too."
if rclone lsf --max-depth 1 "$BACKUP_REMOTE" >/dev/null 2>&1; then
    fail "The key can list $BACKUP_REMOTE."
fi
if rclone deletefile "$BACKUP_REMOTE/$canary" >/dev/null 2>&1; then
    fail "The key deleted a file from $BACKUP_REMOTE."
fi
echo "check-backup-remote: upload-only: the key can add backups, not list or delete them"
