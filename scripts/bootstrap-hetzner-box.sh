#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu server for .github/workflows/deploy-hetzner.yml.
# The procedure around it is docs/runbooks/new-server.md.
#
# Run as root ON THE NEW SERVER, from a copy of this file:
#
#   scp scripts/bootstrap-hetzner-box.sh root@<ip>:
#   ssh root@<ip> bash bootstrap-hetzner-box.sh git@github.com:<owner>/<repo>.git age1... [staging]
#
# The second argument is the public half of the backup key, made on your own
# machine with `age-keygen` (docs/runbooks/new-server.md). It is required: a
# box without backups is the default this script exists to rule out.
#
# `staging` as a third argument names the box's backup monitor in Sentry
# forge-backup-staging rather than forge-backup, so the two boxes' check-ins
# cannot stand in for each other, and keeps a week of local copies, not two.
#
# Safe to run again: every step checks first. The first run stops after
# printing a read-only deploy key for the repository; add it on GitHub and run
# the script again.
#
# What the deploy workflow does not do, because it only updates a server that
# already runs: harden SSH, turn on security updates, install Docker, add swap
# and the `forge` user, check out the repo, generate the infrastructure
# passwords and internal TLS certificates, start the stateful services (Postgres, pgbouncer, Redis,
# MinIO, ClamAV, the exporters) and schedule the encrypted daily backup.
# Written down from the first deploy to a fresh box on 2026-10-26, which
# needed every one of these by hand -- and got no backup at all, found by
# the restore drill on 2026-10-31.
set -euo pipefail
# The single-quoted blocks are expanded by the deploy user's shell, on purpose.
# shellcheck disable=SC2016

USAGE="usage: bootstrap-hetzner-box.sh <git ssh url of the repository> <age public key> [staging]"
REPO="${1:?$USAGE}"
BACKUP_RECIPIENT="${2:?$USAGE}"
case "${3:-}" in
  "") BACKUP_MONITOR=forge-backup RETENTION_DAYS=14 ;;
  staging) BACKUP_MONITOR=forge-backup-staging RETENTION_DAYS=7 ;;
  *) echo "$USAGE" >&2; exit 1 ;;
esac
DEPLOY_USER=forge
HOME_DIR=/home/$DEPLOY_USER
APP_DIR=$HOME_DIR/forge

[ "$(id -u)" = 0 ] || { echo "Run as root." >&2; exit 1; }
# Checked before anything changes, and strictly: it is written into a file
# both systemd and the deploy's shell read.
[[ "$BACKUP_RECIPIENT" =~ ^age1[02-9ac-hj-np-z]{58}$ ]] ||
  { echo "The second argument must be an age public key (age1...), not: $BACKUP_RECIPIENT" >&2; exit 1; }
as_user() { runuser -u "$DEPLOY_USER" -- bash -euo pipefail -c "$1"; }

echo "== SSH: keys only"
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
  > /etc/ssh/sshd_config.d/00-forge-hardening.conf
sshd -t
systemctl reload ssh

echo "== Security updates, with a reboot when one needs it"
# Kernel patches do nothing until a reboot, and without this one never comes. An
# hour after the backup starts. The time is the server's own, and Hetzner's Ubuntu
# images run on UTC. Every service has a restart policy, so the stack comes back.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq unattended-upgrades >/dev/null
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
cat > /etc/apt/apt.conf.d/52forge-reboot <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
EOF

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
as_user 'cd ~/forge && bash scripts/gen-infra-secrets.sh'

echo "== Internal TLS certificates"
as_user 'cd ~/forge && [ -f certs/server.crt ] || bash scripts/gen-internal-certs.sh ./certs >/dev/null'

