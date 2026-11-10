#!/usr/bin/env bash
# Fills an environment's Bitwarden project with its secrets, from your own
# machine. Nothing is shown on screen or written to a file:
#
#   - generates the keys the app signs and encrypts with: JWT_PRIVATE_KEY and
#     JWT_PUBLIC_KEY, ENCRYPTION_KEY, URL_TOKEN_SECRET, PASSWORD_PEPPER and
#     METRICS_TOKEN;
#   - with a box given, reads the passwords its bootstrap generated
#     (bitwarden-values.txt) over SSH, then offers to delete that file;
#   - asks for the third-party secrets (Stripe, Resend, Google, Sentry, ...),
#     with hidden input, and checks each one's format before saving it.
#
# A name already in Bitwarden is never replaced: a second run only fills gaps,
# and cannot swap the ENCRYPTION_KEY the data was encrypted with. It ends by
# listing what is still missing.
#
# Needs bws (Bitwarden Secrets Manager CLI) and openssl, and an access token
# with write access to the project, typed at the prompt or in
# BWS_ACCESS_TOKEN. Make one for this run and revoke it afterwards: the
# deploy's own token stays read-only.
#
# Usage: scripts/fill-bitwarden.sh production|staging [ssh host of the box]
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

die() { echo "$1" >&2; exit 1; }
target="${1:-}"
box="${2:-}"
[[ "$target" =~ ^(production|staging)$ ]] || die "Usage: scripts/fill-bitwarden.sh production|staging [ssh host of the box]"
project=$(sed -n 's/^BITWARDEN_PROJECT_ID=//p' "deploy/$target.env")
[[ "$project" =~ ^[0-9a-f-]{36}$ ]] || die "deploy/$target.env has no BITWARDEN_PROJECT_ID"
command -v bws >/dev/null || die "bws is not installed: https://bitwarden.com/help/secrets-manager-cli/"
command -v openssl >/dev/null || die "openssl is not installed"

# Git Bash would rewrite an argument that looks like a path on its way to a
# Windows program, and bws is one.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
export BWS_SERVER_URL="${BWS_SERVER_URL:-https://vault.bitwarden.eu}"
if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
  read -rsp "Access token with write access to the $target project: " BWS_ACCESS_TOKEN
  echo >&2
  export BWS_ACCESS_TOKEN
fi

# Names only: a value never leaves bws's output. A key in JSON is never
# preceded by a backslash, so a value cannot pass for one.
listed=$(bws secret list "$project" --output json) ||
  die "Could not read Bitwarden project $project. Does the token have access to it?"
existing=" $({ grep -oE '"key": ?"[A-Z0-9_]+"' <<<"$listed" || true; } | sed -E 's/.*"([A-Z0-9_]+)"$/\1/' | xargs) "
has() { [[ "$existing" == *" $1 "* ]]; }
secret_names=" $(grep -vE '^[[:space:]]*(#|$)' deploy/secret-names | xargs) "

kept=()
put() {
  if has "$1"; then
    kept+=("$1")
    return
  fi
  bws secret create "$1" "$2" "$project" --output none >/dev/null
  existing+="$1 "
  echo "set $1"
}

# The keys. The JWT pair is generated together or not at all: half a pair
# would not sign anything the other half verifies.
if has JWT_PRIVATE_KEY && has JWT_PUBLIC_KEY; then
  kept+=(JWT_PRIVATE_KEY JWT_PUBLIC_KEY)
elif has JWT_PRIVATE_KEY || has JWT_PUBLIC_KEY; then
  die "Only one of JWT_PRIVATE_KEY and JWT_PUBLIC_KEY is in Bitwarden. Delete it and run again."
else
  private=$(openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 2>/dev/null)
  put JWT_PRIVATE_KEY "$private"
  put JWT_PUBLIC_KEY "$(openssl pkey -pubout <<<"$private")"
  unset private
fi
has ENCRYPTION_KEY && kept+=(ENCRYPTION_KEY) ||
  put ENCRYPTION_KEY "$(openssl rand 32 | openssl base64 -A | tr '+/' '-_')"
