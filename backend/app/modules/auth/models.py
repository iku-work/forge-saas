from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.core.encrypted_type import EncryptedString, RedactedReprMixin
from app.models._mixins import SoftDeleteMixin, TimestampMixin, UUIDPKMixin

# Every datetime column declared directly in this file (i.e. not inherited
# from TimestampMixin) must use the same tz-aware type as the mixin — see
# app.models._mixins for why a naive TIMESTAMP column breaks asyncpg binds.
_TZ_DATETIME = DateTime(timezone=True)


class User(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "user"

    email: str = Field(unique=True, index=True)
    email_verified_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    hashed_password: str | None = Field(default=None)  # null for OAuth-only users
    full_name: str | None = Field(default=None)
    avatar_url: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    is_superadmin: bool = Field(default=False)
    totp_enabled: bool = Field(default=False)
    locale: str = Field(default="en")
    failed_login_count: int = Field(default=0)
    locked_until: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    # Maps an event name to enabled channels, e.g. member_invited -> email/in_app/push.
    notification_preferences: dict[str, dict[str, bool]] = Field(
        sa_column=Column(JSONB, nullable=False, default=dict)
    )


class Session(UUIDPKMixin, TimestampMixin, SQLModel, table=True):
    __tablename__ = "session"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    refresh_token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    device_label: str | None = Field(default=None)
    ip_address: str
    user_agent: str
    last_seen_at: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    revoked_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    # T-218: the instant of the *real* credential check (password login, 2FA
    # confirmation, magic link, or WebAuthn assertion) that started this
    # session chain -- copied unchanged across every refresh()-driven
    # rotation, unlike expires_at/last_seen_at which each rotation updates.
    # This is what require_recent_auth (app.core.dependencies) reads via the
    # access token's "auth_time" claim to gate step-up-protected actions,
    # since a silent refresh must never itself count as "recent auth".
    auth_time: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]


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
    enabled_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
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
    last_used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]


class EmailVerificationToken(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "email_verification_token"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]


class PasswordResetToken(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "password_reset_token"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]


class MagicLinkToken(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    __tablename__ = "magic_link_token"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    redirect_to: str | None = Field(default=None)


class EmailChangeToken(UUIDPKMixin, TimestampMixin, SoftDeleteMixin, SQLModel, table=True):
    """T-218 (minimal implementation -- see T-226's follow-up for the
    production-ready version): proves the caller owns `new_email` before
    AuthService.confirm_email_change() applies it to User.email. Mirrors
    EmailVerificationToken's shape rather than embedding new_email in a
    signed token, so a second pending request can't silently race the
    uniqueness check confirm does at apply time.
    """

    __tablename__ = "email_change_token"

    user_id: UUID = Field(foreign_key="user.id", index=True)
    new_email: str
    token_hash: str = Field(unique=True)  # SHA-256 hex — never the plain token
    expires_at: datetime = Field(sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
    used_at: datetime | None = Field(default=None, sa_type=_TZ_DATETIME)  # type: ignore[call-overload]
