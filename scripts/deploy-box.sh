#!/usr/bin/env bash
# Everything a deploy does on the box, and the only thing CI's SSH key can run
# there. scripts/lock-deploy-key.sh puts the key in authorized_keys as
#
#   restrict,command="/usr/bin/bash /home/forge/forge/scripts/deploy-box.sh" ssh-ed25519 ...
#
# so whatever command the client asks for arrives here as $SSH_ORIGINAL_COMMAND,
# and only the subcommands below exist. The `forge` user is in the `docker`
# group, which is root on this box: before this, the key could run anything
# (review 2026-11-02, Minor). A person with their own key can run it by hand:
#
#   bash scripts/deploy-box.sh <subcommand> [args]
#
#   current               the running release's commit, or nothing
#   env                   stdin: the app's settings, kept as backend/.env.next
#   migrate <sha> <user>  stdin: a registry token. Check out <sha>, swap in the
#                         new env, pull, update the data services, back up, migrate
#   switch <sha>          start the release's edge and app containers
#   verify <sha>          wait until every service runs <sha> and is healthy
#   rollback <sha>        put back the previous env, checkout and containers
#   prune                 remove images older than a week
#
# What the key still decides: which commit runs, and the app's settings. A
# commit must be on origin/main, so it has passed review and CI, and the image
# names come from this checkout's remote, never from the caller. The settings
# can break the site, but they reach containers as data and compose only, and
# names compose would act on (COMPOSE_*, DOCKER_*) are refused.
# shellcheck disable=SC2086 # $COMPOSE and $DATA_SERVICES are word lists, on purpose
# shellcheck disable=SC1090 # ~/forge-backup.env is the box's, not this repository's
set -euo pipefail

COMPOSE="--env-file backend/.env -f docker-compose.prod.yml -f docker-compose.tls.yml -f docker-compose.registry.yml"
DATA_SERVICES="postgres pgbouncer redis minio clamav postgres-exporter node-exporter"
ENV_LIMIT=262144

die() { echo "deploy-box: $*" >&2; exit 1; }

usage() {
  die "usage: current | env | migrate <sha> <user> | switch <sha> | verify <sha> | rollback <sha> | prune"
}

# A full commit SHA, which is all CI ever sends. Anything shorter could name
# more than one commit, and anything else is not a commit at all.
sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]] || die "not a full commit SHA: $1"
}

# On main, and new enough to carry this script, or the next call through the
# locked key would find nothing to run.
on_main() {
  # --prune: a deleted branch "x" left behind blocks fetching a new "x/y".
  git fetch --quiet --prune origin
  git merge-base --is-ancestor "$1" origin/main 2>/dev/null || die "$1 is not a commit on main"
  git cat-file -e "$1:scripts/deploy-box.sh" 2>/dev/null ||
    die "$1 is older than scripts/deploy-box.sh, so this key cannot deploy it"
}

# One setting from an env file, without the quotes pasting may have added.
setting() {
  sed -n "s/^$1=//p" "$2" | tail -n 1 | tr -d "\"'"
}

# The images for a release: this checkout's own GitHub repository, never one
# the caller names. The edge image differs per environment, because its
# bundles have the public URLs built in.
images() {
  local url path environment suffix=
  url=$(git config --get remote.origin.url)
  path=${url#git@github.com:}
  path=${path%.git}
  [[ "$path" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "unexpected origin: $url"
  environment=$(setting ENVIRONMENT "$2")
  case "$environment" in
    prod) ;;
    staging) suffix=-staging ;;
    *) die "ENVIRONMENT in $2 must be prod or staging, not '$environment'" ;;
  esac
  export BACKEND_IMAGE="ghcr.io/${path,,}:$1"
  export CADDY_IMAGE="ghcr.io/${path,,}-caddy:$1$suffix"
}

# The checkout is what `migrate` left, so a switch never starts a release
# with another release's compose files.
at() {
  [ "$(git rev-parse HEAD)" = "$1" ] || die "the checkout is not at $1. Run migrate first."
}