for name in URL_TOKEN_SECRET PASSWORD_PEPPER METRICS_TOKEN; do
  has "$name" && kept+=("$name") || put "$name" "$(openssl rand -hex 32)"
done

# What the box's bootstrap generated. These must match the passwords its
# services started with, so an existing value is reported, not kept quietly.
if [ -n "$box" ]; then
  values=$(ssh "$box" cat /home/forge/forge/bitwarden-values.txt) ||
    die "Could not read bitwarden-values.txt on $box. Has the bootstrap run, and was the file deleted already?"
  stale=()
  while IFS= read -r line; do
    line=${line%$'\r'}
    [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]] || continue
    name=${BASH_REMATCH[1]} value=${BASH_REMATCH[2]}
    if [[ "$secret_names" != *" $name "* ]]; then
      echo "skipped $name: not for this project (MINIO_KMS_SECRET_KEY goes in the Password Manager)"
    elif has "$name"; then
      stale+=("$name")
    else
      put "$name" "$value"
    fi
  done <<<"$values"
  unset values
  if [ ${#stale[@]} -gt 0 ]; then
    echo "Already in Bitwarden, not replaced: ${stale[*]}. On a new server these are the old one's; delete them in Bitwarden and run again." >&2
  fi
  echo "MINIO_KMS_SECRET_KEY in that file goes in the Password Manager, by hand, before it is deleted." >&2
  read -rp "Delete bitwarden-values.txt on $box now? [y/N] " answer || answer=
  if [[ "${answer%$'\r'}" =~ ^[Yy]$ ]]; then
    ssh "$box" rm /home/forge/forge/bitwarden-values.txt
    echo "deleted bitwarden-values.txt on $box"
  fi
fi

# The third-party secrets. Enter skips one; a value in the wrong format is
# asked for again rather than saved.
ask() {
  local name=$1 pattern=$2 hint=$3 value
  has "$name" && { kept+=("$name"); return; }
  for _ in 1 2 3; do
    read -rsp "$name ($hint; Enter to skip): " value || value=
    echo >&2
    value=$(sed -E 's/^[[:space:]]+|[[:space:]]+$//g' <<<"$value")
    [ -z "$value" ] && return
    if [[ "$value" =~ $pattern ]]; then
      if [[ "$target" == production && "$value" == sk_test_* ]]; then
        echo "note: a test-mode Stripe key on production; payments will not be real" >&2
      fi
      put "$name" "$value"
      return
    fi
    echo "That does not look like $name ($hint). Try again." >&2
  done
  echo "skipped $name after three tries" >&2
}
ask STRIPE_SECRET_KEY '^sk_(test|live)_[A-Za-z0-9]+$' "starts with sk_test_ or sk_live_"
ask STRIPE_WEBHOOK_SECRET '^whsec_[A-Za-z0-9]+$' "starts with whsec_"
ask RESEND_API_KEY '^re_[A-Za-z0-9_]+$' "starts with re_"
ask GOOGLE_CLIENT_SECRET '^GOCSPX-[A-Za-z0-9_-]+$' "starts with GOCSPX-"
ask GITHUB_CLIENT_SECRET '^[0-9a-f]{40}$' "40 hex characters"
ask SENTRY_DSN '^https://[0-9a-f]+@[A-Za-z0-9.-]+/[0-9]+$' "https://<key>@<host>/<project>"
ask VIRUSTOTAL_API_KEY '^[0-9a-f]{64}$' "64 hex characters, only with VIRUS_SCANNER=virustotal"

[ ${#kept[@]} -eq 0 ] || echo "already in Bitwarden, left as they are: ${kept[*]}"
missing=()
for name in $secret_names; do
  [[ "$name" =~ _OLD$|^(MIGRATIONS_DATABASE_URL|READ_REPLICA_URL)$ ]] && continue
  has "$name" || missing+=("$name")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Still not in Bitwarden: ${missing[*]}"
  echo "DATABASE_URL, REDIS_URL and the AWS_ pair come from the box: run again with its SSH host."
fi
echo "Next: copy ENCRYPTION_KEY from Bitwarden into the Password Manager (it is the only way back"
echo "into the encrypted columns), and revoke the write token you used for this run."
