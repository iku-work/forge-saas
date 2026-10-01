"""What a session proved when it was established: its `amr` list.

Review R3 (2026-09-29). `require_superadmin` used to ask whether the
*account* had a second factor enrolled. That is not the same question as
whether the *session* in hand went through one, and the difference was
exploitable: a password-only session opened on one device stayed valid while
the user enrolled TOTP on another, and then passed the admin gate without
ever having been challenged.

So every session records the methods it actually passed (Session.amr), and
the gate reads that. The vocabulary follows RFC 8176 where a term fits and
adds this product's own first factors where none does. Nothing outside this
module should spell these strings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# First factors: how the person got as far as a session (or a challenge).
PASSWORD = "pwd"  # noqa: S105 -- an RFC 8176 method name, not a credential
MAGIC_LINK = "email"
SOCIAL_OAUTH = "oauth"
ENTERPRISE_SSO = "sso"

# Second factors.
TOTP = "otp"
BACKUP_CODE = "backup"
SECURITY_KEY = "hwk"
# The enterprise IdP asserted that *it* checked a second factor (T-331's
# interpretation of the ID token's own amr/acr). An upstream factor we were
# told about, not one we checked -- which is also what the IdP connection
# is trusted for.
IDP_MFA = "mfa"

SECOND_FACTORS = frozenset({TOTP, BACKUP_CODE, SECURITY_KEY, IDP_MFA})


def has_second_factor(amr: Iterable[str]) -> bool:
    """Whether a session with this `amr` went through a second factor."""
    return not SECOND_FACTORS.isdisjoint(amr)