current() {
  local id ref
  # The registry overlay refuses to parse without image refs; `ps` finds the
  # container by service, so placeholders do.
  id=$(BACKEND_IMAGE=unset CADDY_IMAGE=unset docker compose $COMPOSE ps -q backend 2>/dev/null) || true
  [ -n "$id" ] || return 0
  ref=$(docker inspect --format '{{.Config.Image}}' "$id")
  echo "${ref##*:}"
}

receive_env() {
  local next=backend/.env.next
  umask 077
  head -c "$ENV_LIMIT" > "$next.part"
  [ "$(wc -c < "$next.part")" -lt "$ENV_LIMIT" ] || { rm -f "$next.part"; die "the env is $ENV_LIMIT bytes or more"; }
  if grep -qE '^[[:space:]]*(export[[:space:]]+)?(COMPOSE|DOCKER)_' "$next.part"; then
    rm -f "$next.part"
    die "the env sets a COMPOSE_ or DOCKER_ name, which compose would act on"
  fi
  [[ "$(setting DOMAIN "$next.part")" =~ ^[A-Za-z0-9.-]+$ ]] || { rm -f "$next.part"; die "the env has no DOMAIN"; }
  case "$(setting ENVIRONMENT "$next.part")" in
    prod|staging) ;;
    *) rm -f "$next.part"; die "the env's ENVIRONMENT must be prod or staging" ;;
  esac
  mv "$next.part" "$next"
}

migrate() {
  local tag=$1 user=$2 token schema pg_password
  IFS= read -r token || die "expected the registry token on stdin"
  on_main "$tag"
  git checkout --quiet --detach "$tag"
  if [ -f backend/.env.next ]; then
    # A first deploy has no env to keep.
    if [ -f backend/.env ]; then cp -p backend/.env backend/.env.prev; fi
    mv backend/.env.next backend/.env
  fi
  [ -f backend/.env ] || die "no env on the box: send one with \`env\` first"
  images "$tag" backend/.env

  printf '%s\n' "$token" | docker login ghcr.io -u "$user" --password-stdin >/dev/null
  trap 'docker logout ghcr.io >/dev/null' EXIT
  docker compose $COMPOSE pull --quiet backend worker scanner-worker caddy </dev/null
  docker logout ghcr.io >/dev/null
  trap - EXIT

  # The data services, to what this checkout says they are, before anything
  # talks to them. Starts nothing that is already right, and the app's
  # containers cannot reach them on a network they have left.
  docker compose $COMPOSE up -d --wait --wait-timeout 300 --quiet-pull $DATA_SERVICES </dev/null
  # The bucket's policies and lifecycle rules, which change with the checkout.
  # `run`, not `up`: compose does not start a one-shot container again once it
  # has exited. scripts/minio-init.sh is safe to run every time.
  docker compose $COMPOSE run --rm --no-deps minio-init </dev/null

  # Backups leave the box before any data changes (ADR-0009): every product
  # needs an off-host copy, and a high-sensitivity one needs a key that cannot
  # list or delete it (scripts/check-backup-remote.sh).
  if ! grep -qs '^BACKUP_REMOTE=' ~/forge-backup.env; then
    die "No off-box backup on this box (scripts/bootstrap-hetzner-box.sh, then scripts/set-backup-remote.sh). Refusing to migrate without one."
  fi
  if grep -qE '^DATA_SENSITIVITY=["]?high["]?$' backend/.env; then
    (set -a; . ~/forge-backup.env; set +a; bash scripts/check-backup-remote.sh </dev/null)
  fi

  # A restore point before the schema changes: the newest archive in
  # /var/backups/forge is the state this migration started from
  # (docs/runbooks/backup-restore.md). A first deploy has no schema yet, so
  # nothing to keep. </dev/null on every exec: compose forwards stdin into the
  # container, and this script's stdin is the SSH channel.
  schema=$(docker compose $COMPOSE exec -T postgres \
    psql -U postgres -d forge -tAc "select to_regclass('public.alembic_version')" </dev/null)
  if [ "$schema" = alembic_version ]; then
    (set -a; . ~/forge-backup.env; set +a; bash scripts/backup.sh db </dev/null)
  fi

  # The superuser's password, for this one container: -e with no value takes it
  # from this shell's environment, so it is never in an argv, and the running
  # app never has it.
  #
  # `python -m app.core.migrations`, never alembic's own `upgrade head`: this schema
  # has branched module heads and `head` (singular) cannot run.
  pg_password=$(sed -n 's/^POSTGRES_PASSWORD=//p' .env.postgres)
  export MIGRATIONS_DATABASE_URL="postgresql+asyncpg://postgres:$pg_password@postgres:5432/forge?sslmode=require"
  docker compose $COMPOSE run --rm --no-deps -e MIGRATIONS_DATABASE_URL backend python -m app.core.migrations </dev/null
}

