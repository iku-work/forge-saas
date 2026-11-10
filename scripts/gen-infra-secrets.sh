#!/usr/bin/env bash
# The passwords the stateful services start with, one account per job:
#
#   .env.postgres           postgres, the superuser. Migrations and backups only.
#   .env.pgbouncer          forge_app, what the application connects as.
#   .env.postgres-exporter  forge_monitor, statistics and nothing else.
#   .env.minio              the MinIO root user, and forge-app for the application.
#   .env.redis              Redis's password.
#
# Run as the deploy user from the checkout. Called by the bootstrap; safe to run
# again, since nothing is overwritten once it is the right shape. It changes
# files only: a running service keeps what it started with until it is recreated.
#
# When it generates anything, it writes the application's new secrets to
# bitwarden-values.txt. Copy that into Bitwarden and delete it. The settings
# that go with them (PGBOUNCER_ENABLED, STORAGE_BACKEND, AWS_BUCKET_NAME as
# .env.minio's MINIO_BUCKET, ...) are committed, in deploy/common.env.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

random() { openssl rand -hex 24; }
value() { sed -n "s/^$1=//p" "$2"; }
generated=

# A box from before the application had its own accounts still has the old file.
# It is kept, so the change can be undone by hand.
replacing() {
  generated=1
  if [ -f "$1" ]; then cp "$1" "$1.prev"; fi
}

if [ ! -f .env.postgres ]; then
  replacing .env.postgres
  sed "s|change-me|$(random)|" .env.postgres.example > .env.postgres
fi

if ! grep -qs '^DB_USER=forge_app$' .env.pgbouncer; then
  replacing .env.pgbouncer
  sed "s|change-me|$(random)|g" .env.pgbouncer.example > .env.pgbouncer
fi

if ! grep -qs '://forge_monitor:' .env.postgres-exporter; then
  replacing .env.postgres-exporter
  sed "s|change-me|$(random)|" .env.postgres-exporter.example > .env.postgres-exporter
fi

if [ ! -f .env.minio ]; then
  replacing .env.minio
  sed -e "s|^MINIO_ROOT_PASSWORD=.*|MINIO_ROOT_PASSWORD=$(random)|" \
      -e "s|^MINIO_KMS_SECRET_KEY=.*|MINIO_KMS_SECRET_KEY=forge-prod:$(openssl rand -base64 32)|" \
      -e "s|^MINIO_APP_SECRET_KEY=.*|MINIO_APP_SECRET_KEY=$(random)|" \
      .env.minio.example > .env.minio
elif ! grep -q '^MINIO_APP_SECRET_KEY=' .env.minio; then
  replacing .env.minio
  grep '^MINIO_APP_ACCESS_KEY=' .env.minio.example >> .env.minio
  echo "MINIO_APP_SECRET_KEY=$(random)" >> .env.minio
fi

if [ ! -f .env.redis ]; then
  replacing .env.redis
  sed "s|change-me|$(random)|" .env.redis.example > .env.redis
fi

[ -n "$generated" ] || exit 0

cat > bitwarden-values.txt <<EOF
DATABASE_URL=postgresql+asyncpg://forge_app:$(value PGPASSWORD .env.pgbouncer)@pgbouncer:6432/forge?sslmode=require
REDIS_URL=rediss://:$(value REDIS_PASSWORD .env.redis)@redis:6379/0?ssl_ca_certs=/certs/ca.crt
AWS_ACCESS_KEY_ID=$(value MINIO_APP_ACCESS_KEY .env.minio)
AWS_SECRET_ACCESS_KEY=$(value MINIO_APP_SECRET_KEY .env.minio)

# Not for the Bitwarden deploy project: back this up somewhere off the box.
# It decrypts every uploaded file, the way ENCRYPTION_KEY decrypts the columns.
MINIO_KMS_SECRET_KEY=$(value MINIO_KMS_SECRET_KEY .env.minio)
EOF
echo "Wrote bitwarden-values.txt. Copy it into Bitwarden, then delete it."