echo "== Stateful services"
# DOMAIN only satisfies compose's interpolation of the caddy service, which is
# not started here; the deploy brings caddy and the app up with the real env.
#
# Postgres on its own first: pgbouncer's healthcheck logs in as forge_app, which
# has to exist by then.
as_user '
  cd ~/forge
  export DOMAIN=bootstrap COMPOSE_FILE=docker-compose.prod.yml:docker-compose.tls.yml
  docker compose up -d --wait --quiet-pull postgres
  bash scripts/provision-db-roles.sh
  docker compose --profile clamav \
    up -d --quiet-pull pgbouncer redis minio minio-init clamav postgres-exporter node-exporter
'

echo "== Encrypted daily backup (docs/runbooks/backup-restore.md)"
# The box holds only the public key: it can write backups and cannot read
# them. ~/forge-backup.env is outside the checkout so a deploy's checkout
# never touches it, and the deploy reads it too, for the backup it takes
# before every migration.
command -v age >/dev/null && command -v rclone >/dev/null ||
  { apt-get update -qq; apt-get install -y -qq age rclone >/dev/null; }
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /var/backups/forge
if [ ! -f "$HOME_DIR/forge-backup.env" ]; then
  install -m 600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /dev/null "$HOME_DIR/forge-backup.env"
  cat > "$HOME_DIR/forge-backup.env" <<EOF
BACKUP_AGE_RECIPIENT=$BACKUP_RECIPIENT
BACKUP_DIR=/var/backups/forge
BACKUP_RETENTION_DAYS=$RETENTION_DAYS
BACKUP_SENTRY_MONITOR=$BACKUP_MONITOR
# The compose set the backup needs to reach Postgres and MinIO: the deploy's,
# minus the registry overlay (it requires the image refs), with DOMAIN and
# friends interpolated from the env the deploy ships, as the deploy does.
COMPOSE_FILE=docker-compose.prod.yml:docker-compose.tls.yml
COMPOSE_ENV_FILES=backend/.env
# The off-host copy is added by scripts/set-backup-remote.sh. Until then
# every archive sits on the same disk as the database.
EOF
fi
cat > /etc/systemd/system/forge-backup.service <<EOF
[Unit]
Description=Encrypted backup of the database and uploaded files (scripts/backup.sh)

[Service]
Type=oneshot
User=$DEPLOY_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$HOME_DIR/forge-backup.env
ExecStart=/usr/bin/bash $APP_DIR/scripts/backup.sh
EOF
# Persistent: a run missed while the box was off happens at the next boot
# instead of being skipped.
cat > /etc/systemd/system/forge-backup.timer <<EOF
[Unit]
Description=Daily encrypted backup

[Timer]
OnCalendar=*-*-* 03:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now forge-backup.timer >/dev/null

cat > /etc/systemd/system/forge-box-health.service <<EOF
[Unit]
Description=Report a full disk or a failing container to Sentry (scripts/check-box-health.sh)

[Service]
Type=oneshot
User=$DEPLOY_USER
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/bash $APP_DIR/scripts/check-box-health.sh
EOF
cat > /etc/systemd/system/forge-box-health.timer <<EOF
[Unit]
Description=Hourly server health check

[Timer]
OnCalendar=hourly

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now forge-box-health.timer >/dev/null

echo
echo "Done. Next, from docs/runbooks/new-server.md:"
[ -f "$APP_DIR/bitwarden-values.txt" ] &&
  echo "  - store MINIO_KMS_SECRET_KEY from $APP_DIR/bitwarden-values.txt in the Password Manager," &&
  echo "    then fill Bitwarden from your machine: bash scripts/fill-bitwarden.sh <environment> root@<this box>"
grep -q '^BACKUP_REMOTE=' "$HOME_DIR/forge-backup.env" ||
  echo "  - point backups off the box: ssh -t $DEPLOY_USER@<this box> bash forge/scripts/set-backup-remote.sh"
echo "  - lock the CI deploy key (scripts/lock-deploy-key.sh), add the GitHub environment secrets, then deploy"
echo "  - after the first deploy, run the restore drill (backup-restore.md)"