# Caddy in a call of its own, first, then the app with --no-deps. Without
# --no-deps, compose stops caddy and waits for its dependency chain, and the
# backend refuses to start while caddy is down: caddy carries the network alias
# for STORAGE_DOMAIN, and the storage guard (correctly) refuses to reach object
# storage over the public internet. That deadlock took production down on
# 2026-09-14. In one call, compose still orders caddy after a healthy backend,
# which on a fresh box never happens (the first deploy to forge-prod-2,
# 2026-10-26).
start_release() {
  docker compose $COMPOSE up -d --no-deps caddy </dev/null
  docker compose $COMPOSE up -d --no-deps backend worker scanner-worker </dev/null
}

switch() {
  at "$1"
  images "$1" backend/.env
  start_release
}

# scripts/wait-for-release.sh says what it checks, and why the deploy's smoke
# test is not enough.
verify() {
  local domain
  at "$1"
  images "$1" backend/.env
  domain=$(setting DOMAIN backend/.env)
  [[ "$domain" =~ ^[A-Za-z0-9.-]+$ ]] || die "no DOMAIN in backend/.env"
  PROD_DOMAIN=$domain COMPOSE=$COMPOSE bash scripts/wait-for-release.sh </dev/null
}

# Only the images, env and checkout move backwards. The database does not: a
# migration that has already applied stays applied, which is why they must be
# written to work against the previous release too.
rollback() {
  on_main "$1"
  if [ -f backend/.env.prev ]; then mv backend/.env.prev backend/.env; fi
  rm -f backend/.env.next
  git checkout --quiet --detach "$1"
  images "$1" backend/.env
  # No --wait: getting the app back must not depend on these. verify says
  # whether it worked.
  docker compose $COMPOSE up -d --quiet-pull $DATA_SERVICES </dev/null
  start_release
  # `up -d` returns before anything is healthy: the 2026-11-02 drill printed
  # "rolled back" 12 s before the site came back. Say which it was, because a
  # rollback that failed needs a person (deploy-pipeline.md, Escalation).
  if verify "$1"; then
    echo "rolled back to $1, healthy" >&2
  else
    die "ROLLBACK FAILED: $1 did not come back healthy. The site needs a person."
  fi
}

# Nothing else removes old releases, and a full disk is what took production
# down on 2026-09-13. Images in use are never pruned, so the running release
# stays. A week of others stays for rolling back by hand.
prune() {
  docker image prune -af --filter until=168h | tail -n 1
}

# Everything runs from inside main(), which bash reads in full before it starts:
# `migrate` and `rollback` check out another commit, which replaces this file.
main() {
  if [ -n "${SSH_ORIGINAL_COMMAND+set}" ]; then
    set -f
    # shellcheck disable=SC2086 # split into words; globbing is off
    set -- $SSH_ORIGINAL_COMMAND
    set +f
  fi
  local command=${1:-}
  [ $# -gt 0 ] && shift
  cd ~/forge
  case "$command:$#" in
    current:0) current ;;
    env:0) receive_env ;;
    migrate:2) sha "$1"; [[ "$2" =~ ^[A-Za-z0-9-]+(\[bot\])?$ ]] || die "unexpected user: $2"; migrate "$1" "$2" ;;
    switch:1) sha "$1"; switch "$1" ;;
    verify:1) sha "$1"; verify "$1" ;;
    rollback:1) sha "$1"; rollback "$1" ;;
    prune:0) prune ;;
    *) usage ;;
  esac
  exit
}

main "$@"
