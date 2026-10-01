"""Bearer tokens for tests that act as a superadmin.

Review R3: require_superadmin reads the *session's* authentication methods
(Session.amr), not the account's enrollment. A bare
`create_access_token(user.id, None, ())` has no session behind it, so it can
no longer reach an admin route -- which is correct, and is exactly the shape
of token the gate exists to refuse. Tests that mean "a superadmin who signed
in properly" mint one here instead: a real Session row that passed a second
factor, and an access token bound to it.
"""

from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models._mixins import utcnow
from app.modules.auth import amr
from app.modules.auth.models import User
from app.modules.auth.repository import SessionRepository
from app.modules.auth.utils import create_access_token, generate_refresh_token

MFA_SESSION = (amr.PASSWORD, amr.TOTP)


async def session_token(
    session: AsyncSession,
    user: User,
    *,
    methods: Sequence[str] = MFA_SESSION,
) -> str:
    """An access token for `user`, bound to a committed session with `methods`."""
    _plain, token_hash = generate_refresh_token()
    row = await SessionRepository(session).create(
        user_id=user.id,
        token_hash=token_hash,
        device_label=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        expires_at=utcnow() + timedelta(days=30),
        amr=methods,
    )
    await session.commit()
    return create_access_token(user.id, None, (), sid=row.id)
