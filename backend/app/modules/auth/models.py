from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.core.encrypted_type import EncryptedString, RedactedReprMixin
from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin

# Every datetime column declared directly in this file (i.e. not inherited
# from TimestampMixin) must use the same tz-aware type as the mixin — see
# app.models._mixins for why a naive TIMESTAMP column breaks asyncpg binds.
_TZ_DATETIME = DateTime(timezone=True)


class User(RedactedReprMixin, UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "user"
    # T-219/ASVS V6.1.1: full_name is the highest-value plaintext PII column
    # left after TOTPSecret.secret_encrypted -- see docs/security/
    # data-classification.md for why email itself stays plaintext (it's a
    # unique, indexed lookup key; EncryptedString's own docstring says
    # filtering on an encrypted column isn't supported).
    __redacted_fields__: ClassVar[set[str]] = {"full_name"}

    email: str = Field(unique=True, index=True)
    email_verified_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    hashed_password: str | None = Field(default=None)  # null for OAuth-only users
    full_name: str | None = Field(default=None, sa_column=Column(EncryptedString, nullable=True))
    # Set by an OAuth provider (AuthService's oauth flow copies `picture`),
    # so it is an absolute URL on someone else's domain.
    avatar_url: str | None = Field(default=None)
    # An avatar the user uploaded, as the private object key it lives at --
    # the bucket is not public-read, so there is no URL to store. Resolved to
    # a short-lived presigned URL by GET /auth/me/avatar, and preferred over
    # avatar_url above: an upload is a deliberate choice, an OAuth picture is
    # a default.
    #
    # Deliberately NOT a foreign key to stored_file.id. `file_storage` is an
    # optional module with nothing depending on it, so that table does not
    # exist in every configuration and a constraint would fail to create in
    # the minimum-surface one. The cost is a key that can outlive its file,
    # which shows up as an avatar that stops loading -- see
    # AuthService.set_avatar for what is validated at write time instead.
    avatar_object_key: str | None = Field(default=None)

    @property
    def has_uploaded_avatar(self) -> bool:
        """What UserPublic exposes instead of the key or a URL.

        A plain property rather than a column: it is derived, and the key
        itself must not leave the server -- a client holding one could ask for
        it to be presigned.
        """
        return self.avatar_object_key is not None

    is_active: bool = Field(default=True)
    is_superadmin: bool = Field(default=False)
    totp_enabled: bool = Field(default=False)
    locale: str = Field(default="en")
    # F-004. "system" means "follow prefers-color-scheme", and is the default
    # because a first-time user's OS preference is a better guess than either
    # fixed choice -- and because it is the only value that keeps honouring
    # the OS when the user changes it later.
    #
    # A plain string column rather than a native enum, matching `locale`
    # directly above: the set of values is owned by the API schema
    # (UpdateMeRequest._check_theme), which can add one without a migration.
    # An ALTER TYPE would be the wrong unit of change for a UI preference.
    theme: str = Field(default="system")
    failed_login_count: int = Field(default=0)
    locked_until: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    # Maps an event name to enabled channels, e.g. member_invited -> email/in_app/push.
    notification_preferences: dict[str, dict[str, bool]] = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False, default=dict)
    )


class Session(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    __tablename__ = "session"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    refresh_token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    device_label: str | None = Field(default=None)
    ip_address: str
    user_agent: str
    last_seen_at: datetime = Field(sa_type=_TZ_DATETIME)
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)
    revoked_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    # Why revoked_at was set -- one of the REVOKED_* constants in
    # app.modules.auth.repository. refresh() treats a revoked token as stolen
    # only when rotation revoked it: a token revoked by logout, a password
    # change or a 2FA enrollment is simply dead, and presenting it again is
    # what the device it was on does next, not an attack. Before this column
    # every revocation looked like rotation, so the first stale refresh from
    # a signed-out device signed the user out everywhere else too.
    revoked_reason: str | None = Field(default=None)
    # T-218: the instant of the *real* credential check (password login, 2FA
    # confirmation, magic link, or WebAuthn assertion) that started this
    # session chain -- copied unchanged across every refresh()-driven
    # rotation, unlike expires_at/last_seen_at which each rotation updates.
    # This is what require_recent_auth (app.core.dependencies) reads via the
    # access token's "auth_time" claim to gate step-up-protected actions,
    # since a silent refresh must never itself count as "recent auth".
    auth_time: datetime = Field(sa_type=_TZ_DATETIME)
    # Review R3: the authentication methods this session chain actually
    # passed, in RFC 8176's vocabulary where one fits ("pwd", "otp", "hwk",
    # "mfa") plus this product's own first factors ("email" for a magic link,
    # "oauth", "sso") and "backup" for a TOTP recovery code. Set when the
    # session is created, copied unchanged by refresh() like auth_time, and
    # only ever *added to* afterwards -- enrolling a second factor proves it
    # on the current session (see AuthService.verify_2fa).
    #
    # require_superadmin reads this rather than the account's enrollment.
    # Enrollment says a factor exists; this says the session in hand went
    # through it. A password-only session opened before the user enrolled
    # used to pass the admin gate on the strength of the former.
    amr: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )


