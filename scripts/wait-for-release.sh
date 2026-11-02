#!/usr/bin/env bash
# Runs on the box, fed over ssh from the deploy's own checkout, so a rollback
# to a release older than this script can still use it. Waits until every
# service runs the given images and is healthy, and this box's own edge serves
# the site with a valid certificate.
#
# `up -d` returns before healthchecks pass, and a worker that starts and then
# crash-loops leaves /health/ready green (OCT-05). The edge check is separate
# from the deploy's smoke test because that one follows DNS: on 2026-11-02 it
# turned out to have been passing against a different server for days.
#
# Required env: BACKEND_IMAGE, CADDY_IMAGE, PROD_DOMAIN, COMPOSE.
set -euo pipefail

check() {
  local svc=$1 want=$2 id image state
  id=$(docker compose $COMPOSE ps -q "$svc")
  [ -n "$id" ] || { echo "$svc: not running"; return 1; }
  image=$(docker inspect --format '{{.Config.Image}}' "$id")
  state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")
  [ "$image" = "$want" ] || { echo "$svc: runs $image"; return 1; }
  case "$state" in healthy|running) return 0 ;; esac
  echo "$svc: $state"; return 1
}

edge() {
  curl -sf --max-time 5 --resolve "$PROD_DOMAIN:443:127.0.0.1" "https://$PROD_DOMAIN/health/ready" >/dev/null && return 0
  echo "this box's edge does not serve $PROD_DOMAIN: DNS not pointing here yet, or no certificate"
  return 1
}

for _ in $(seq 1 30); do
  if check backend "$BACKEND_IMAGE" && check worker "$BACKEND_IMAGE" \
     && check scanner-worker "$BACKEND_IMAGE" && check caddy "$CADDY_IMAGE" && edge; then
    echo "every service healthy on the release"; exit 0
  fi
  sleep 5
done
exit 1
