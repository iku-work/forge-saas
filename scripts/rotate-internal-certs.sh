#!/usr/bin/env bash
# Rotate the internal TLS material (scripts/gen-internal-certs.sh) without a
# window in which verification fails.
#
# The naive rotation -- regenerate, restart everything -- has a real hole in
# it. Two hops here *verify* rather than merely encrypt: pgbouncer checks
# Postgres against a CA (`server_tls_sslmode=verify-ca`), and the app and
# worker check Redis against one (`?ssl_ca_certs=`). Replace a server's
# certificate before its verifier trusts the new issuer and every connection
# across that hop fails closed until the verifier catches up. On a single host
# that is a few seconds of hard errors; under any ordering mishap it is longer.
#
# So trust moves first and shrinks last:
#
#   1. TRUST BOTH   ca.crt = old + new, restart the verifiers.
#   2. SERVE NEW    server.crt/key = new, restart the servers.
#   3. DROP OLD     ca.crt = new, restart the verifiers.
#
# At no point is a certificate presented that some verifier does not already
# accept. Connections still drop as containers restart -- this removes the
# failure window, not the restarts.
#
# Who is what, in this stack:
#   verifiers : pgbouncer (of Postgres), backend, worker, scanner-worker (of Redis)
#   servers   : postgres, redis, pgbouncer (its client-facing certificate)
#   neither   : the app's hop to pgbouncer is `sslmode=require`, which
#               encrypts without checking who answered, so nothing there
#               needs to trust anything.
set -euo pipefail

CERT_DIR="${CERT_DIR:-./certs}"
COMPOSE="docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml"
VERIFIERS="pgbouncer backend worker scanner-worker"
SERVERS="postgres redis pgbouncer"

if [ ! -f "$CERT_DIR/server.crt" ]; then
  echo "No $CERT_DIR/server.crt -- nothing to rotate. Run 'make internal-certs' first." >&2
  exit 1
fi

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

say "Current certificate"
openssl x509 -in "$CERT_DIR/server.crt" -noout -subject -enddate

say "Phase 0: issue the replacement"
# Into a staging path: nothing reads server.next.* , so this is inert until
# phase 2 promotes it.
DAYS="${DAYS:-825}" bash "$(dirname "$0")/gen-internal-certs.sh" "$CERT_DIR/.next" >/dev/null
mv "$CERT_DIR/.next/server.crt" "$CERT_DIR/server.next.crt"
mv "$CERT_DIR/.next/server.key" "$CERT_DIR/server.next.key"
rm -rf "$CERT_DIR/.next"
openssl x509 -in "$CERT_DIR/server.next.crt" -noout -subject -enddate

say "Phase 1: trust both (old + new), restart verifiers"
cat "$CERT_DIR/server.crt" "$CERT_DIR/server.next.crt" > "$CERT_DIR/ca.crt"
chmod 644 "$CERT_DIR/ca.crt"
# --force-recreate because the material is copied into each container at
# start; a plain `restart` reuses the existing container and its stale copy.
$COMPOSE up -d --force-recreate --no-deps $VERIFIERS

say "Phase 2: serve the new certificate, restart servers"
mv "$CERT_DIR/server.next.crt" "$CERT_DIR/server.crt"
mv "$CERT_DIR/server.next.key" "$CERT_DIR/server.key"
chmod 644 "$CERT_DIR/server.crt"
chmod 600 "$CERT_DIR/server.key"
$COMPOSE up -d --force-recreate --no-deps $SERVERS

say "Phase 3: drop the old issuer from the trust set, restart verifiers"
cp "$CERT_DIR/server.crt" "$CERT_DIR/ca.crt"
chmod 644 "$CERT_DIR/ca.crt"
$COMPOSE up -d --force-recreate --no-deps $VERIFIERS

say "Rotation complete"
openssl x509 -in "$CERT_DIR/server.crt" -noout -subject -enddate
echo
echo "Verify the stack agrees:"
echo "  make check-certs"
echo "  $COMPOSE exec -T backend python -c \\"
echo "    \"import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/ready').status)\""
