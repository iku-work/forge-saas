"""
Business logic for registration, email verification (T-017), login with
brute-force lockout (T-018), refresh-token rotation with theft detection
(T-019), and password reset + magic link (T-020).
"""

import json
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import pyotp
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, ConflictError, NotFoundError
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.models import TOTPSecret, User
from app.modules.auth.repository import (
    EmailVerificationTokenRepository,
    MagicLinkTokenRepository,
    OAuthAccountRepository,
    PasswordResetTokenRepository,
    SessionRepository,
    TOTPSecretRepository,
    UserRepository,
)
from app.modules.auth.schemas import LoginRequest, OAuthProfile, RegisterRequest
from app.modules.auth.utils import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    sign_url_token,
    verify_password,
    verify_url_token,
)

logger = logging.getLogger(__name__)

_EMAIL_VERIFICATION_TOKEN_TTL = timedelta(hours=24)
_PASSWORD_RESET_TOKEN_TTL = timedelta(hours=1)
_MAGIC_LINK_TOKEN_TTL = timedelta(minutes=15)
_MAX_FAILED_LOGIN_ATTEMPTS = 10
_LOCKOUT_DURATION = timedelta(minutes=15)
# Hashed once at import time so a login for an unknown email (or an
# OAuth-only account with no password) still pays argon2's full verify
# cost — otherwise the timing difference would itself leak which emails
# are registered.
_DUMMY_PASSWORD_HASH = hash_password(uuid4().hex)

# §T-024: the token /login hands back instead of real credentials when
# totp_enabled is set -- short-lived since it only proves "password already
# checked", not "this session is authenticated".
_TOTP_LOGIN_PURPOSE = "2fa_login"
_TOTP_LOGIN_TOKEN_TTL_SECONDS = 300
_BACKUP_CODE_COUNT = 10


