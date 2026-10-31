#!/usr/bin/env bash
# Points the encrypted backups at a Cloudflare R2 bucket, off the box.
# Run as the deploy user, ON THE SERVER, in a terminal (it prompts):
#
#   ssh -t forge@<ip> bash forge/scripts/set-backup-remote.sh
#
# The values are typed or pasted here and go straight into
# ~/forge-backup.env (mode 600), which the backup unit and the deploy read,
# so the token never passes through anything else. The bucket and its
# token are made first: docs/runbooks/backup-restore.md, "Off-host copy".
# Running it again replaces the previous remote.
set -euo pipefail

ENV_FILE="$HOME/forge-backup.env"
[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE: run scripts/bootstrap-hetzner-box.sh first." >&2; exit 1; }
command -v rclone >/dev/null || { echo "rclone is not installed (apt-get install rclone, as root)." >&2; exit 1; }

read -rp "Bucket name [forge-prod-backups]: " bucket
bucket="${bucket:-forge-prod-backups}"
read -rp "S3 endpoint (https://<account id>.eu.r2.cloudflarestorage.com): " endpoint
read -rp "Access Key ID: " key_id
read -rsp "Secret Access Key (not shown): " secret
echo

# Strict, because each value lands in a file both systemd and a shell read:
# nothing but the characters Cloudflare uses can get in.
[[ "$bucket" =~ ^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$ ]] || { echo "Not a bucket name: $bucket" >&2; exit 1; }
[[ "$endpoint" =~ ^https://[0-9a-f]{32}(\.eu)?\.r2\.cloudflarestorage\.com$ ]] ||
  { echo "Not an R2 endpoint: $endpoint" >&2; exit 1; }
[[ "$endpoint" == *.eu.r2.* ]] ||
  echo "Warning: not an EU-jurisdiction endpoint; backups will not be pinned to the EU." >&2
[[ "$key_id" =~ ^[0-9a-f]{32}$ ]] || { echo "The Access Key ID should be 32 hex characters." >&2; exit 1; }
[[ "$secret" =~ ^[0-9a-f]{64}$ ]] || { echo "The Secret Access Key should be 64 hex characters." >&2; exit 1; }

# Replace any earlier block, keep everything else.
tmp="$(mktemp "$ENV_FILE.XXXXXX")"
sed '/^# >>> backup remote/,/^# <<< backup remote/d' "$ENV_FILE" > "$tmp"
cat >> "$tmp" <<EOF
# >>> backup remote (scripts/set-backup-remote.sh)
BACKUP_REMOTE=r2:$bucket
RCLONE_CONFIG_R2_TYPE=s3
RCLONE_CONFIG_R2_PROVIDER=Cloudflare
RCLONE_CONFIG_R2_ENDPOINT=$endpoint
RCLONE_CONFIG_R2_ACCESS_KEY_ID=$key_id
RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=$secret
# A token scoped to one bucket may not list or create buckets.
RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
# rclone verifies an upload with HEAD ?versionId=..., which R2 answers 501
# (found 2026-10-31, rclone 1.60). The upload carries a Content-MD5 that R2
# checks, so skipping the HEAD loses no integrity check.
RCLONE_CONFIG_R2_NO_HEAD=true
# <<< backup remote
EOF
chmod 600 "$tmp"
mv "$tmp" "$ENV_FILE"

# Prove the token reaches the bucket before anything relies on it.
set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a
if rclone lsf "r2:$bucket" --max-depth 1 >/dev/null; then
  echo "Saved. The bucket answers; the next backup is copied there."
else
  echo "Saved, but the bucket did not answer: check the endpoint, bucket name and token scope." >&2
  exit 1
fi
