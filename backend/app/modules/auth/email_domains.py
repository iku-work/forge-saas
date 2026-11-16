"""Which email domains may hold an account: not a disposable one.

The bundled list (disposable_domains.txt, refreshed by
scripts/update_disposable_domains.py) plus the product's own
BLOCKED_EMAIL_DOMAINS, minus its ALLOWED_EMAIL_DOMAINS. A listed domain covers
its subdomains, since disposable services hand those out freely.
"""

from functools import lru_cache
from pathlib import Path

from app.core.exceptions import UnprocessableEntityError
from app.core.module_loader import get_email_domain_rules

_BUNDLED = Path(__file__).with_name("disposable_domains.txt")


@lru_cache(maxsize=1)
def _bundled_domains() -> frozenset[str]:
    lines = _BUNDLED.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def is_blocked_email(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].strip().lower().rstrip(".")
    blocked, allowed = get_email_domain_rules()
    labels = domain.split(".")
    # The domain itself and every parent: "x.mailinator.com" is "mailinator.com".
    candidates = [".".join(labels[i:]) for i in range(len(labels) - 1)]
    if any(candidate in allowed for candidate in candidates):
        return False
    listed = _bundled_domains() | blocked
    return any(candidate in listed for candidate in candidates)


def refuse_blocked_email(email: str) -> None:
    if is_blocked_email(email):
        raise UnprocessableEntityError(
            "errors.email_domain_not_allowed",
            fallback="Use a permanent email address: this one is from a disposable email service.",
        )