@dataclass(frozen=True, slots=True)
class TwoFactorChallenge:
    temp_token: str


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)
        self._sessions = SessionRepository(session)
        self._email_verification_tokens = EmailVerificationTokenRepository(session)
        self._password_reset_tokens = PasswordResetTokenRepository(session)
        self._magic_link_tokens = MagicLinkTokenRepository(session)
        self._oauth_accounts = OAuthAccountRepository(session)
        self._totp_secrets = TOTPSecretRepository(session)
        self._audit = AuditService(session)

    async def register(self, req: RegisterRequest) -> None:
        existing = await self._users.get_by_email(req.email)
        if existing is not None:
            # No enumeration: the caller replies "check your inbox" on this
            # path exactly as it does on the happy path below — the only
            # observable difference anywhere is this log line.
            logger.warning("auth.register: duplicate email attempt (user_id=%s)", existing.id)
            return

        user = await self._users.create(
            email=req.email,
            hashed_password=hash_password(req.password),
            is_active=False,
        )
        plain_token, token_hash = generate_refresh_token()
        await self._email_verification_tokens.create(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=utcnow() + _EMAIL_VERIFICATION_TOKEN_TTL,
        )

        logger.info("auth.register: created user (user_id=%s)", user.id)
        # No org context exists yet at registration -- org_id is null per
        # app.modules.audit.models.AuditEvent's docstring.
        await self._audit.log(
            action="user.registered",
            resource_type="user",
            resource_id=str(user.id),
            org_id=None,
            actor_id=user.id,
        )
        await self._enqueue_verification_email(user, plain_token)

    async def verify_email(self, token: str) -> User:
        record = await self._email_verification_tokens.get_by_hash(hash_token(token))
        if record is None:
            raise AuthenticationError(
                "errors.invalid_verification_token",
                fallback="Invalid or expired verification token",
            )

        user = await self._users.get_by_id(record.user_id)
        if user is None:
            raise AuthenticationError(
                "errors.invalid_verification_token",
                fallback="Invalid or expired verification token",
            )

        await self._email_verification_tokens.mark_used(record.id)
        await self._users.mark_email_verified(record.user_id)
        return user

    async def login(
        self, req: LoginRequest, ip: str, user_agent: str
    ) -> tuple[str, str, User] | TwoFactorChallenge:
        user = await self._users.get_by_email(req.email)
        if user is None:
            verify_password(req.password, _DUMMY_PASSWORD_HASH)
            raise AuthenticationError(
                "errors.invalid_credentials", fallback="Invalid email or password"
            )

        if user.locked_until is not None and user.locked_until > utcnow():
            raise AuthenticationError(
                "errors.account_locked",
                fallback="Your account is temporarily locked due to too many failed attempts.",
            )

        if user.email_verified_at is None:
            raise AuthenticationError(
                "errors.email_not_verified",
                fallback="Please verify your email address before logging in.",
            )

        if not verify_password(req.password, user.hashed_password or _DUMMY_PASSWORD_HASH):
            await self._users.increment_failed_login(user.id)
            if user.failed_login_count >= _MAX_FAILED_LOGIN_ATTEMPTS:
                await self._users.set_lockout(user.id, utcnow() + _LOCKOUT_DURATION)
                await self._users.reset_failed_login(user.id)
            raise AuthenticationError(
                "errors.invalid_credentials", fallback="Invalid email or password"
            )

        await self._users.reset_failed_login(user.id)

        if user.totp_enabled:
            # Password is correct, but that alone isn't enough to issue real
            # tokens -- temp_token only proves this much and must be traded
            # for a code at /auth/2fa/login (confirm_login_2fa below).
            temp_token = sign_url_token(
                {"user_id": str(user.id), "purpose": _TOTP_LOGIN_PURPOSE},
                _TOTP_LOGIN_TOKEN_TTL_SECONDS,
            )
            return TwoFactorChallenge(temp_token=temp_token)

        result = await self._create_session(user, ip, user_agent)
        await self._audit.log(
            action="user.login_success",
            resource_type="user",
            resource_id=str(user.id),
            org_id=None,
            actor_id=user.id,
            ip=ip,
            ua=user_agent,
        )
        return result

    async def _create_session(self, user: User, ip: str, user_agent: str) -> tuple[str, str, User]:
        access_token = create_access_token(user.id, None, ())
        refresh_plain, refresh_hash = generate_refresh_token()
        settings = get_settings()
        await self._sessions.create(
            user_id=user.id,
            token_hash=refresh_hash,
            device_label=None,
            ip_address=ip,
            user_agent=user_agent,
            expires_at=utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
        return access_token, refresh_plain, user

    async def refresh(self, refresh_token_plain: str, ip: str, user_agent: str) -> tuple[str, str]:
        session = await self._sessions.get_by_token_hash(hash_token(refresh_token_plain))

        if session is None:
            raise AuthenticationError(
                "errors.session_invalid",
                fallback="Your session is no longer valid. Please log in again.",
            )

        if session.revoked_at is not None or session.expires_at <= utcnow():
            # A revoked-or-expired token being presented again is the
            # signature of a stolen refresh token (rotation means the
            # legitimate client already moved on to a newer one). Revoking
            # every session the user has -- not just this one -- is what
            # actually kicks out an attacker replaying it from another device.
            await self._sessions.revoke_all_for_user(session.user_id)
            raise AuthenticationError(
                "errors.session_invalid",
                fallback="Your session is no longer valid. Please log in again.",
            )

        await self._sessions.revoke(session.id)

        access_token = create_access_token(session.user_id, None, ())
        refresh_plain, refresh_hash = generate_refresh_token()
        settings = get_settings()
        await self._sessions.create(
            user_id=session.user_id,
            token_hash=refresh_hash,
            device_label=session.device_label,
            ip_address=ip,
            user_agent=user_agent,
            expires_at=utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )

        return access_token, refresh_plain

    async def request_password_reset(self, email: str) -> None:
        user = await self._users.get_by_email(email)
        if user is None:
            # No enumeration: silently return so the caller's "check your
            # inbox" response is identical whether or not the email exists.
            return

        plain_token, token_hash = generate_refresh_token()
        await self._password_reset_tokens.create(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=utcnow() + _PASSWORD_RESET_TOKEN_TTL,
        )
        await self._enqueue_password_reset_email(user, plain_token)

    async def confirm_password_reset(self, token: str, new_password: str) -> None:
        record = await self._password_reset_tokens.get_by_hash(hash_token(token))
        if record is None:
            raise AuthenticationError(
                "errors.invalid_reset_token", fallback="Invalid or expired reset token"
            )

        user = await self._users.get_by_id(record.user_id)
        if user is None:
            raise AuthenticationError(
                "errors.invalid_reset_token", fallback="Invalid or expired reset token"
            )

        await self._password_reset_tokens.mark_used(record.id)
        await self._users.update(user.id, hashed_password=hash_password(new_password))
        # A password reset is treated the same as a confirmed compromise of
        # every existing session -- whoever could read the reset email now
        # controls the account, so anyone already logged in elsewhere (e.g.
        # the actual attacker) gets signed out.
        await self._sessions.revoke_all_for_user(user.id)

    async def request_magic_link(self, email: str) -> None:
        user = await self._users.get_by_email(email)
        if user is None:
            # Same enumeration protection as request_password_reset above.
            return

        plain_token, token_hash = generate_refresh_token()
        await self._magic_link_tokens.create(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=utcnow() + _MAGIC_LINK_TOKEN_TTL,
        )
        await self._enqueue_magic_link_email(user, plain_token)

    async def consume_magic_link(
        self, token: str, ip: str, user_agent: str
    ) -> tuple[str, str, User]:
        record = await self._magic_link_tokens.get_by_hash(hash_token(token))
        if record is None:
            raise AuthenticationError(
                "errors.invalid_magic_link", fallback="Invalid or expired magic link"
            )

        user = await self._users.get_by_id(record.user_id)
        if user is None:
            raise AuthenticationError(
                "errors.invalid_magic_link", fallback="Invalid or expired magic link"
            )

        await self._magic_link_tokens.mark_used(record.id)

        return await self._create_session(user, ip, user_agent)

    async def oauth_login(
        self, provider: str, profile: OAuthProfile, ip: str, user_agent: str
    ) -> tuple[str, str, User]:
        oauth_account = await self._oauth_accounts.get_by_provider_account(
            provider, profile.provider_user_id
        )
        if oauth_account is not None:
            user = await self._users.get_by_id(oauth_account.user_id)
            if user is None:
                raise AuthenticationError(
                    "errors.oauth_no_linked_user",
                    fallback="OAuth account has no linked user",
                )
        else:
            if not profile.email_verified:
                # The only signal we have to match this profile to an existing
                # account is its email -- trusting that match requires the
                # provider to have verified the address itself.
                raise AuthenticationError(
                    "errors.oauth_email_not_verified",
                    fallback="OAuth provider did not report a verified email",
                )

            user = await self._users.get_by_email(profile.email)
            if user is None:
                user = await self._users.create(
                    email=profile.email,
                    hashed_password=None,  # OAuth-only account -- §T-023
                    email_verified_at=utcnow(),
                    is_active=True,
                    full_name=profile.full_name,
                    avatar_url=profile.avatar_url,
                )
            elif user.email_verified_at is None:
                # A provider-verified email supersedes our own pending local
                # verification for the same address.
                user = await self._users.update(user.id, email_verified_at=utcnow(), is_active=True)

            await self._oauth_accounts.create(
                user_id=user.id, provider=provider, provider_user_id=profile.provider_user_id
            )

        return await self._create_session(user, ip, user_agent)

    async def logout(self, refresh_token_plain: str) -> None:
        session = await self._sessions.get_by_token_hash(hash_token(refresh_token_plain))
        if session is not None:
            await self._sessions.revoke(session.id)

    async def logout_all(self, user_id: UUID) -> None:
        await self._sessions.revoke_all_for_user(user_id)

    async def list_sessions(self, user_id: UUID) -> list[SessionModel]:
        return await self._sessions.list_active_for_user(user_id)

    async def update_locale(self, user_id: UUID, locale: str) -> User:
        return await self._users.update(user_id, locale=locale)

    async def update_notification_preferences(
        self, user_id: UUID, preferences: dict[str, dict[str, bool]]
    ) -> User:
        # T-103: the only write path to this column -- NotificationService
        # (a different module) never reaches into UserRepository directly
        # per §0.1, so its read path (channels_for()) and this write each go
        # through their own module's service instead of sharing a repo.
        return await self._users.update(user_id, notification_preferences=preferences)

    async def revoke_session(self, user_id: UUID, session_id: UUID) -> None:
        session = await self._sessions.get_by_id(session_id)
        if session is None or session.user_id != user_id:
            # Same shape whether the session doesn't exist or belongs to
            # someone else -- a 403 would confirm the id refers to a real,
            # someone-else's session.
            raise NotFoundError("errors.session_not_found", fallback="Session not found")
        await self._sessions.revoke(session.id)

    async def enable_2fa(self, user: User) -> tuple[str, str]:
        """Returns (provisioning_uri, secret). Not active until verify_2fa()."""
        if user.totp_enabled:
            raise ConflictError(
                "errors.totp_already_enabled",
                fallback="Two-factor authentication is already enabled",
            )

        secret = pyotp.random_base32()
        await self._totp_secrets.upsert_pending(user.id, secret)
        provisioning_uri = pyotp.TOTP(secret).provisioning_uri(
            name=user.email, issuer_name="ForgeSaaS"
        )
        return provisioning_uri, secret

    async def verify_2fa(self, user: User, code: str) -> list[str]:
        """Activates 2FA and returns the plaintext backup codes -- shown once."""
        record = await self._totp_secrets.get_by_user_id(user.id)
        if record is None or record.enabled_at is not None:
            raise ConflictError(
                "errors.totp_setup_not_pending",
                fallback="No pending two-factor setup to verify",
            )

        if not pyotp.TOTP(record.secret_encrypted).verify(code, valid_window=1):
            raise AuthenticationError(
                "errors.invalid_totp_code", fallback="Invalid two-factor code"
            )

        backup_codes = [secrets.token_hex(5) for _ in range(_BACKUP_CODE_COUNT)]
        await self._totp_secrets.mark_enabled(
            user.id, json.dumps([hash_password(c) for c in backup_codes])
        )
        await self._users.update(user.id, totp_enabled=True)
        return backup_codes

    async def disable_2fa(self, user: User, password: str, code: str) -> None:
        if not verify_password(password, user.hashed_password or _DUMMY_PASSWORD_HASH):
            raise AuthenticationError("errors.invalid_password", fallback="Invalid password")

        record = await self._totp_secrets.get_by_user_id(user.id)
        if record is None or record.enabled_at is None:
            raise ConflictError(
                "errors.totp_not_enabled",
                fallback="Two-factor authentication is not enabled",
            )

        if not pyotp.TOTP(record.secret_encrypted).verify(code, valid_window=1):
            raise AuthenticationError(
                "errors.invalid_totp_code", fallback="Invalid two-factor code"
            )

        await self._totp_secrets.disable(user.id)
        await self._users.update(user.id, totp_enabled=False)

    async def confirm_login_2fa(
        self, temp_token: str, code: str, ip: str, user_agent: str
    ) -> tuple[str, str, User]:
        payload = verify_url_token(temp_token, max_age=_TOTP_LOGIN_TOKEN_TTL_SECONDS)
        if payload.get("purpose") != _TOTP_LOGIN_PURPOSE:
            raise AuthenticationError(
                "errors.invalid_totp_session",
                fallback="Invalid or expired two-factor session",
            )

        user = await self._users.get_by_id(UUID(payload["user_id"]))
        if user is None:
            raise AuthenticationError(
                "errors.invalid_totp_session",
                fallback="Invalid or expired two-factor session",
            )

        record = await self._totp_secrets.get_by_user_id(user.id)
        if record is None or record.enabled_at is None:
            raise AuthenticationError(
                "errors.invalid_totp_session",
                fallback="Invalid or expired two-factor session",
            )

        if pyotp.TOTP(record.secret_encrypted).verify(
            code, valid_window=1
        ) or await self._consume_backup_code(record, code):
            return await self._create_session(user, ip, user_agent)

        raise AuthenticationError("errors.invalid_totp_code", fallback="Invalid two-factor code")

    async def _consume_backup_code(self, record: TOTPSecret, code: str) -> bool:
        hashed_codes: list[str] = json.loads(record.backup_codes_hash)
        for hashed in hashed_codes:
            if verify_password(code, hashed):
                hashed_codes.remove(hashed)
                await self._totp_secrets.update_backup_codes(
                    record.user_id, json.dumps(hashed_codes)
                )
                return True
        return False

    async def _enqueue_verification_email(self, user: User, token: str) -> None:
        settings = get_settings()
        await jobs.enqueue_job(
            "send_email",
            to=user.email,
            template="verify_email",
            locale=user.locale,
            context={
                "user_name": user.full_name,
                "verification_url": f"{settings.FRONTEND_URL}/verify-email?token={token}",
            },
        )

    async def _enqueue_password_reset_email(self, user: User, token: str) -> None:
        settings = get_settings()
        await jobs.enqueue_job(
            "send_email",
            to=user.email,
            template="password_reset",
            locale=user.locale,
            context={
                "user_name": user.full_name,
                "reset_url": f"{settings.FRONTEND_URL}/reset-password?token={token}",
            },
        )

    async def _enqueue_magic_link_email(self, user: User, token: str) -> None:
        settings = get_settings()
        await jobs.enqueue_job(
            "send_email",
            to=user.email,
            template="magic_link",
            locale=user.locale,
            context={
                "user_name": user.full_name,
                "login_url": f"{settings.FRONTEND_URL}/magic-link/consume?token={token}",
            },
        )
