#!/usr/bin/env bash
# Sends one test event from each process that reports to Sentry, starting Sentry
# the way that process does. Run from the checkout, with COMPOSE_FILE set as for
# backup.sh. An event ID only means the SDK queued it, so look for the three
# issues in Sentry too. Prints no secrets.
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

send() {
  docker compose exec -T "$1" python -c "
import sentry_sdk
import $2
event_id = sentry_sdk.capture_message('Test event from $1')
if event_id is None:
    raise SystemExit('$1: Sentry is not configured')
sentry_sdk.flush(10)
print('$1:', event_id)
" </dev/null
}

send backend app.main
send worker app.workers.main
send scanner-worker app.workers.main
