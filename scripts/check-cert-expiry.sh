#!/usr/bin/env bash
# How long the internal TLS material has left.
#
# This is the failure mode that actually happens. Rotation is a procedure
# somebody runs on purpose; expiry is a date nobody is watching. When these
# certificates lapse, pgbouncer stops accepting the app, the app stops
# accepting Redis, and the stack goes down for a reason that looks nothing
# like a certificate problem from the outside -- the public Caddy certificate
# is ACME-renewed and perfectly healthy, so the obvious suspect is innocent.
#
# Exit codes are chosen for cron: 0 fine, 1 expiring soon or already expired,
# 2 cannot tell. Run it daily and let the non-zero exit mail you:
#
#   0 6 * * *  cd /srv/forge && ./scripts/check-cert-expiry.sh >/dev/null
#
# Checks server.crt and every certificate in the ca.crt bundle, because during
# a rotation the bundle legitimately holds two and the *older* one expiring is
# harmless while the newer one expiring is not.
set -uo pipefail

CERT_DIR="${CERT_DIR:-./certs}"
WARN_DAYS="${WARN_DAYS:-30}"

if [ ! -f "$CERT_DIR/server.crt" ]; then
  echo "check-cert-expiry: no $CERT_DIR/server.crt (TLS overlay not in use?)" >&2
  exit 2
fi

now=$(date +%s)
status=0

report() {
  local label="$1" not_after="$2"
  local end
  # BSD date needs a format string; GNU date does not. Try GNU first.
  end=$(date -d "$not_after" +%s 2>/dev/null) \
    || end=$(date -j -f "%b %d %T %Y %Z" "$not_after" +%s 2>/dev/null) \
    || { echo "check-cert-expiry: cannot parse '$not_after'" >&2; status=2; return; }

  local days=$(( (end - now) / 86400 ))
  if [ "$days" -lt 0 ]; then
    printf 'EXPIRED   %-28s %s (%d days ago)\n' "$label" "$not_after" "$(( -days ))"
    status=1
  elif [ "$days" -lt "$WARN_DAYS" ]; then
    printf 'EXPIRING  %-28s %s (%d days left)\n' "$label" "$not_after" "$days"
    status=1
  else
    printf 'ok        %-28s %s (%d days left)\n' "$label" "$not_after" "$days"
  fi
}

report "server.crt (presented)" \
  "$(openssl x509 -in "$CERT_DIR/server.crt" -noout -enddate | cut -d= -f2)"

if [ -f "$CERT_DIR/ca.crt" ]; then
  # A bundle may hold more than one certificate mid-rotation; split and check
  # each. `openssl x509` alone would silently report only the first.
  i=0
  while IFS= read -r line; do
    printf '%s\n' "$line"
  done < "$CERT_DIR/ca.crt" | awk '
    /BEGIN CERTIFICATE/ { n++ }
    { if (n > 0) print > ("'"$CERT_DIR"'/.ca-split-" n ".pem") }
  '
  for pem in "$CERT_DIR"/.ca-split-*.pem; do
    [ -e "$pem" ] || continue
    i=$((i + 1))
    report "ca.crt entry $i (trusted)" \
      "$(openssl x509 -in "$pem" -noout -enddate | cut -d= -f2)"
    rm -f "$pem"
  done
fi

if [ "$status" -eq 1 ]; then
  echo
  echo "Rotate with: make rotate-internal-certs"
fi
exit "$status"
