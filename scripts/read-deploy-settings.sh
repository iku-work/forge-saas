#!/usr/bin/env bash
# Reads an environment's committed settings, deploy/common.env and
# deploy/<environment>.env, for the Hetzner deploy. Writes settings.env, the
# lines that ship to the box, and adds BITWARDEN_PROJECT_ID, DOMAIN and
# MARKETING_DOMAIN to $GITHUB_ENV.
#
# Fails, naming them, on what would ship broken: a line that is not NAME=value,
# a name set twice, a secret (deploy/secret-names) in a file this public
# repository commits, or an ENVIRONMENT that is not this environment's.
#
# Usage: read-deploy-settings.sh production|staging
set -euo pipefail
cd "$(dirname "$0")/.."

target="${1:-}"
case "$target" in
  production) want=prod ;;
  staging) want=staging ;;
  *) echo "environment must be staging or production, got: $target" >&2; exit 1 ;;
esac
files=(deploy/common.env "deploy/$target.env")
for f in "${files[@]}"; do
  [ -f "$f" ] || { echo "$f is missing" >&2; exit 1; }
done

fail() { echo "$1" >&2; exit 1; }
lines=$(grep -hvE '^[[:space:]]*(#|$)' "${files[@]}" || true)
bad=$(grep -vE '^[A-Z][A-Z0-9_]*=' <<<"$lines" || true)
[ -z "$bad" ] || fail "Not NAME=value, in ${files[*]}: $bad"
names=$(cut -d= -f1 <<<"$lines")
dupes=$(sort <<<"$names" | uniq -d | xargs)
[ -z "$dupes" ] || fail "Set more than once in ${files[*]}: $dupes"
leaked=$(grep -Fx -f <(grep -vE '^[[:space:]]*(#|$)' deploy/secret-names) <<<"$names" | xargs || true)
[ -z "$leaked" ] || fail "Secrets committed to ${files[*]}: $leaked. Delete them there, rotate them (this repository is public) and keep them in Bitwarden."

value() { sed -n "s/^$1=//p" <<<"$lines"; }
project=$(value BITWARDEN_PROJECT_ID)
[[ "$project" =~ ^[0-9a-f-]{36}$ ]] || fail "deploy/$target.env needs BITWARDEN_PROJECT_ID, the ID of the Bitwarden project holding $target's secrets"
[ "$(value ENVIRONMENT)" = "$want" ] || fail "deploy/$target.env must set ENVIRONMENT=$want"
domain=$(value DOMAIN)
[[ "$domain" =~ ^[A-Za-z0-9.-]+$ ]] || fail "DOMAIN in deploy/$target.env must be a bare hostname"
marketing=$(value MARKETING_DOMAIN)
[[ "$marketing" =~ ^[A-Za-z0-9.-]*$ ]] || fail "MARKETING_DOMAIN in deploy/$target.env must be a bare hostname"

grep -v '^BITWARDEN_PROJECT_ID=' <<<"$lines" > settings.env
printf 'BITWARDEN_PROJECT_ID=%s\nDOMAIN=%s\nMARKETING_DOMAIN=%s\n' "$project" "$domain" "$marketing" >> "$GITHUB_ENV"
