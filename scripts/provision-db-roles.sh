#!/usr/bin/env bash
# Creates the database's limited accounts and gives them what they need:
#
#   forge_app      reads and writes rows. Cannot create tables, read files or run
#                  programs, which is what a superuser could do after an SQL
#                  injection (COPY ... TO PROGRAM).
#   forge_monitor  statistics only, for postgres-exporter. Reads no user data.
#
# Safe to run again, against a live database. Takes the database to grant on, by
# default forge. Run from the checkout, with COMPOSE_FILE set as for backup.sh.
#
# Not a migration: migrations hold no passwords, and a fresh server needs these
# accounts before pgbouncer can pass its healthcheck, ahead of any migration.
#
# Run again after a restore (restore-postgres.sh does), since grants are not
# restored and a restored database starts with none.
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

DB="${1:-forge}"
app_password="$(sed -n 's/^PGPASSWORD=//p' .env.pgbouncer)"
monitor_password="$(sed -En 's#^DATA_SOURCE_NAME=[a-z]+://[^:]+:([^@]+)@.*#\1#p' .env.postgres-exporter)"

# Both go into the SQL below as literals, and $DB as an identifier.
for value in "$app_password" "$monitor_password" "$DB"; do
  [[ "$value" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "provision-db-roles: unexpected password or database name" >&2; exit 1; }
done

# Over stdin, so the passwords never reach an argv that `ps` can show.
docker compose exec -T postgres psql -U postgres -d "$DB" -q -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'forge_app') THEN
    CREATE ROLE forge_app;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'forge_monitor') THEN
    CREATE ROLE forge_monitor;
  END IF;
END
\$\$;

ALTER ROLE forge_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '$app_password';
ALTER ROLE forge_monitor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '$monitor_password';
GRANT pg_monitor TO forge_monitor;

GRANT CONNECT ON DATABASE "$DB" TO forge_app, forge_monitor;
GRANT USAGE ON SCHEMA public TO forge_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO forge_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO forge_app;

-- Migrations run as postgres, so this is what reaches tables that do not exist yet.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO forge_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO forge_app;
SQL
echo "provision-db-roles: forge_app and forge_monitor ready on $DB"
