#!/usr/bin/env bash
# Tells Sentry about trouble on this server that no application error would: a
# filling disk, or a container that is unhealthy, restarting or has failed. Runs
# hourly from the timer scripts/bootstrap-hetzner-box.sh installs, and sends
# nothing while all is well. A server that is off cannot report itself, which is
# what the uptime monitor in docs/runbooks/new-server.md is for.
#
# `--test` sends one event regardless, to prove it arrives. Prints no secrets.
set -euo pipefail
cd "$(dirname "$0")/.."

DISK_LIMIT="${BOX_HEALTH_DISK_LIMIT:-85}"
problems=()
[ "${1:-}" = --test ] && problems+=("test event from check-box-health.sh")

used="$(df -P / | awk 'NR == 2 { gsub("%", "", $5); print $5 }')"
[ "$used" -lt "$DISK_LIMIT" ] || problems+=("disk ${used}% full")

while IFS='|' read -r name status; do
  case "$status" in
    "Exited (0)"*) ;; # minio-init, which is meant to finish
    *unhealthy* | Restarting* | Exited* | Dead*) problems+=("$name: $status") ;;
  esac
done < <(docker ps -a --format '{{.Names}}|{{.Status}}')

if [ "${#problems[@]}" -eq 0 ]; then
  echo "box health: ok"
  exit 0
fi

message="$(IFS=';'; echo "Box health: ${problems[*]}" | tr -d '"\\')"
echo "$message"

dsn="${BACKUP_SENTRY_DSN:-$(sed -n 's/^SENTRY_DSN=//p' backend/.env 2>/dev/null | tr -d "\"'")}"
if [[ ! "$dsn" =~ ^https://([^@/]+)@([^/]+)/([0-9]+)$ ]]; then
  echo "box health: no SENTRY_DSN, so nobody is told" >&2
  exit 1
fi
key="${BASH_REMATCH[1]}" host="${BASH_REMATCH[2]}" project="${BASH_REMATCH[3]}"

event_id="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
printf '{"event_id":"%s"}\n{"type":"event"}\n{"event_id":"%s","level":"error","logger":"box-health","server_name":"%s","fingerprint":["box-health"],"message":"%s"}\n' \
  "$event_id" "$event_id" "$(hostname)" "$message" |
  curl -fsS -m 10 -o /dev/null -X POST "https://$host/api/$project/envelope/" \
    -H "X-Sentry-Auth: Sentry sentry_version=7, sentry_key=$key" \
    -H "Content-Type: application/x-sentry-envelope" --data-binary @-
