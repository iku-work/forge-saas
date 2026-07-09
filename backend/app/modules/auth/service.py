"""
Business logic for registration, email verification (T-017), login with
brute-force lockout (T-018), refresh-token rotation with theft detection
(T-019), password reset + magic link (T-020), the authenticated
change-password/OAuth-unlink/security-notification-email flows (T-216), and
WebAuthn/FIDO2 registration + assertion as an alternative second factor
(T-217).
"""

import json
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pyotp
import webauthn
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json_dict
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core import jobs
from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, ConflictError, NotFoundError
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth.models import OAuthAccount, TOTPSecret, User, WebAuthnCredential
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.repository import (
    EmailVerificationTokenRepository,
    MagicLinkTokenRepository,
    OAuthAccountRepository,
    PasswordResetTokenRepository,
    SessionRepository,
    TOTPSecretRepository,
    UserRepository,
    WebAuthnCredentialRepository,
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

# §T-217: hardcoded the same way enable_2fa's issuer_name is -- no dedicated
# Settings field, since this is display-only text a user sees in their
# authenticator's/browser's own UI, not a security boundary.
_WEBAUTHN_RP_NAME = "ForgeSaaS"
_WEBAUTHN_REGISTER_PURPOSE = "webauthn_register"
_WEBAUTHN_LOGIN_PURPOSE = "webauthn_login"
# Registration/assertion ceremonies involve a user physically touching a
# security key -- longer-lived than the TOTP login window since a hardware
# key fumbled on the first try shouldn't force the whole flow to restart.
_WEBAUTHN_CHALLENGE_TTL_SECONDS = 300


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
        self._webauthn_credentials = WebAuthnCredentialRepository(session)
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

        # §T-217: a registered security key is treated as an alternative to
        # TOTP at this same gate, not an additional required factor -- a user
        # with only a WebAuthn credential (no TOTP) still needs to clear this
        # challenge, just via /auth/webauthn/login instead of /auth/2fa/login.
        has_webauthn = await self._webauthn_credentials.exists_for_user(user.id)
        if user.totp_enabled or has_webauthn:
            # Password is correct, but that alone isn't enough to issue real
            # tokens -- temp_token only proves this much and must be traded
            # for a code at /auth/2fa/login (confirm_login_2fa below) or a
            # security-key assertion at /auth/webauthn/login/options+/login.
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
            is_new_user = user is None
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
            if not is_new_user:
                # T-216/ASVS V2.5.5: only a security-relevant event for an
                # account that already existed -- a brand-new signup-via-OAuth
                # has no prior state for this to be a *change* to.
                await self._enqueue_security_notification_email(
                    user, "oauth_linked", provider=provider
                )

        return await self._create_session(user, ip, user_agent)

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
        current_refresh_token_plain: str | None,
    ) -> None:
        if not verify_password(current_password, user.hashed_password or _DUMMY_PASSWORD_HASH):
            raise AuthenticationError("errors.invalid_password", fallback="Invalid password")

        await self._users.update(user.id, hashed_password=hash_password(new_password))

        # Keeps the session this request is authenticated with alive and
        # revokes every other one (ASVS V3.3.4) -- unlike
        # confirm_password_reset's unconditional revoke_all_for_user, this
        # request already proved the caller's identity, so it shouldn't log
        # them out of their own action.
        current_session_id: UUID | None = None
        if current_refresh_token_plain is not None:
            current_session = await self._sessions.get_by_token_hash(
                hash_token(current_refresh_token_plain)
            )
            if current_session is not None:
                current_session_id = current_session.id
        await self._sessions.revoke_all_for_user(user.id, except_id=current_session_id)

        await self._enqueue_security_notification_email(user, "password_change")

    async def list_oauth_accounts(self, user_id: UUID) -> list[OAuthAccount]:
        return await self._oauth_accounts.list_for_user(user_id)

    async def unlink_oauth(self, user: User, provider: str) -> None:
        account = await self._oauth_accounts.get_by_user_and_provider(user.id, provider)
        if account is None:
            raise NotFoundError(
                "errors.oauth_account_not_linked",
                fallback="No linked account for this provider",
            )

        if user.hashed_password is None:
            # An OAuth-only user (§T-023) with exactly one linked provider has
            # no other way to sign back in -- unlinking would permanently
            # lock them out (ASVS V3.5.1 still expects this to be revocable,
            # just not down to zero auth factors).
            remaining = await self._oauth_accounts.list_for_user(user.id)
            if len(remaining) <= 1:
                raise ConflictError(
                    "errors.oauth_last_auth_method",
                    fallback="Can't unlink your only sign-in method. Set a password first.",
                )

        await self._oauth_accounts.delete(account.id)
        await self._enqueue_security_notification_email(user, "oauth_unlinked", provider=provider)

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
        await self._enqueue_security_notification_email(user, "totp_enabled")
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
        await self._enqueue_security_notification_email(user, "totp_disabled")

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

    async def list_webauthn_credentials(self, user_id: UUID) -> list[WebAuthnCredential]:
        return await self._webauthn_credentials.list_for_user(user_id)

    async def begin_webauthn_registration(self, user: User) -> tuple[dict[str, Any], str]:
        existing = await self._webauthn_credentials.list_for_user(user.id)
        settings = get_settings()
        options = webauthn.generate_registration_options(
            rp_id=settings.DOMAIN,
            rp_name=_WEBAUTHN_RP_NAME,
            user_name=user.email,
            user_id=user.id.bytes,
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
                for c in existing
            ],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
        )
        challenge_token = sign_url_token(
            {
                "user_id": str(user.id),
                "purpose": _WEBAUTHN_REGISTER_PURPOSE,
                "challenge": bytes_to_base64url(options.challenge),
            },
            _WEBAUTHN_CHALLENGE_TTL_SECONDS,
        )
        return options_to_json_dict(options), challenge_token

    async def complete_webauthn_registration(
        self,
        user: User,
        challenge_token: str,
        credential: dict[str, Any],
        nickname: str | None,
    ) -> WebAuthnCredential:
        payload = verify_url_token(challenge_token, max_age=_WEBAUTHN_CHALLENGE_TTL_SECONDS)
        if payload.get("purpose") != _WEBAUTHN_REGISTER_PURPOSE or payload.get("user_id") != str(
            user.id
        ):
            raise AuthenticationError(
                "errors.invalid_webauthn_challenge",
                fallback="Invalid or expired WebAuthn challenge",
            )

        settings = get_settings()
        try:
            verified = webauthn.verify_registration_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(payload["challenge"]),
                expected_rp_id=settings.DOMAIN,
                expected_origin=settings.FRONTEND_URL,
            )
        except WebAuthnException as exc:
            raise AuthenticationError(
                "errors.invalid_webauthn_response", fallback="Could not verify security key"
            ) from exc

        record = await self._webauthn_credentials.create(
            user_id=user.id,
            credential_id=bytes_to_base64url(verified.credential_id),
            public_key=bytes_to_base64url(verified.credential_public_key),
            sign_count=verified.sign_count,
            aaguid=verified.aaguid,
            nickname=nickname,
        )
        await self._enqueue_security_notification_email(user, "webauthn_registered")
        return record

    async def revoke_webauthn_credential(self, user_id: UUID, credential_id: UUID) -> None:
        record = await self._webauthn_credentials.get_by_id(credential_id)
        if record is None or record.user_id != user_id:
            # Same shape whether the credential doesn't exist or belongs to
            # someone else -- mirrors revoke_session's reasoning above.
            raise NotFoundError(
                "errors.webauthn_credential_not_found", fallback="Security key not found"
            )
        await self._webauthn_credentials.delete(record.id)
        user = await self._users.get_by_id(user_id)
        if user is not None:
            await self._enqueue_security_notification_email(user, "webauthn_removed")

    async def begin_webauthn_login(self, temp_token: str) -> tuple[dict[str, Any], str]:
        payload = verify_url_token(temp_token, max_age=_TOTP_LOGIN_TOKEN_TTL_SECONDS)
        if payload.get("purpose") != _TOTP_LOGIN_PURPOSE:
            raise AuthenticationError(
                "errors.invalid_totp_session",
                fallback="Invalid or expired two-factor session",
            )

        user_id = UUID(payload["user_id"])
        credentials = await self._webauthn_credentials.list_for_user(user_id)
        if not credentials:
            raise AuthenticationError(
                "errors.webauthn_not_registered", fallback="No security key registered"
            )

        settings = get_settings()
        options = webauthn.generate_authentication_options(
            rp_id=settings.DOMAIN,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
                for c in credentials
            ],
            user_verification=UserVerificationRequirement.PREFERRED,
        )
        challenge_token = sign_url_token(
            {
                "user_id": str(user_id),
                "purpose": _WEBAUTHN_LOGIN_PURPOSE,
                "challenge": bytes_to_base64url(options.challenge),
            },
            _WEBAUTHN_CHALLENGE_TTL_SECONDS,
        )
        return options_to_json_dict(options), challenge_token

    async def complete_webauthn_login(
        self, challenge_token: str, credential: dict[str, Any], ip: str, user_agent: str
    ) -> tuple[str, str, User]:
        payload = verify_url_token(challenge_token, max_age=_WEBAUTHN_CHALLENGE_TTL_SECONDS)
        if payload.get("purpose") != _WEBAUTHN_LOGIN_PURPOSE:
            raise AuthenticationError(
                "errors.invalid_webauthn_challenge",
                fallback="Invalid or expired WebAuthn challenge",
            )

        user_id = UUID(payload["user_id"])
        user = await self._users.get_by_id(user_id)
        record = await self._webauthn_credentials.get_by_credential_id(credential.get("id", ""))
        if user is None or record is None or record.user_id != user_id:
            raise AuthenticationError(
                "errors.invalid_webauthn_response", fallback="Could not verify security key"
            )

        settings = get_settings()
        try:
            verified = webauthn.verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(payload["challenge"]),
                expected_rp_id=settings.DOMAIN,
                expected_origin=settings.FRONTEND_URL,
                credential_public_key=base64url_to_bytes(record.public_key),
                credential_current_sign_count=record.sign_count,
            )
        except WebAuthnException as exc:
            if "sign count" in str(exc):
                # Clone-detection, same spirit as refresh-token reuse
                # detection in refresh() (T-019): a counter that goes
                # backwards means this credential was cloned, so every
                # session gets revoked, not just this login attempt.
                logger.warning(
                    "auth.webauthn: sign-count regression detected (user_id=%s, credential_id=%s)",
                    user_id,
                    record.id,
                )
                await self._sessions.revoke_all_for_user(user_id)
            raise AuthenticationError(
                "errors.invalid_webauthn_response", fallback="Could not verify security key"
            ) from exc

        await self._webauthn_credentials.update_sign_count(record.id, verified.new_sign_count)
        return await self._create_session(user, ip, user_agent)

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

    async def _enqueue_security_notification_email(
        self, user: User, event: str, *, provider: str | None = None
    ) -> None:
        # T-216/T-217/ASVS V2.2.3, V2.5.5: fired on password change, 2FA
        # enable/disable, OAuth link/unlink, and WebAuthn registration --
        # `event` selects the body copy the SecurityNotification template
        # renders (see
        # packages/email-templates/src/templates/SecurityNotification.tsx).
        settings = get_settings()
        await jobs.enqueue_job(
            "send_email",
            to=user.email,
            template="security_notification",
            locale=user.locale,
            context={
                "user_name": user.full_name,
                "event": event,
                "provider": provider.capitalize() if provider else None,
                "settings_url": f"{settings.FRONTEND_URL}/settings/security",
            },
        )
