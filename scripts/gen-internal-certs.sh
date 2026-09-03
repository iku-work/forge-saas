#!/usr/bin/env bash
# Certificates for the container-to-container hops in docker-compose.prod.yml,
# used together with docker-compose.tls.yml.
#
# These are NOT the public-facing certificate. Caddy obtains that from Let's
# Encrypt automatically and nothing here touches it. What this covers is the
# traffic *behind* Caddy -- app to pgbouncer, pgbouncer to Postgres, app to
# Redis -- which `Settings._enforce_startup_invariants` requires to be
# encrypted outside dev (sslmode=require on every Postgres DSN, rediss:// on
# Redis). Without these the prod stack refuses to boot, by design.
#
# Self-signed is the right shape here. This is not protecting against an
# untrusted network path the way the public hop is; it closes the "cleartext
# on a shared Docker bridge" gap, on a host you control. The certificate is
# its own CA, which is what lets pgbouncer's `server_tls_sslmode=verify-ca`
# verify Postgres against it.
#
# There is no automatic renewal. Note the expiry somewhere you will see it,
# and re-run this before it lapses -- see docs/runbooks/backup-restore.md.
set -euo pipefail

OUT_DIR="${1:-./certs}"
DAYS="${DAYS:-825}"

mkdir -p "$OUT_DIR"

# A config file rather than `openssl req -subj`: under Git Bash on Windows,
# MSYS path conversion rewrites a leading "/CN=..." into a filesystem path and
# the subject silently comes out empty.
cat > "$OUT_DIR/openssl.cnf" <<'EOF'
[req]
distinguished_name = dn
x509_extensions = v3
prompt = no

[dn]
CN = forge-internal-ca

[v3]
# One certificate serves all three hops, so every service name the stack
# resolves has to be present. These are compose service names on forge-net,
# not public hostnames.
subjectAltName = DNS:postgres,DNS:pgbouncer,DNS:redis,DNS:localhost,IP:127.0.0.1
basicConstraints = critical,CA:TRUE
keyUsage = critical,digitalSignature,keyEncipherment,keyCertSign
EOF

openssl req -new -x509 -nodes \
  -days "$DAYS" \
  -newkey rsa:2048 \
  -keyout "$OUT_DIR/server.key" \
  -out "$OUT_DIR/server.crt" \
  -config "$OUT_DIR/openssl.cnf" 2>/dev/null

chmod 644 "$OUT_DIR/server.crt"
chmod 600 "$OUT_DIR/server.key"

echo "Wrote $OUT_DIR/server.crt and $OUT_DIR/server.key"
openssl x509 -in "$OUT_DIR/server.crt" -noout -subject -enddate -ext subjectAltName

cat <<EOF

Next:
  1. Point the three DSNs at TLS (see backend/.env.example):
       DATABASE_URL=postgresql+asyncpg://USER:PASS@pgbouncer:6432/forge?sslmode=require
       MIGRATIONS_DATABASE_URL=postgresql+asyncpg://USER:PASS@postgres:5432/forge?sslmode=require
       REDIS_URL=rediss://redis:6379/0?ssl_ca_certs=/certs/server.crt
  2. Bring the stack up with the TLS overlay:
       docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml up -d

The overlay fixes file ownership inside each container, so $OUT_DIR only has
to be readable by the Docker daemon -- you do not need to chown it to the
container users yourself.
EOF
