"""
Business logic for registration, email verification (T-017), login with
brute-force lockout (T-018), refresh-token rotation with theft detection
(T-019), password reset + magic link (T-020), the authenticated
change-password/OAuth-unlink/security-notification-email flows (T-216), and
WebAuthn/FIDO2 registration + assertion as an alternative second factor
(T-217).
"""

import asyncio
import hmac
import json
import logging
import secrets
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pyotp
import webauthn
from redis.asyncio import Redis
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
from app.core.db import persist_then_reject
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from app.core.module_loader import is_enabled
from app.core.urls import frontend_url
from app.models._mixins import utcnow
from app.modules.audit.service import AuditService
from app.modules.auth import amr as amr_methods
from app.modules.auth.device_label import describe_device
from app.modules.auth.models import OAuthAccount, TOTPSecret, User, WebAuthnCredential
from app.modules.auth.models import Session as SessionModel
from app.modules.auth.repository import (
    REVOKED_EXPIRED,
    REVOKED_ROTATED,
    REVOKED_THEFT,
    EmailChangeTokenRepository,
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
from app.modules.auth.session_rules import is_session_usable
from app.modules.auth.utils import (
    PasswordCheck,
    backup_code_digests,
    check_password,
    create_access_token,
    generate_refresh_token,
    hash_backup_code,
    hash_password,
    hash_token,
    is_legacy_backup_code,
    sign_url_token,
    verify_password,
    verify_url_token,
)
from app.modules.notifications.preferences import normalize
from app.modules.sso import enforcement as sso_enforcement

logger = logging.getLogger(__name__)

_EMAIL_VERIFICATION_TOKEN_TTL = timedelta(hours=24)
_PASSWORD_RESET_TOKEN_TTL = timedelta(hours=1)
_MAGIC_LINK_TOKEN_TTL = timedelta(minutes=15)
_EMAIL_CHANGE_TOKEN_TTL = timedelta(hours=1)
# T-226: per-*target-address* limit, distinct from the per-user/per-IP
# sensitive_limit slowapi already applies to POST /auth/change-email --
# without this, an attacker with a valid session of their own could still
# use the flow as a spam vector against any inbox by repeatedly naming it as
# new_email (each request re-sends the confirm-link email to that address).
_EMAIL_CHANGE_TARGET_LIMIT = 3
_EMAIL_CHANGE_TARGET_WINDOW_SECONDS = 3600
_MAX_FAILED_LOGIN_ATTEMPTS = 10
_LOCKOUT_DURATION = timedelta(minutes=15)
# Hashed once at import time so a login for an unknown email (or an
# OAuth-only account with no password) still pays argon2's full verify
# cost — otherwise the timing difference would itself leak which emails
# are registered.
_DUMMY_PASSWORD_HASH = hash_password(uuid4().hex)


async def _hash_password_off_loop(plain: str) -> str:
    # T-223: argon2's memory-hard params (T-225) make a single hash/verify
    # call take tens of milliseconds of pure CPU time. Calling
    # hash_password/verify_password (app.modules.auth.utils) directly here
    # would block this whole worker's single-threaded event loop for that
    # duration, serializing every other in-flight request behind it --
    # k6's login-burst.js (tests/load/) is what actually surfaced this: 30
    # concurrent logins pushed p(95) past 1.8s even though one hash in
    # isolation only takes ~45ms. Every request-serving call site in this
    # module goes through this (or _verify_password_off_loop) instead of
    # calling utils.py's functions directly -- they stay synchronous so
    # seed scripts and the ~60 test files that construct a User directly
    # don't have to change.
    return await asyncio.to_thread(hash_password, plain)


async def _verify_password_off_loop(plain: str, hashed: str) -> bool:
    return await asyncio.to_thread(verify_password, plain, hashed)


async def _check_password_off_loop(plain: str, hashed: str) -> PasswordCheck:
    """As above, plus whether the stored hash should be rewritten.

    Only the login path uses this. Everywhere else that verifies a password
    is checking something with no row worth updating -- a backup code, or a
    re-authentication before a sensitive action.
    """
    return await asyncio.to_thread(check_password, plain, hashed)


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


def _match_totp_step(secret: str, code: str) -> int | None:
    """Returns the TOTP time-step `code` matches (current step +/- 1, i.e.
    the same tolerance `valid_window=1` gave the old plain `.verify()` call),
    or None if it matches none of them. Unlike `.verify()`, this identifies
    *which* step matched, which is what makes same-step replay detection
    possible below."""
    totp = pyotp.TOTP(secret)
    now = utcnow()
    current_step = totp.timecode(now)
    for offset in (-1, 0, 1):
        if hmac.compare_digest(totp.at(now, offset), code):
            return current_step + offset
    return None


def _session_invalid() -> AuthenticationError:
    return AuthenticationError(
        "errors.session_invalid",
        fallback="Your session is no longer valid. Please log in again.",
    )


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        # The unit of work itself, not only repositories built from it: T-331's
        # enforcement check is a cross-module query this service delegates
        # rather than owns (app.modules.sso.enforcement), and handing it a
        # repository would mean building one here for a table this module has
        # no other business with.
        self._db = session
        self._users = UserRepository(session)
        self._sessions = SessionRepository(session)
        self._email_verification_tokens = EmailVerificationTokenRepository(session)
        self._password_reset_tokens = PasswordResetTokenRepository(session)
        self._magic_link_tokens = MagicLinkTokenRepository(session)
        self._oauth_accounts = OAuthAccountRepository(session)
        self._totp_secrets = TOTPSecretRepository(session)
        self._webauthn_credentials = WebAuthnCredentialRepository(session)
        self._email_change_tokens = EmailChangeTokenRepository(session)
        self._audit = AuditService(session)

    async def users_by_ids(self, user_ids: Collection[UUID]) -> dict[UUID, User]:
        """Live users by id, in one query: for a page of rows that each name a
        person (members, file uploaders). Other modules go through this rather
        than this module's repository (§0.1)."""
        users = await self._users.list_by_ids(user_ids)
        return {user.id: user for user in users}

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
            hashed_password=await _hash_password_off_loop(req.password),
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

        if not await self._email_verification_tokens.consume(record.id):
            # Lost the race to another request presenting the same
            # token. Same answer a replay gets, because that is what
            # it is -- see _TokenRepository.consume.
            raise AuthenticationError(
                "errors.invalid_verification_token",
                fallback="Invalid or expired verification token",
            )
        await self._users.mark_email_verified(record.user_id)
        return user

    async def login(
        self, req: LoginRequest, ip: str, user_agent: str
    ) -> tuple[str, str, User] | TwoFactorChallenge:
        user = await self._users.get_by_email(req.email)
        if user is None:
            await _verify_password_off_loop(req.password, _DUMMY_PASSWORD_HASH)
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

        password_check = await _check_password_off_loop(
            req.password, user.hashed_password or _DUMMY_PASSWORD_HASH
        )
        if not password_check.ok:
            user_id = user.id
            # Persisted despite the 401 it produces: raised the ordinary way,
            # get_session's rollback took the count with it, so the counter
            # never rose and T-018's lockout never engaged over HTTP.
            await persist_then_reject(
                self._db,
                lambda: self._record_failed_login(user_id, ip, user_agent),
                AuthenticationError(
                    "errors.invalid_credentials", fallback="Invalid email or password"
                ),
            )

        await self._users.reset_failed_login(user.id)

        # The one moment the plaintext is available, which is the only moment
        # a password can be re-hashed. True when this password verified under
        # PASSWORD_PEPPER_OLD, or when argon2's cost parameters have been
        # raised since it was stored -- both migrate the same way, one login
        # at a time. Deliberately after the credential is known good and
        # before any of the branches below can return.
        if password_check.needs_rehash and user.hashed_password is not None:
            await self._users.update(
                user.id, hashed_password=await _hash_password_off_loop(req.password)
            )

        # T-331: an organization that enforces SSO does not accept passwords
        # from its members. Checked *after* the password, deliberately -- an
        # enforcement check before it would answer "is this address in an SSO
        # organization" to anyone who can type an email, which is the same
        # leak `domain_hint_enabled` exists to make opt-in. It costs nothing
        # (not even a query) when `enterprise_sso` is off.
        blocked = await sso_enforcement.password_login_blocked(self._db, user.id)
        if blocked is not None:
            user_id = user.id

            async def _record_refusal() -> None:
                await self._audit.log(
                    action="user.password_login_refused_sso",
                    resource_type="user",
                    resource_id=str(user_id),
                    org_id=None,
                    actor_id=user_id,
                    ip=ip,
                    ua=user_agent,
                )

            # The refusal is recorded; the failed-login reset and any rehash
            # above are not -- this request did not sign anybody in.
            await persist_then_reject(
                self._db,
                _record_refusal,
                PermissionDeniedError(
                    "errors.sso_required",
                    fallback=(
                        f"Your organization signs in with {blocked.provider_label}. "
                        "Use single sign-on instead of a password."
                    ),
                    params={
                        "provider": blocked.provider_label,
                        "org_slug": blocked.org_slug,
                    },
                ),
            )

        challenge = await self._second_factor_challenge(user, first_factor=amr_methods.PASSWORD)
        if challenge is not None:
            return challenge

        result = await self._create_session(user, ip, user_agent, amr=[amr_methods.PASSWORD])
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

    async def _second_factor_challenge(
        self, user: User, *, first_factor: str
    ) -> TwoFactorChallenge | None:
        """The one place any sign-in decides whether a second factor is owed.

        None means "issue a session"; a challenge means the caller must
        return it instead, and the holder trades it for a code at
        /auth/2fa/login or a security-key assertion at
        /auth/webauthn/login/options+/login.

        Every entry point consults this -- password, magic link, social
        OAuth, enterprise SSO -- and that is the point. They used to decide
        separately, and three of the four decided wrong:

        - magic link and social OAuth called `_create_session` directly, so
          an account with TOTP or a security key enrolled could be signed
          into with neither. Possession of an inbox, or of a Google session,
          was enough.
        - SSO checked `totp_enabled` alone, so a user whose only second
          factor was a security key was never challenged.

        The social case was a deliberate decision once, on the reasoning that
        for Google or GitHub "the account *is* the factor". That is a claim
        about the provider, and it is not ours to make on the user's behalf:
        someone who enrolled a second factor here asked for it to be checked
        here. If we want to accept the provider's own MFA we should read its
        `amr` claim and say so, rather than assume it.

        §T-217: a registered security key is an alternative to TOTP at this
        gate, not an additional factor -- a user with only WebAuthn still
        clears it, via a different endpoint.
        """
        # Short-circuited rather than `totp_enabled or exists_for_user(...)`:
        # the answer is already yes, and this runs on every sign-in.
        if not user.totp_enabled and not await self._webauthn_credentials.exists_for_user(user.id):
            return None
        temp_token = sign_url_token(
            # "amr": the first factor already passed, so the session the
            # challenge eventually issues records both (review R3).
            {"user_id": str(user.id), "purpose": _TOTP_LOGIN_PURPOSE, "amr": first_factor},
            _TOTP_LOGIN_TOKEN_TTL_SECONDS,
        )
        return TwoFactorChallenge(temp_token=temp_token)

    async def _create_session(
        self, user: User, ip: str, user_agent: str, *, amr: Sequence[str]
    ) -> tuple[str, str, User]:
        settings = get_settings()
        now = utcnow()
        refresh_plain, refresh_hash = generate_refresh_token()
        # The session is created *before* the access token, not after, because
        # the token now carries that session's id -- see AccessTokenPayload.sid.
        session = await self._sessions.create(
            user_id=user.id,
            token_hash=refresh_hash,
            device_label=describe_device(user_agent),
            ip_address=ip,
            user_agent=user_agent,
            expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            auth_time=now,
            amr=amr,
        )
        access_token = create_access_token(user.id, None, (), auth_time=now, sid=session.id)
        return access_token, refresh_plain, user

    async def refresh(self, refresh_token_plain: str, ip: str, user_agent: str) -> tuple[str, str]:
        """T-019's theft detection, plus T-218's periodic re-authentication
        (ASVS V3.3.2): a token can be rejected for three distinct reasons,
        only one of which is a theft signal --

        - `revoked_at` set: this exact token was already rotated away (or
          explicitly logged out) and is being replayed -- the signature of a
          stolen refresh token, so every session the user has gets revoked.
        - absolute lifetime (`expires_at`, anchored to the session chain's
          original `auth_time` and never extended by rotation) exceeded:
          ordinary aging, not an attack -- just this one chain is rejected,
          asking the user to log in again.
        - idle timeout (`last_seen_at` older than
          REFRESH_TOKEN_IDLE_TIMEOUT_MINUTES) exceeded: same as above, ordinary
          inactivity, not an attack.
        """
        session = await self._sessions.get_by_token_hash(hash_token(refresh_token_plain))

        if session is None:
            raise _session_invalid()

        # Captured before any rejection below: persist_then_reject rolls
        # back first, which expires `session`.
        session_id = session.id
        user_id = session.user_id

        if session.revoked_at is not None and session.revoked_reason in {REVOKED_ROTATED, None}:
            # Rotated away and presented again: the one legitimate holder
            # moved on, so this is a copy. (NULL: revoked before reasons were
            # recorded, when every revocation was treated this way -- kept
            # conservative for those rows.)
            await persist_then_reject(
                self._db,
                lambda: self._contain_refresh_theft(user_id, session_id, ip, user_agent),
                _session_invalid(),
            )

        if session.revoked_at is not None:
            # Revoked on purpose -- signed out, password changed, second
            # factor enrolled. The device holding it will try it once more;
            # that is not theft, and treating it as theft signed the user out
            # of the very session that did the revoking.
            raise _session_invalid()

        # Same rule the session list and the active-sessions gauge apply, from
        # one definition (session_rules) -- they disagreed until 2026-09-21,
        # and the list was the one that was wrong.
        if not is_session_usable(session):
            await persist_then_reject(
                self._db,
                lambda: self._sessions.revoke(session_id, reason=REVOKED_EXPIRED),
                _session_invalid(),
            )

        if not await self._sessions.revoke_if_active(session_id):
            # Another request rotated this same session between the check
            # above and here. One refresh token, two successors -- which is
            # either a client racing itself or the theft this detector is
            # for, and there is no way to tell them apart from here. Treat it
            # as theft, exactly as a presented-after-revocation token is.
            await persist_then_reject(
                self._db,
                lambda: self._contain_refresh_theft(user_id, session_id, ip, user_agent),
                _session_invalid(),
            )

        refresh_plain, refresh_hash = generate_refresh_token()
        # As in _create_session: the successor session has to exist before the
        # token that names it. A rotation mints a token bound to the *new*
        # session, so revoking the old one does not take the fresh token with
        # it.
        rotated = await self._sessions.create(
            user_id=session.user_id,
            token_hash=refresh_hash,
            # The chain keeps the identity it was created with -- a rotation
            # is the same device, and re-deriving on every refresh would let
            # a label drift on a browser upgrade. `or` only to heal the
            # sessions that predate device_label being populated at all;
            # without it they stay "Unknown device" for their whole life.
            device_label=session.device_label or describe_device(user_agent),
            ip_address=ip,
            user_agent=user_agent,
            # Absolute cap stays anchored to the chain's original auth_time
            # rather than sliding forward on every rotation -- otherwise an
            # actively-used session would never hit the periodic-re-auth
            # target ASVS V3.3.2 asks for.
            expires_at=session.expires_at,
            auth_time=session.auth_time,
            # Like auth_time: a rotation proves nothing new (review R3).
            amr=session.amr,
        )
        access_token = create_access_token(
            session.user_id, None, (), auth_time=session.auth_time, sid=rotated.id
        )

        return access_token, refresh_plain

    async def _record_failed_login(self, user_id: UUID, ip: str, user_agent: str) -> None:
        """One wrong password: count it, and lock the account at the limit.

        Always run through persist_then_reject -- see login().
        """
        count = await self._users.increment_failed_login(user_id)
        if count >= _MAX_FAILED_LOGIN_ATTEMPTS:
            await self._users.set_lockout(user_id, utcnow() + _LOCKOUT_DURATION)
            await self._users.reset_failed_login(user_id)
            # T-219/ASVS V7.2.1: the counter increment above is state, not a
            # discrete security event -- lockout is the high-severity one (an
            # active brute-force attempt), so it gets its own audit action
            # rather than folding into user.login_failed.
            await self._audit.log(
                action="user.account_locked",
                resource_type="user",
                resource_id=str(user_id),
                org_id=None,
                actor_id=user_id,
                ip=ip,
                ua=user_agent,
                severity="elevated",
            )
            return
        await self._audit.log(
            action="user.login_failed",
            resource_type="user",
            resource_id=str(user_id),
            org_id=None,
            actor_id=user_id,
            ip=ip,
            ua=user_agent,
        )

    async def _contain_refresh_theft(
        self, user_id: UUID, session_id: UUID, ip: str, user_agent: str
    ) -> None:
        """T-019's response to a refresh token presented twice: every session
        the user holds goes, including the successor a thief would be using.

        Always run through persist_then_reject. Raised the ordinary way, the
        rejection rolled this back and the successor kept working.
        """
        await self._sessions.revoke_all_for_user(user_id, reason=REVOKED_THEFT)
        await self._audit.log(
            action="user.refresh_token_reuse",
            resource_type="session",
            resource_id=str(session_id),
            org_id=None,
            actor_id=user_id,
            ip=ip,
            ua=user_agent,
            severity="elevated",
        )

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

    async def confirm_password_reset(self, token: str, new_password: str) -> User:
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

        if not await self._password_reset_tokens.consume(record.id):
            # Lost the race to another request presenting the same
            # token. Same answer a replay gets, because that is what
            # it is -- see _TokenRepository.consume.
            raise AuthenticationError(
                "errors.invalid_reset_token", fallback="Invalid or expired reset token"
            )
        await self._users.update(
            user.id, hashed_password=await _hash_password_off_loop(new_password)
        )
        # A password reset is treated the same as a confirmed compromise of
        # every existing session -- whoever could read the reset email now
        # controls the account, so anyone already logged in elsewhere (e.g.
        # the actual attacker) gets signed out.
        await self._sessions.revoke_all_for_user(user.id)
        # Returned so the router can bust the user cache (T-240): the cached
        # row carries hashed_password, and change_password verifies the
        # caller's current password against exactly that copy.
        return user

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
    ) -> tuple[str, str, User] | TwoFactorChallenge:
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

        if not await self._magic_link_tokens.consume(record.id):
            # Lost the race to another request presenting the same
            # token. Same answer a replay gets, because that is what
            # it is -- see _TokenRepository.consume.
            raise AuthenticationError(
                "errors.invalid_magic_link", fallback="Invalid or expired magic link"
            )

        # The token is spent either way: it proved control of the inbox, and
        # that is all it was ever good for. Whether that is enough to issue a
        # session is _second_factor_challenge's call, not this method's.
        challenge = await self._second_factor_challenge(user, first_factor=amr_methods.MAGIC_LINK)
        if challenge is not None:
            return challenge

        return await self._create_session(user, ip, user_agent, amr=[amr_methods.MAGIC_LINK])

    async def oauth_login(
        self, provider: str, profile: OAuthProfile, ip: str, user_agent: str
    ) -> tuple[str, str, User] | TwoFactorChallenge:
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

        challenge = await self._second_factor_challenge(user, first_factor=amr_methods.SOCIAL_OAUTH)
        if challenge is not None:
            return challenge

        return await self._create_session(user, ip, user_agent, amr=[amr_methods.SOCIAL_OAUTH])

    async def sso_login(  # noqa: PLR0913 -- the identity's four parts plus ip/ua
        self,
        user: User,
        ip: str,
        user_agent: str,
        *,
        provider: str,
        provider_user_id: str,
        mfa_asserted: bool,
    ) -> tuple[str, str, User] | TwoFactorChallenge:
        """Issue a session for an identity an IdP has already proven (T-331).

        The caller (`app.modules.sso.service.SsoLoginService`) owns the
        protocol and has verified the ID token's signature, issuer, audience,
        expiry and nonce, and that the email is in the connection's own
        domain. This method owns everything that makes it a *session in this
        product*: the account link, the refresh-token chain, `auth_time`, the
        audit event, and the second-factor decision.

        **It is deliberately not `oauth_login`.** Two differences, and both
        are T-331 asking for something the social path does not do:

        - TOTP still applies unless the IdP said it checked a second factor.
          `oauth_login` skips the challenge entirely, which is defensible for
          Google or GitHub (the account *is* the factor) and is not what an
          enterprise wants from an IdP that may or may not enforce MFA.
        - the link is scoped to a *connection*, so the same person at two
          organizations is two links and neither one lets the other's IdP
          sign them in.
        """
        link = await self._oauth_accounts.get_by_provider_account(provider, provider_user_id)
        if link is None:
            await self._oauth_accounts.create(
                user_id=user.id, provider=provider, provider_user_id=provider_user_id
            )
        elif link.user_id != user.id:
            # The IdP's `sub` is stable and belongs to one person. Seeing it
            # against a different local account means the two have been
            # merged or re-pointed somewhere upstream, and guessing which is
            # right is how an account takeover gets automated.
            raise AuthenticationError(
                "errors.sso_subject_conflict",
                fallback="That identity is already linked to a different account",
            )

        await self._audit.log(
            action="user.sso_login",
            resource_type="user",
            resource_id=str(user.id),
            org_id=None,
            actor_id=user.id,
            ip=ip,
            ua=user_agent,
        )

        if not mfa_asserted:
            # The IdP has proven who they are, which is not yet enough to
            # issue real tokens for an account whose owner asked for a second
            # factor. `mfa_asserted` is the one thing that skips it, and it
            # comes from the IdP's own amr/acr claim -- an upstream factor we
            # were actually told about, rather than one we assumed.
            #
            # This used to test `user.totp_enabled` directly, which meant a
            # user whose only second factor was a security key was never
            # challenged here. Going through the shared helper is what keeps
            # the four sign-in paths from drifting apart again.
            challenge = await self._second_factor_challenge(
                user, first_factor=amr_methods.ENTERPRISE_SSO
            )
            if challenge is not None:
                return challenge

        first = [amr_methods.ENTERPRISE_SSO]
        return await self._create_session(
            user, ip, user_agent, amr=[*first, amr_methods.IDP_MFA] if mfa_asserted else first
        )

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
        current_refresh_token_plain: str | None,
    ) -> None:
        if not await _verify_password_off_loop(
            current_password, user.hashed_password or _DUMMY_PASSWORD_HASH
        ):
            raise AuthenticationError("errors.invalid_password", fallback="Invalid password")

        await self._users.update(
            user.id, hashed_password=await _hash_password_off_loop(new_password)
        )

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

    async def request_email_change(  # noqa: PLR0913 — see SessionRepository.create's precedent
        self,
        user: User,
        new_email: str,
        current_password: str | None,
        ip: str,
        user_agent: str,
        redis: Redis,
    ) -> None:
        """T-226: production-ready follow-up to T-218's minimal version.
        Enumeration-safe the same way request_password_reset() is: if
        `new_email` already belongs to a *different* user, this silently
        no-ops rather than raising a conflict, so the response an
        authenticated caller sees doesn't leak whether an arbitrary address
        is registered to someone else.
        """
        if new_email == user.email:
            raise ConflictError(
                "errors.email_unchanged", fallback="This is already your current email address"
            )

        # ASVS V2.5.5-adjacent: require_recent_auth (the router's dependency)
        # only proves the session itself was established/refreshed recently
        # -- it doesn't re-prove the caller still has the account's own
        # credential in hand. An OAuth-only user (§T-023, hashed_password is
        # None) has no password to re-prove, so require_recent_auth's session
        # freshness is the whole story for them, same as it is today.
        if user.hashed_password is not None and not await _verify_password_off_loop(
            current_password or "", user.hashed_password
        ):
            raise AuthenticationError("errors.invalid_password", fallback="Invalid password")

        # Applies regardless of the enumeration-safe no-op below -- the
        # inbox being spammed doesn't have to belong to a registered user.
        await self._check_email_change_target_rate_limit(redis, new_email)

        existing = await self._users.get_by_email(new_email)
        if existing is not None:
            logger.warning(
                "auth.request_email_change: target address already registered "
                "(user_id=%s, target_user_id=%s)",
                user.id,
                existing.id,
            )
            return

        plain_token, token_hash = generate_refresh_token()
        cancel_plain_token, cancel_token_hash = generate_refresh_token()
        await self._email_change_tokens.create(
            user_id=user.id,
            new_email=new_email,
            token_hash=token_hash,
            cancel_token_hash=cancel_token_hash,
            expires_at=utcnow() + _EMAIL_CHANGE_TOKEN_TTL,
            requested_ip=ip,
            requested_user_agent=user_agent,
        )
        await jobs.enqueue_job(
            "send_email",
            to=new_email,
            template="change_email",
            locale=user.locale,
            context={
                "user_name": user.full_name,
                "confirm_url": frontend_url(
                    f"/confirm-email-change?token={plain_token}", user.locale
                ),
            },
        )
        # Notifies the *current* address too (ASVS V2.5.5) -- so a real
        # account owner finds out about a change they didn't request even
        # before it's confirmed, not just after. Carries a cancel_url (T-226)
        # so that owner can kill the request themselves, not just be told
        # about it.
        await self._enqueue_security_notification_email(
            user,
            "email_change_requested",
            new_email=new_email,
            cancel_url=frontend_url(
                f"/cancel-email-change?token={cancel_plain_token}", user.locale
            ),
        )

    async def cancel_email_change(self, cancel_token: str) -> None:
        """T-226: the grace/undo window a real account owner reaches from the
        email_change_requested notification's cancel link -- lets them stop a
        request they didn't make before the (separate) confirm link mailed to
        new_email is ever clicked. Public/token-only, like confirm_email_change,
        since the owner clicking this may not have a valid session at all if
        the request came from a compromised credential.
        """
        record = await self._email_change_tokens.get_by_cancel_hash(hash_token(cancel_token))
        if record is None:
            raise AuthenticationError(
                "errors.invalid_email_change_cancel_token",
                fallback="This cancel link is invalid or has expired",
            )

        user = await self._users.get_by_id(record.user_id)
        if user is None:
            raise AuthenticationError(
                "errors.invalid_email_change_cancel_token",
                fallback="This cancel link is invalid or has expired",
            )

        await self._email_change_tokens.mark_cancelled(record.id)
        await self._enqueue_security_notification_email(
            user, "email_change_cancelled", new_email=record.new_email
        )
        logger.info("auth.cancel_email_change: pending change cancelled (user_id=%s)", user.id)

    async def confirm_email_change(self, token: str) -> User:
        record = await self._email_change_tokens.get_by_hash(hash_token(token))
        if record is None:
            raise AuthenticationError(
                "errors.invalid_email_change_token",
                fallback="This email change link is invalid or has expired",
            )

        user = await self._users.get_by_id(record.user_id)
        if user is None:
            raise AuthenticationError(
                "errors.invalid_email_change_token",
                fallback="This email change link is invalid or has expired",
            )

        # Re-checked at confirm time, not just at request time -- the target
        # address could have been claimed by someone else in the interim.
        existing = await self._users.get_by_email(record.new_email)
        if existing is not None and existing.id != user.id:
            raise ConflictError(
                "errors.conflict", fallback="This email address is no longer available"
            )

        if not await self._email_change_tokens.consume(record.id):
            # Lost the race to another request presenting the same
            # token. Same answer a replay gets, because that is what
            # it is -- see _TokenRepository.consume.
            raise AuthenticationError(
                "errors.invalid_email_change_token",
                fallback="This email change link is invalid or has expired",
            )
        old_email = user.email
        # Sent to the *old* address before the mutation below -- the User
        # object this method holds and the one UserRepository.update()
        # fetches are the same SQLAlchemy identity-mapped instance, so
        # user.email would already read as the *new* address afterward.
        await self._enqueue_security_notification_email(
            user, "email_changed", new_email=record.new_email
        )
        # T-226: email_verified_at now reflects the *new* address -- this
        # confirm link is itself proof of control over it, the same proof
        # verify_email()/mark_email_verified() rely on for a brand-new
        # signup. Pending Invitation rows are untouched deliberately:
        # Invitation.email is never matched against User.email (see
        # TenancyService.accept_invitation, which trusts the caller's
        # session, not an email match), so a change here can't invalidate or
        # misdirect one.
        updated = await self._users.update(
            user.id, email=record.new_email, email_verified_at=utcnow()
        )
        # No authenticated session to spare the way change_password's request
        # has -- this confirm link is clicked from wherever, possibly with no
        # session at all -- so every existing session is revoked
        # unconditionally, same reasoning as confirm_password_reset.
        await self._sessions.revoke_all_for_user(user.id)
        logger.info(
            "auth.confirm_email_change: email changed (user_id=%s, old_email=%s)",
            user.id,
            old_email,
        )
        return updated

    async def _check_email_change_target_rate_limit(self, redis: Redis, new_email: str) -> None:
        key = f"auth:email_change:target:{new_email.lower()}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, _EMAIL_CHANGE_TARGET_WINDOW_SECONDS)
        if count > _EMAIL_CHANGE_TARGET_LIMIT:
            raise RateLimitError(
                "errors.email_change_target_rate_limited",
                fallback=(
                    "Too many change-email requests for this address recently. "
                    "Please try again later."
                ),
            )

    async def logout(self, refresh_token_plain: str) -> None:
        session = await self._sessions.get_by_token_hash(hash_token(refresh_token_plain))
        if session is not None:
            await self._sessions.revoke(session.id)

    async def logout_all(self, user_id: UUID) -> None:
        await self._sessions.revoke_all_for_user(user_id)

    async def list_sessions(self, user_id: UUID) -> list[SessionModel]:
        return await self._sessions.list_active_for_user(user_id)

    async def resolve_avatar_change(self, user_id: UUID, file_id: UUID | None) -> str | None:
        """Translate a client-supplied file id into the key to store.

        None means "clear the avatar" and needs no lookup. Anything else is
        checked against the caller: the file has to exist, be ready, live in
        the avatars bucket, and have been uploaded by *them*. Skipping that
        would let anyone set their avatar to any file id in the system and
        then have GET /auth/me/avatar presign it -- the bucket is private, so
        this endpoint is the only thing standing in front of it.

        Raises UnprocessableEntityError rather than saying which check failed:
        the distinctions would confirm the existence of other people's files.
        """
        if file_id is None:
            return None

        # file_storage is optional and nothing requires it, so `stored_file`
        # does not exist in every configuration -- querying it there would be
        # a 500 rather than a refusal. There is also no way to have uploaded
        # anything, so any id the client offers is invalid by definition.
        if not is_enabled("file_storage"):
            raise UnprocessableEntityError(
                "errors.avatar_file_invalid",
                fallback="That file cannot be used as an avatar.",
            )

        # Lazy, like app.core.resilience's import of the scanner: file_storage
        # is optional, and auth must still import when it is off.
        from app.modules.storage.avatars import (  # noqa: PLC0415
            resolve_own_avatar_key,
        )

        key = await resolve_own_avatar_key(self._db, user_id=user_id, file_id=file_id)
        if key is None:
            raise UnprocessableEntityError(
                "errors.avatar_file_invalid",
                fallback="That file cannot be used as an avatar.",
            )
        return key

    async def update_profile(self, user_id: UUID, changes: Mapping[str, Any]) -> User:
        """Apply exactly the fields PATCH /me was sent, and no others.

        `changes` comes from model_dump(exclude_unset=True), so an omitted key
        is absent here rather than present-and-None -- which is what lets
        `{"full_name": null}` clear a name while `{"locale": "de"}` leaves it
        alone. Writes go through UserRepository so full_name passes through
        EncryptedString on the way to the column.
        """
        return await self._users.update(user_id, **dict(changes))

    async def update_notification_preferences(
        self, user_id: UUID, preferences: dict[str, dict[str, bool]]
    ) -> User:
        # T-103: the only write path to this column -- NotificationService
        # (a different module) never reaches into UserRepository directly
        # per §0.1, so its read path (channels_for()) and this write each go
        # through their own module's service instead of sharing a repo.
        #
        # T-234: normalize() forces the channels a type can't opt out of back
        # on, so the stored matrix is the one channels_for() will act on --
        # and so the settings page, which renders straight from this column,
        # can't show a required channel as off.
        return await self._users.update(user_id, notification_preferences=normalize(preferences))

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

    async def verify_2fa(
        self, user: User, code: str, *, current_session_id: UUID | None = None
    ) -> list[str]:
        """Activates 2FA and returns the plaintext backup codes -- shown once.

        `current_session_id` is the session making the request: it has just
        proved the factor, so it is the one session that keeps working (see
        _second_factor_enrolled).
        """
        record = await self._totp_secrets.get_by_user_id(user.id)
        if record is None or record.enabled_at is not None:
            raise ConflictError(
                "errors.totp_setup_not_pending",
                fallback="No pending two-factor setup to verify",
            )

        if not await self._verify_totp_and_track_replay(user, record, code):
            raise AuthenticationError(
                "errors.invalid_totp_code", fallback="Invalid two-factor code"
            )

        backup_codes = [secrets.token_hex(5) for _ in range(_BACKUP_CODE_COUNT)]
        hashed_backup_codes = [hash_backup_code(c) for c in backup_codes]
        had_second_factor = await self._webauthn_credentials.exists_for_user(user.id)
        await self._totp_secrets.mark_enabled(user.id, json.dumps(hashed_backup_codes))
        await self._users.update(user.id, totp_enabled=True)
        await self._second_factor_enrolled(
            user.id,
            current_session_id,
            amr_methods.TOTP,
            raises_the_bar=not had_second_factor,
        )
        await self._enqueue_security_notification_email(user, "totp_enabled")
        return backup_codes

    async def _second_factor_enrolled(
        self,
        user_id: UUID,
        current_session_id: UUID | None,
        method: str,
        *,
        raises_the_bar: bool,
    ) -> None:
        """Review R3: what enrolling a factor does to the sessions that exist.

        The session making the request has just proved the factor (a valid
        TOTP code, or a completed registration ceremony), so it records it.

        If this is the account's *first* second factor, every other session
        was opened below the bar the account now sets, and is revoked. Left
        alone, a password-only session from before enrollment -- on a stolen
        laptop, say -- carried on, and passed require_superadmin, which
        checked enrollment rather than the session. Someone who turns on 2FA
        because they suspect a compromise expects the other devices signed
        out, and this is what does it.
        """
        if current_session_id is not None:
            await self._sessions.add_amr(current_session_id, method)
        if raises_the_bar:
            await self._sessions.revoke_all_for_user(user_id, except_id=current_session_id)

    async def disable_2fa(self, user: User, password: str, code: str) -> None:
        if not await _verify_password_off_loop(
            password, user.hashed_password or _DUMMY_PASSWORD_HASH
        ):
            raise AuthenticationError("errors.invalid_password", fallback="Invalid password")

        record = await self._totp_secrets.get_by_user_id(user.id)
        if record is None or record.enabled_at is None:
            raise ConflictError(
                "errors.totp_not_enabled",
                fallback="Two-factor authentication is not enabled",
            )

        if not await self._verify_totp_and_track_replay(user, record, code):
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

        # Legacy challenge tokens (issued before review R3, five-minute TTL)
        # carry no first factor; every issuer of one then was a password or
        # an equivalent, and the second factor is what the admin gate reads.
        first_factor = str(payload.get("amr", amr_methods.PASSWORD))
        if await self._verify_totp_and_track_replay(user, record, code):
            return await self._create_session(
                user, ip, user_agent, amr=[first_factor, amr_methods.TOTP]
            )
        if await self._consume_backup_code(record, code):
            return await self._create_session(
                user, ip, user_agent, amr=[first_factor, amr_methods.BACKUP_CODE]
            )

        raise AuthenticationError("errors.invalid_totp_code", fallback="Invalid two-factor code")

    async def _verify_totp_and_track_replay(
        self, user: User, record: TOTPSecret, code: str
    ) -> bool:
        """T-218/ASVS V2.8.4-V2.8.5: wraps `_match_totp_step` with
        replay tracking -- a code matching a step at or before the last one
        already consumed is rejected even though it's still inside pyotp's
        own +/-1 validity window, and every successful match advances
        `last_consumed_step` so it can't be replayed itself. A rejected
        replay is logged *and* notified (ASVS V2.8.5) -- unlike an ordinary
        wrong code, this specific failure shape means someone possesses a
        code the legitimate user already used, which is a stronger signal
        than a typo.
        """
        step = _match_totp_step(record.secret_encrypted, code)
        if step is None:
            return False
        if record.last_consumed_step is not None and step <= record.last_consumed_step:
            logger.warning(
                "auth.totp: replay rejected (user_id=%s, step=%s, last_consumed_step=%s)",
                record.user_id,
                step,
                record.last_consumed_step,
            )
            await self._enqueue_security_notification_email(user, "totp_replay_rejected")
            return False
        if not await self._totp_secrets.mark_step_consumed(record.user_id, step):
            # Another request consumed this step (or a newer one) between the
            # comparison above and here -- the same replay, arriving
            # concurrently rather than sequentially.
            logger.warning(
                "auth.totp: concurrent replay rejected (user_id=%s, step=%s)",
                record.user_id,
                step,
            )
            await self._enqueue_security_notification_email(user, "totp_replay_rejected")
            return False
        return True

    async def _consume_backup_code(self, record: TOTPSecret, code: str) -> bool:
        original = record.backup_codes_hash
        stored: list[str] = json.loads(original)
        candidates = backup_code_digests(code)
        match = next(
            (s for s in stored if any(hmac.compare_digest(s, c) for c in candidates)), None
        )
        if match is None:
            match = await self._match_legacy_backup_code(stored, code)
        if match is None:
            return False
        stored.remove(match)
        # Compare-and-swap against the exact list we read. Losing means
        # another request spent a code in between, and writing our copy
        # would put theirs back. Refusing is the safe side: the user retries
        # with an unspent code.
        return await self._totp_secrets.swap_backup_codes(
            record.user_id, expected=original, replacement=json.dumps(stored)
        )

    async def _match_legacy_backup_code(self, stored: list[str], code: str) -> str | None:
        """Argon2 codes from before keyed digests (see utils.is_legacy_backup_code).
        Only accounts that still hold some pay the Argon2 cost."""
        for hashed in stored:
            if is_legacy_backup_code(hashed) and await _verify_password_off_loop(code, hashed):
                return hashed
        return None

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
        *,
        current_session_id: UUID | None = None,
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

        had_second_factor = user.totp_enabled or await self._webauthn_credentials.exists_for_user(
            user.id
        )
        record = await self._webauthn_credentials.create(
            user_id=user.id,
            credential_id=bytes_to_base64url(verified.credential_id),
            public_key=bytes_to_base64url(verified.credential_public_key),
            sign_count=verified.sign_count,
            aaguid=verified.aaguid,
            nickname=nickname,
        )
        await self._second_factor_enrolled(
            user.id,
            current_session_id,
            amr_methods.SECURITY_KEY,
            raises_the_bar=not had_second_factor,
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
                # Carried through from the first-factor challenge (review R3).
                "amr": str(payload.get("amr", amr_methods.PASSWORD)),
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
                # Semgrep's logger-credential-disclosure rule fires on the
                # word "credential" in the format string below. What is
                # logged is a user id and `record.id` -- the primary key of
                # the webauthn_credential row -- not the credential itself,
                # which is a public key this code never logs anywhere.
                # Suppressed at the line rather than by disabling the rule,
                # which is worth keeping for what it is actually looking for.
                # nosemgrep
                logger.warning(
                    "auth.webauthn: sign-count regression detected (user_id=%s, credential_id=%s)",
                    user_id,
                    record.id,
                )
                record_id = record.id

                async def _contain_clone() -> None:
                    await self._sessions.revoke_all_for_user(user_id, reason=REVOKED_THEFT)
                    await self._audit.log(
                        action="user.webauthn_clone_detected",
                        resource_type="webauthn_credential",
                        resource_id=str(record_id),
                        org_id=None,
                        actor_id=user_id,
                        ip=ip,
                        ua=user_agent,
                        severity="elevated",
                    )

                # Through persist_then_reject for the same reason as refresh's
                # theft response: raised plainly, the revocation rolled back.
                await persist_then_reject(
                    self._db,
                    _contain_clone,
                    AuthenticationError(
                        "errors.invalid_webauthn_response",
                        fallback="Could not verify security key",
                    ),
                )
            raise AuthenticationError(
                "errors.invalid_webauthn_response", fallback="Could not verify security key"
            ) from exc

        await self._webauthn_credentials.update_sign_count(record.id, verified.new_sign_count)
        first_factor = str(payload.get("amr", amr_methods.PASSWORD))
        return await self._create_session(
            user, ip, user_agent, amr=[first_factor, amr_methods.SECURITY_KEY]
        )

    async def _enqueue_verification_email(self, user: User, token: str) -> None:
        await jobs.enqueue_job(
            "send_email",
            to=user.email,
            template="verify_email",
            locale=user.locale,
            context={
                "user_name": user.full_name,
                "verification_url": frontend_url(f"/verify-email?token={token}", user.locale),
            },
        )

    async def _enqueue_password_reset_email(self, user: User, token: str) -> None:
        await jobs.enqueue_job(
            "send_email",
            to=user.email,
            template="password_reset",
            locale=user.locale,
            context={
                "user_name": user.full_name,
                "reset_url": frontend_url(f"/reset-password?token={token}", user.locale),
            },
        )

    async def _enqueue_magic_link_email(self, user: User, token: str) -> None:
        await jobs.enqueue_job(
            "send_email",
            to=user.email,
            template="magic_link",
            locale=user.locale,
            context={
                "user_name": user.full_name,
                "login_url": frontend_url(f"/magic-link?token={token}", user.locale),
            },
        )

    async def _enqueue_security_notification_email(
        self,
        user: User,
        event: str,
        *,
        provider: str | None = None,
        new_email: str | None = None,
        cancel_url: str | None = None,
    ) -> None:
        # T-216/T-217/T-218/T-226/ASVS V2.2.3, V2.5.5: fired on password
        # change, 2FA enable/disable, OAuth link/unlink, WebAuthn
        # registration, TOTP replay rejection, and email change
        # request/completion/cancellation -- `event` selects the body copy
        # the SecurityNotification template renders (see packages/
        # email-templates/src/templates/SecurityNotification.tsx).
        # Always sent to `user.email` -- callers around an email-change flow
        # must pass a `user` object whose `.email` still reads as whichever
        # address (old or new) is the intended recipient at that point.
        await jobs.enqueue_job(
            "send_email",
            to=user.email,
            template="security_notification",
            locale=user.locale,
            context={
                "user_name": user.full_name,
                "event": event,
                "provider": provider.capitalize() if provider else None,
                "new_email": new_email,
                # Only set for email_change_requested -- lets a real owner
                # cancel a request they didn't make straight from this email
                # (T-226), instead of just being told to go check settings.
                "cancel_url": cancel_url,
                "settings_url": frontend_url("/settings/security", user.locale),
            },
        )
