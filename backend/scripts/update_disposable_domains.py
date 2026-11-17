"""Refresh app/modules/auth/disposable_domains.txt from the open-source
disposable-email-domains list (CC0, public domain).

    python scripts/update_disposable_domains.py

Every line is checked to be a plain lower-case domain before anything is
written, so a changed or tampered upstream file can't put anything else into
the blocklist. Commit the result; the diff shows what was added and removed.
"""

import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SOURCE = (
    "https://raw.githubusercontent.com/disposable-email-domains/"
    "disposable-email-domains/main/disposable_email_blocklist.conf"
)
TARGET = (
    Path(__file__).resolve().parent.parent / "app" / "modules" / "auth" / "disposable_domains.txt"
)
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_MIN_EXPECTED = 1000  # the list has thousands; far fewer means a bad download


def main() -> int:
    with urllib.request.urlopen(SOURCE, timeout=30) as response:  # noqa: S310 -- fixed https URL above
        text = response.read().decode("utf-8")
    domains = sorted({line.strip().lower() for line in text.splitlines() if line.strip()})
    bad = [d for d in domains if not _DOMAIN.match(d)]
    if bad:
        print(f"refusing: {len(bad)} lines are not plain domains, e.g. {bad[:3]}", file=sys.stderr)
        return 1
    if len(domains) < _MIN_EXPECTED:
        print(f"refusing: only {len(domains)} domains, expected thousands", file=sys.stderr)
        return 1
    header = (
        "# Disposable email domains: sign-up and email change refuse these.\n"
        f"# Source: {SOURCE}\n"
        "# License: CC0 1.0 (public domain).\n"
        f"# Refreshed: {datetime.now(UTC).date().isoformat()}"
        " by scripts/update_disposable_domains.py\n"
        "# Products add or exempt domains in product/config.py, not here.\n"
    )
    TARGET.write_text(header + "\n".join(domains) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(domains)} domains to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
