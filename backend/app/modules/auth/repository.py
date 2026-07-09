"""
DB access for the auth module's User, Session, and single-use token tables.

These are *not* org-scoped: per §0.2, User/Session/OAuthAccount/TOTPSecret/
ConsentRecord belong to a user, not an org. app.core.repository.BaseRepository
assumes an org-scoped model (it requires org_id in its constructor), so it
doesn't fit here — each repository below is a small bespoke class instead.
"""

from datetime import datetime
from typing import Any, ClassVar, Protocol, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.models._mixins import utcnow
from app.modules.auth.models import (
    EmailVerificationToken,
    MagicLinkToken,
    OAuthAccount,
    PasswordResetToken,
    TOTPSecret,
    User,
    WebAuthnCredential,
)
from app.modules.auth.models import (
    Session as SessionModel,
)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        m = cast("Any", User)
        stmt = select(User).where(m.id == user_id, m.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        m = cast("Any", User)
        stmt = select(User).where(m.email == email, m.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, **kwargs: Any) -> User:  # noqa: ANN401 — forwarded to User(**kwargs)
        user = User(**kwargs)
        self.session.add(user)
        await self.session.flush()
        return user

    async def update(self, user_id: UUID, **kwargs: Any) -> User:  # noqa: ANN401 — see create()
        user = await self._get_or_raise(user_id)
        for key, value in kwargs.items():
            setattr(user, key, value)
        await self.session.flush()
        return user

    async def increment_failed_login(self, user_id: UUID) -> None:
        user = await self._get_or_raise(user_id)
        user.failed_login_count += 1
        await self.session.flush()

    async def reset_failed_login(self, user_id: UUID) -> None:
        user = await self._get_or_raise(user_id)
        user.failed_login_count = 0
        await self.session.flush()

    async def set_lockout(self, user_id: UUID, until: datetime) -> None:
        user = await self._get_or_raise(user_id)
        user.locked_until = until
        await self.session.flush()

    async def list_cursor(
        self,
        after: UUID | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        *,
        q: str | None = None,
        active: bool | None = None,
    ) -> tuple[list[User], UUID | None]:
        # T-141: admin user directory -- global (not org-scoped), so this
        # can't live on BaseRepository. Mirrors OrganizationRepository's own
        # list_cursor (app.modules.tenancy.repository) for the same reason.
        capped_limit = min(limit, MAX_PAGE_SIZE)
        m = cast("Any", User)
        stmt = select(User).where(m.deleted_at.is_(None))
        if q is not None:
            stmt = stmt.where(m.email.ilike(f"%{q}%"))
        if active is not None:
            stmt = stmt.where(m.is_active.is_(active))
        stmt = stmt.order_by(m.created_at.desc(), m.id.desc()).limit(capped_limit + 1)
        if after is not None:
            anchor = await self.get_by_id(after)
            if anchor is not None:
                stmt = stmt.where(tuple_(m.created_at, m.id) < (anchor.created_at, anchor.id))
        result = await self.session.execute(stmt)
        items = list(result.scalars().all())
        page = items[:capped_limit]
        next_cursor = page[-1].id if page and len(items) > capped_limit else None
        return page, next_cursor

    async def mark_email_verified(self, user_id: UUID) -> None:
        # Verifying the email is what flips a freshly-registered user from
        # is_active=False to usable (§T-017) — the two fields always change
        # together, so a single repository method keeps that pairing atomic.
        user = await self._get_or_raise(user_id)
        user.email_verified_at = utcnow()
        user.is_active = True
        await self.session.flush()

    async def _get_or_raise(self, user_id: UUID) -> User:
        user = await self.get_by_id(user_id)
        if user is None:
            raise NotFoundError("errors.user_not_found", fallback="User not found")
        return user


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(  # noqa: PLR0913 — matches the T-016 spec's exact signature
        self,
        *,
        user_id: UUID,
        token_hash: str,
        device_label: str | None,
        ip_address: str,
        user_agent: str,
        expires_at: datetime,
    ) -> SessionModel:
        now = utcnow()
        session_obj = SessionModel(
            user_id=user_id,
            refresh_token_hash=token_hash,
            device_label=device_label,
            ip_address=ip_address,
            user_agent=user_agent,
            last_seen_at=now,
            expires_at=expires_at,
        )
        self.session.add(session_obj)
        await self.session.flush()
        return session_obj

    async def get_by_token_hash(self, token_hash: str) -> SessionModel | None:
        m = cast("Any", SessionModel)
        stmt = select(SessionModel).where(m.refresh_token_hash == token_hash)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke(self, session_id: UUID) -> None:
        session_obj = await self._get_or_raise(session_id)
        session_obj.revoked_at = utcnow()
        await self.session.flush()

    async def revoke_all_for_user(self, user_id: UUID, *, except_id: UUID | None = None) -> None:
        # T-216: except_id lets change_password() keep the session the
        # request itself is authenticated with alive while revoking every
        # other one (ASVS V3.3.4's "all *other* active sessions" -- unlike
        # confirm_password_reset, which has no authenticated session to
        # spare and always revokes everything).
        m = cast("Any", SessionModel)
        stmt = select(SessionModel).where(m.user_id == user_id, m.revoked_at.is_(None))
        if except_id is not None:
            stmt = stmt.where(m.id != except_id)
        result = await self.session.execute(stmt)
        now = utcnow()
        for session_obj in result.scalars().all():
            session_obj.revoked_at = now
        await self.session.flush()

    async def list_active_for_user(self, user_id: UUID) -> list[SessionModel]:
        m = cast("Any", SessionModel)
        stmt = select(SessionModel).where(
            m.user_id == user_id,
            m.revoked_at.is_(None),
            m.expires_at > utcnow(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_active(self) -> int:
        m = cast("Any", SessionModel)
        stmt = (
            select(func.count())
            .select_from(SessionModel)
            .where(m.revoked_at.is_(None), m.expires_at > utcnow())
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def prune_expired(self) -> int:
        m = cast("Any", SessionModel)
        stmt = delete(SessionModel).where(m.expires_at < utcnow())
        result = await self.session.execute(stmt)
        await self.session.flush()
        return cast("CursorResult[Any]", result).rowcount

    async def get_by_id(self, session_id: UUID) -> SessionModel | None:
        m = cast("Any", SessionModel)
        stmt = select(SessionModel).where(m.id == session_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_or_raise(self, session_id: UUID) -> SessionModel:
        session_obj = await self.get_by_id(session_id)
        if session_obj is None:
            raise NotFoundError("errors.session_not_found", fallback="Session not found")
        return session_obj


class OAuthAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_provider_account(
        self, provider: str, provider_user_id: str
    ) -> OAuthAccount | None:
        m = cast("Any", OAuthAccount)
        stmt = select(OAuthAccount).where(
            m.provider == provider, m.provider_user_id == provider_user_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, *, user_id: UUID, provider: str, provider_user_id: str) -> OAuthAccount:
        account = OAuthAccount(
            user_id=user_id, provider=provider, provider_user_id=provider_user_id
        )
        self.session.add(account)
        await self.session.flush()
        return account

    async def list_for_user(self, user_id: UUID) -> list[OAuthAccount]:
        m = cast("Any", OAuthAccount)
        stmt = select(OAuthAccount).where(m.user_id == user_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_user_and_provider(self, user_id: UUID, provider: str) -> OAuthAccount | None:
        m = cast("Any", OAuthAccount)
        stmt = select(OAuthAccount).where(m.user_id == user_id, m.provider == provider)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete(self, account_id: UUID) -> None:
        m = cast("Any", OAuthAccount)
        stmt = delete(OAuthAccount).where(m.id == account_id)
        await self.session.execute(stmt)
        await self.session.flush()


class TOTPSecretRepository:
    """TOTPSecret's primary key *is* user_id (T-013) -- there is at most one
    row per user, so "enabling" after a previous disable reuses that same
    row (clearing deleted_at) rather than inserting a second one.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id: UUID) -> TOTPSecret | None:
        m = cast("Any", TOTPSecret)
        stmt = select(TOTPSecret).where(m.user_id == user_id, m.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_pending(self, user_id: UUID, secret: str) -> TOTPSecret:
        m = cast("Any", TOTPSecret)
        stmt = select(TOTPSecret).where(m.user_id == user_id)
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            record = TOTPSecret(user_id=user_id, secret_encrypted=secret)
            self.session.add(record)
        else:
            record.secret_encrypted = secret
            record.enabled_at = None
            record.backup_codes_hash = "[]"
            record.deleted_at = None
        await self.session.flush()
        return record

    async def mark_enabled(self, user_id: UUID, backup_codes_hash: str) -> None:
        record = await self._get_or_raise(user_id)
        record.enabled_at = utcnow()
        record.backup_codes_hash = backup_codes_hash
        await self.session.flush()

    async def update_backup_codes(self, user_id: UUID, backup_codes_hash: str) -> None:
        record = await self._get_or_raise(user_id)
        record.backup_codes_hash = backup_codes_hash
        await self.session.flush()

    async def disable(self, user_id: UUID) -> None:
        record = await self._get_or_raise(user_id)
        record.deleted_at = utcnow()
        await self.session.flush()

    async def _get_or_raise(self, user_id: UUID) -> TOTPSecret:
        record = await self.get_by_user_id(user_id)
        if record is None:
            raise NotFoundError("errors.totp_secret_not_found", fallback="TOTP secret not found")
        return record


class WebAuthnCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(self, user_id: UUID) -> list[WebAuthnCredential]:
        m = cast("Any", WebAuthnCredential)
        stmt = select(WebAuthnCredential).where(m.user_id == user_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def exists_for_user(self, user_id: UUID) -> bool:
        m = cast("Any", WebAuthnCredential)
        stmt = select(func.count()).select_from(WebAuthnCredential).where(m.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one() > 0

    async def get_by_id(self, credential_id: UUID) -> WebAuthnCredential | None:
        m = cast("Any", WebAuthnCredential)
        stmt = select(WebAuthnCredential).where(m.id == credential_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_credential_id(self, credential_id: str) -> WebAuthnCredential | None:
        m = cast("Any", WebAuthnCredential)
        stmt = select(WebAuthnCredential).where(m.credential_id == credential_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(  # noqa: PLR0913 — matches SessionRepository.create's precedent
        self,
        *,
        user_id: UUID,
        credential_id: str,
        public_key: str,
        sign_count: int,
        aaguid: str,
        nickname: str | None,
    ) -> WebAuthnCredential:
        record = WebAuthnCredential(
            user_id=user_id,
            credential_id=credential_id,
            public_key=public_key,
            sign_count=sign_count,
            aaguid=aaguid,
            nickname=nickname,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def update_sign_count(self, credential_id: UUID, sign_count: int) -> None:
        record = await self._get_or_raise(credential_id)
        record.sign_count = sign_count
        record.last_used_at = utcnow()
        await self.session.flush()

    async def delete(self, credential_id: UUID) -> None:
        m = cast("Any", WebAuthnCredential)
        stmt = delete(WebAuthnCredential).where(m.id == credential_id)
        await self.session.execute(stmt)
        await self.session.flush()

    async def _get_or_raise(self, credential_id: UUID) -> WebAuthnCredential:
        record = await self.get_by_id(credential_id)
        if record is None:
            raise NotFoundError(
                "errors.webauthn_credential_not_found", fallback="Security key not found"
            )
        return record


class _SingleUseToken(Protocol):
    """Structural shape every single-use token model must have."""

    id: UUID
    token_hash: str
    expires_at: datetime
    used_at: datetime | None


class _TokenRepository[T: _SingleUseToken]:
    """Shared shape for EmailVerificationToken / PasswordResetToken / MagicLinkToken.

    Mirrors BaseRepository's structural-typing approach (see
    app.core.repository) since SQLModel table classes aren't typed with
    SQLAlchemy's ``Mapped[]`` — query construction goes through a
    ``cast("Any", ...)`` view of the model.
    """

    model: ClassVar[type[T]]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs: Any) -> T:  # noqa: ANN401 — forwarded to model ctor
        token = self.model(**kwargs)
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_by_hash(self, token_hash: str) -> T | None:
        m = cast("Any", self.model)
        stmt = select(self.model).where(
            m.token_hash == token_hash,
            m.used_at.is_(None),
            m.expires_at > utcnow(),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_used(self, token_id: UUID) -> None:
        m = cast("Any", self.model)
        stmt = select(self.model).where(m.id == token_id)
        result = await self.session.execute(stmt)
        token = result.scalar_one_or_none()
        if token is None:
            raise NotFoundError(
                "errors.resource_not_found",
                fallback=f"{self.model.__name__} not found",
                params={"resource": self.model.__name__},
            )
        token.used_at = utcnow()
        await self.session.flush()

    async def delete_expired(self) -> int:
        m = cast("Any", self.model)
        stmt = delete(self.model).where(m.expires_at < utcnow())
        result = await self.session.execute(stmt)
        await self.session.flush()
        return cast("CursorResult[Any]", result).rowcount


class EmailVerificationTokenRepository(_TokenRepository[EmailVerificationToken]):
    model = EmailVerificationToken


class PasswordResetTokenRepository(_TokenRepository[PasswordResetToken]):
    model = PasswordResetToken


class MagicLinkTokenRepository(_TokenRepository[MagicLinkToken]):
    model = MagicLinkToken
