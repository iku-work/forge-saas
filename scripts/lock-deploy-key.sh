#!/usr/bin/env bash
# Lets CI's deploy key run scripts/deploy-box.sh on this box and nothing else.
# Run as root ON THE BOX, from a copy of this file, with the key's public half:
#
#   scp scripts/lock-deploy-key.sh root@<ip>:
#   ssh root@<ip> bash lock-deploy-key.sh "$(cat ~/.ssh/<product>_ci_deploy.pub)"
#
# Any unrestricted line for the same key is replaced, so this also locks a key
# that was added the old way. Other keys, your own included, are left alone.
# Safe to run again.
set -euo pipefail

DEPLOY_USER=forge
HOME_DIR=/home/$DEPLOY_USER
APP_DIR=$HOME_DIR/forge
KEYS=$HOME_DIR/.ssh/authorized_keys
SCRIPT=$APP_DIR/scripts/deploy-box.sh

[ "$(id -u)" = 0 ] || { echo "Run as root." >&2; exit 1; }
read -r type blob _ <<<"${1:?usage: lock-deploy-key.sh '<ssh public key>'}"
[[ "$type" =~ ^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa)$ && "$blob" =~ ^[A-Za-z0-9+/]+=*$ ]] ||
  { echo "Not an SSH public key: give the .pub file's contents." >&2; exit 1; }
[ -f "$KEYS" ] || { echo "$KEYS is missing: run scripts/bootstrap-hetzner-box.sh first." >&2; exit 1; }

# A checkout from before the locked key has nothing for it to run.
if [ ! -f "$SCRIPT" ]; then
  echo "== Moving the checkout to origin/main, which has scripts/deploy-box.sh"
  runuser -u "$DEPLOY_USER" -- git -C "$APP_DIR" fetch --quiet --prune origin
  runuser -u "$DEPLOY_USER" -- git -C "$APP_DIR" checkout --quiet --detach origin/main
fi

line="restrict,command=\"/usr/bin/bash $SCRIPT\" $type $blob ci-deploy"
# Written beside the original and moved over it, so sshd never reads half a file.
{ grep -vF " $blob" "$KEYS" || true; echo "$line"; } > "$KEYS.new"
chown "$DEPLOY_USER:$DEPLOY_USER" "$KEYS.new"
chmod 600 "$KEYS.new"
mv "$KEYS.new" "$KEYS"
echo "Locked: that key now runs only $SCRIPT."