class OAuthAccount(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    __tablename__ = "oauth_account"
    __table_args__: ClassVar[tuple[UniqueConstraint]] = (
        UniqueConstraint("provider", "provider_user_id"),
    )

    user_id: UUID = Field(foreign_key="user.id", index=True)
    provider: str
    provider_user_id: str


class TOTPSecret(RedactedReprMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "totp_secret"
    __redacted_fields__: ClassVar[set[str]] = {"secret_encrypted"}

    user_id: UUID = Field(foreign_key="user.id", primary_key=True)
    # never plaintext — §0.6
    secret_encrypted: str = Field(sa_column=Column(EncryptedString, nullable=False))
    enabled_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    backup_codes_hash: str = Field(default="[]")  # JSON list of argon2-hashed codes
    # T-218/ASVS V2.8.4-V2.8.5: the TOTP time-step (30s window) of the last
    # successfully consumed code, so a code already used this window (or an
    # earlier one) can be rejected as a replay instead of accepted again --
    # see AuthService._verify_totp_step. `None` until the first successful
    # verification.
    last_consumed_step: int | None = Field(default=None)


class WebAuthnCredential(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    """A registered hardware key or platform authenticator (T-217) -- an
    alternative to TOTPSecret at the same "2FA enabled" gate in
    AuthService.login(). Unlike TOTPSecret, a user may register more than
    one, so this has its own generated id rather than user_id as its PK.
    """

    __tablename__ = "webauthn_credential"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    # Base64url-encoded, per the WebAuthn/`webauthn` library's own wire
    # format -- looked up directly against the `id` field the browser sends
    # back on assertion, no decoding needed for the lookup itself.
    credential_id: str = Field(unique=True, index=True)
    # The COSE public key, base64url-encoded -- public by definition, so
    # (unlike TOTPSecret.secret_encrypted) this needs no EncryptedString.
    public_key: str
    sign_count: int = Field(default=0)
    aaguid: str
    nickname: str | None = Field(default=None)
    last_used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)


class EmailVerificationToken(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "email_verification_token"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)
    used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)


class PasswordResetToken(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "password_reset_token"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)
    used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)


class MagicLinkToken(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "magic_link_token"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)
    used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    redirect_to: str | None = Field(default=None)


class EmailChangeToken(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    """Proves the caller owns `new_email` before
    AuthService.confirm_email_change() applies it to User.email. Mirrors
    EmailVerificationToken's shape rather than embedding new_email in a
    signed token, so a second pending request can't silently race the
    uniqueness check confirm does at apply time.

    T-226: also carries a second single-use secret (`cancel_token_hash`),
    mailed to the *old* address alongside the `email_change_requested`
    notification, so the real account owner can kill a request they didn't
    make before `token_hash` is ever redeemed. `cancelled_at` is distinct
    from `used_at` only so callers can tell *which* outcome ended this row's
    life; `get_by_hash`/confirm still treat a cancelled row as unusable
    because mark_cancelled() also sets `used_at` (see repository.py).
    `cancel_token_hash` is nullable because rows created before this column
    existed have no cancel capability -- there's no value to backfill it
    with, and those rows expire within their original 1h TTL regardless.
    """

    __tablename__ = "email_change_token"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    new_email: str
    token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    cancel_token_hash: str | None = Field(default=None, unique=True)  # SHA-256 hex
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)
    used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    cancelled_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)
    # T-226: same device/IP fingerprint Session.{ip_address,user_agent} carry,
    # for the same admin-assisted recovery / audit story -- lets support (or
    # AuditService's log, which this doesn't duplicate into since the token
    # row itself *is* the durable record) tell a legitimate request from one
    # made after a session/credential compromise.
    requested_ip: str
    requested_user_agent: str
