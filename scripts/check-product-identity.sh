#!/usr/bin/env bash
# Refuses to deploy a product that still carries the template's identity: its
# name, its placeholder support address, the placeholder legal pages, or an
# indexing origin that is not this product's site. Each of these fails silently
# otherwise, in front of real users. docs/runbooks/first-product.md is the list
# of what to change.
#
# Usage: check-product-identity.sh <public site url, empty without marketing>
set -uo pipefail
cd "$(dirname "$0")/.."

site="${1:-}"
failures=0
refuse() {
  echo "refused: $1" >&2
  failures=1
}

grep -q 'name: "ForgeSaaS"' product.config.ts &&
  refuse "product.config.ts still names the product ForgeSaaS"
grep -q 'supportEmail: ".*@example\.com"' product.config.ts &&
  refuse "product.config.ts still has a support address at example.com"
grep -q '^APP_NAME = "ForgeSaaS"' backend/product/config.py &&
  refuse "backend/product/config.py still names the product ForgeSaaS"
grep -q '^SUPPORT_EMAIL = ".*@example\.com"' backend/product/config.py &&
  refuse "backend/product/config.py still has a support address at example.com"

if [ -n "$site" ]; then
  origin="$(sed -n 's/^export const PRODUCTION_ORIGIN = "\(.*\)";.*/\1/p' apps/marketing/src/config/indexing.ts)"
  [ "${site%/}" = "$origin" ] ||
    refuse "PRODUCTION_ORIGIN in apps/marketing/src/config/indexing.ts is $origin, not $site, so the site would not be indexed"
  grep -lq 'Have a lawyer' apps/marketing/src/content/legal/en/*.mdx &&
    refuse "the legal pages in apps/marketing/src/content/legal are still the template's placeholders"
fi

exit "$failures"
