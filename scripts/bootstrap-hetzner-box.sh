#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu server for .github/workflows/deploy-hetzner.yml.
# The procedure around it is docs/runbooks/new-server.md.
#
# Run as root ON THE NEW SERVER, from a copy of this file:
#
#   scp scripts/bootstrap-hetzner-box.sh root@<ip>:
#   ssh root@<ip> bash bootstrap-hetzner-box.sh git@github.com:<owner>/<repo>.git
#
# Safe to run again: every step checks first. The first run stops after
# printing a read-only deploy key for the repository; add it on GitHub and run
# the script again.
#
# What the deploy workflow does not do, because it only updates a server that
# already runs: harden SSH, install Docker, add swap and the `forge` user,
# check out the repo, generate the infrastructure passwords and internal TLS
# certificates, and start the stateful services (Postgres, pgbouncer, Redis,
# MinIO, ClamAV, the exporters). Written down from the first deploy to a fresh
# box on 2026-10-26, which needed every one of these by hand.
set -euo pipefail
# The single-quoted blocks are expanded by the deploy user's shell, on purpose.
# shellcheck disable=SC2016

REPO="${1:?usage: bootstrap-hetzner-box.sh <git ssh url of the repository>}"
DEPLOY_USER=forge
HOME_DIR=/home/$DEPLOY_USER
APP_DIR=$HOME_DIR/forge

[ "$(id -u)" = 0 ] || { echo "Run as root." >&2; exit 1; }
as_user() { runuser -u "$DEPLOY_USER" -- bash -euo pipefail -c "$1"; }

echo "== SSH: keys only"
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
  > /etc/ssh/sshd_config.d/00-forge-hardening.conf
sshd -t
systemctl reload ssh

echo "== Docker, from Docker's own repository"
if ! command -v docker >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck source=/dev/null
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
fi

echo "== 2 GB swap"
# ClamAV alone holds ~1.5 GB of signatures in memory; on a 4 GB box the
# swap is what keeps a signature reload from waking the OOM killer.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== The $DEPLOY_USER user (docker group, no sudo)"
id "$DEPLOY_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker "$DEPLOY_USER"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$HOME_DIR/.ssh"
if [ ! -f "$HOME_DIR/.ssh/authorized_keys" ]; then
  install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /root/.ssh/authorized_keys "$HOME_DIR/.ssh/authorized_keys"
fi

echo "== Read access to the repository"
as_user '
  [ -f ~/.ssh/github_deploy ] || ssh-keygen -q -t ed25519 -N "" -C "$(hostname) read-only" -f ~/.ssh/github_deploy
  # GitHub publishes its host keys; pinning them beats trusting the first answer.
  grep -q "^github.com " ~/.ssh/known_hosts 2>/dev/null ||
    curl -fsSL https://api.github.com/meta |
      python3 -c "import json,sys; print(\"\\n\".join(\"github.com \" + k for k in json.load(sys.stdin)[\"ssh_keys\"]))" \
      >> ~/.ssh/known_hosts
  grep -q "^Host github.com" ~/.ssh/config 2>/dev/null ||
    printf "Host github.com\n  IdentityFile ~/.ssh/github_deploy\n  IdentitiesOnly yes\n" >> ~/.ssh/config
  chmod 600 ~/.ssh/config
'
if ! as_user "git ls-remote '$REPO' >/dev/null 2>&1"; then
  echo
  echo "Add this key on GitHub: repository Settings -> Deploy keys -> Add deploy key,"
  echo "with 'Allow write access' left unticked. Then run this script again."
  echo
  cat "$HOME_DIR/.ssh/github_deploy.pub"
  exit 2
fi
[ -d "$APP_DIR/.git" ] || as_user "git clone -q '$REPO' '$APP_DIR'"

echo "== Infrastructure passwords (generated once, never overwritten)"
as_user '
  cd ~/forge
  umask 077
  if [ ! -f .env.postgres ]; then
    pg=$(openssl rand -hex 24)
    minio=$(openssl rand -hex 24)
    kms="forge-prod:$(openssl rand -base64 32)"
    sed "s|change-me|$pg|" .env.postgres.example > .env.postgres
    sed "s|change-me|$pg|g" .env.pgbouncer.example > .env.pgbouncer
    sed "s|postgres:postgres@|postgres:$pg@|" .env.postgres-exporter.example > .env.postgres-exporter
    sed -e "s|^MINIO_ROOT_PASSWORD=.*|MINIO_ROOT_PASSWORD=$minio|" \
        -e "s|^MINIO_KMS_SECRET_KEY=.*|MINIO_KMS_SECRET_KEY=$kms|" \
        .env.minio.example > .env.minio
    user=$(sed -n "s/^MINIO_ROOT_USER=//p" .env.minio)
    bucket=$(sed -n "s/^MINIO_BUCKET=//p" .env.minio)
    # What the app needs to reach these services. Copy each line into the
    # Bitwarden project, then delete this file.
    cat > bitwarden-values.txt <<EOF
DATABASE_URL=postgresql+asyncpg://postgres:$pg@pgbouncer:6432/forge?sslmode=require
MIGRATIONS_DATABASE_URL=postgresql+asyncpg://postgres:$pg@postgres:5432/forge?sslmode=require
PGBOUNCER_ENABLED=true
REDIS_URL=rediss://redis:6379/0?ssl_ca_certs=/certs/ca.crt
STORAGE_BACKEND=minio
AWS_ACCESS_KEY_ID=$user
AWS_SECRET_ACCESS_KEY=$minio
AWS_BUCKET_NAME=$bucket
VIRUS_SCANNER=clamav
CLAMAV_HOST=clamav
CLAMAV_PORT=3310

# Not for the Bitwarden deploy project: back this up somewhere off the box.
# It decrypts every uploaded file, the way ENCRYPTION_KEY decrypts the columns.
MINIO_KMS_SECRET_KEY=$kms
EOF
  fi
'

echo "== Internal TLS certificates"
as_user 'cd ~/forge && [ -f certs/server.crt ] || bash scripts/gen-internal-certs.sh ./certs >/dev/null'

echo "== Stateful services"
# DOMAIN only satisfies compose's interpolation of the caddy service, which is
# not started here; the deploy brings caddy and the app up with the real env.
as_user '
  cd ~/forge
  DOMAIN=bootstrap docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml --profile clamav \
    up -d --quiet-pull postgres pgbouncer redis minio minio-init clamav postgres-exporter node-exporter
'

echo
echo "Done. Next, from docs/runbooks/new-server.md:"
[ -f "$APP_DIR/bitwarden-values.txt" ] &&
  echo "  - copy $APP_DIR/bitwarden-values.txt into Bitwarden, then delete it"
echo "  - add the CI deploy key and the GitHub environment secrets, then deploy"
