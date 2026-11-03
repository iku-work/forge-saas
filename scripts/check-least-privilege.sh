#!/usr/bin/env bash
# Proves a running stack uses its limited accounts: what the application can do,
# and what it can no longer do. Run from the checkout, with COMPOSE_FILE set as
# for backup.sh, after the schema exists. Prints no secrets.
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

failures=0
report() { echo "$1: $2"; [ "$1" = ok ] || failures=$((failures + 1)); }

# As the application connects: through pgbouncer, with its account.
sql() {
  docker compose exec -T pgbouncer sh -c \
    'psql -h 127.0.0.1 -p 6432 -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -tAc "$1"' sh "$1" </dev/null
}
expect() {
  if [ "$(sql "$2" 2>/dev/null)" = "$3" ]; then report ok "$1"; else report FAILED "$1"; fi
}
refused() {
  if sql "$2" >/dev/null 2>&1; then report FAILED "$1"; else report ok "$1"; fi
}

expect "the app connects as forge_app" "SELECT current_user" forge_app
expect "forge_app has no admin attributes" \
  "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication FROM pg_roles WHERE rolname = current_user" f
expect "forge_app can write rows" "SELECT has_table_privilege(current_user, 'public.\"user\"', 'INSERT')" t
refused "forge_app cannot create tables" "CREATE TABLE least_privilege_probe (id int)"
refused "forge_app cannot run programs" "COPY (SELECT 1) TO PROGRAM 'true'"

if docker compose exec -T backend sh -c '[ -z "${MIGRATIONS_DATABASE_URL:-}" ]' </dev/null; then
  report ok "the superuser's password is not in the app's environment"
else
  report FAILED "the superuser's password is not in the app's environment"
fi

# A ping with no password, over TLS when the overlay is on.
noauth="$(docker compose exec -T redis sh -c \
  'tls=; [ ! -f /tmp/ca.crt ] || tls="--tls --cacert /tmp/ca.crt --cert /tmp/server.crt --key /tmp/server.key"; redis-cli $tls ping' </dev/null 2>&1 || true)"
case "$noauth" in
  *NOAUTH*) report ok "Redis refuses a client with no password" ;;
  *) report FAILED "Redis refuses a client with no password" ;;
esac

if docker compose exec -T backend python - <scripts/check-storage-access.py; then
  report ok "the storage account does what the app needs, and no more"
else
  report FAILED "the storage account does what the app needs, and no more"
fi

[ "$failures" -eq 0 ] || { echo "$failures check(s) failed" >&2; exit 1; }
echo "all checks passed"
