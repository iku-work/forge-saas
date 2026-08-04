"""
encrypted_type.py
-----------------
A SQLAlchemy TypeDecorator that provides transparent Fernet encryption
for sensitive database columns.

Usage in a SQLModel:

    class TOTPSecret(RedactedReprMixin, SQLModel, table=True):
        __redacted_fields__: ClassVar[set[str]] = {"secret_encrypted"}

        user_id: UUID = Field(primary_key=True)
        secret_encrypted: str = Field(sa_column=Column(EncryptedString))

ENCRYPTION_KEY / ENCRYPTION_KEY_OLD are read through app.core.config.get_settings()
(pydantic-settings) — never directly from the environment, per §0.6.

Generate a key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Key rotation workflow (the full operational procedure, including crash
recovery and rollback at each step, is docs/runbooks/encryption-key-rotation.md):
    1. Generate a new key.
    2. Set ENCRYPTION_KEY=<new_key>, ENCRYPTION_KEY_OLD=<current_key>. Deploy.
    3. New writes use the new key; old rows still decrypt via the old key.
    4. Run rotate_encryption_keys_job (app.workers.jobs.encryption), which
       discovers and walks every EncryptedString column on its own.
    5. Once every row is migrated, remove ENCRYPTION_KEY_OLD.
"""

import logging
import threading
from typing import ClassVar

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

_fernet_instance: MultiFernet | None = None
_fernet_lock = threading.Lock()


def _load_fernet() -> MultiFernet:
    """
    Read Settings.ENCRYPTION_KEY (and optionally ENCRYPTION_KEY_OLD) and
    return a MultiFernet instance.

    Raises OSError with a clear message if anything is wrong. Settings
    already validates ENCRYPTION_KEY at construction time (see
    app.core.config._validate_encryption_key), so in normal operation only
    a malformed ENCRYPTION_KEY_OLD entry reaches the error paths below —
    the primary-key checks are defense in depth for a stubbed Settings.
    """
    settings = get_settings()
    if not settings.ENCRYPTION_KEY:
        raise OSError(
            "ENCRYPTION_KEY is not set. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )

    try:
        keys = [Fernet(settings.ENCRYPTION_KEY.encode("ascii"))]
    except (ValueError, UnicodeEncodeError) as exc:
        raise OSError(
            f"ENCRYPTION_KEY is set but invalid: {exc}. "
            "It must be a URL-safe base64-encoded 32-byte key."
        ) from exc

    for index, raw in enumerate(filter(None, settings.ENCRYPTION_KEY_OLD.split(",")), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            keys.append(Fernet(stripped.encode("ascii")))
        except (ValueError, UnicodeEncodeError) as exc:
            raise OSError(f"Key #{index} in ENCRYPTION_KEY_OLD is invalid: {exc}") from exc

    return MultiFernet(keys)


def _get_fernet() -> MultiFernet:
    """
    Return the shared MultiFernet instance, initialising it on the first call.

    Thread-safe: uses double-checked locking so only one thread ever calls
    _load_fernet(), even under concurrent requests at startup.
    """
    global _fernet_instance  # noqa: PLW0603 -- guards the module-level singleton below

    # Fast path — already initialised (no lock needed for a read)
    if _fernet_instance is not None:
        return _fernet_instance

    # Slow path — first call, take the lock
    with _fernet_lock:
        # Another thread may have initialised it while we waited
        if _fernet_instance is None:
            _fernet_instance = _load_fernet()

    return _fernet_instance


def get_shared_fernet() -> MultiFernet:
    """
    Public accessor for the process-wide MultiFernet instance.

    Callers outside this module (eager validation at FastAPI startup in
    app.main, the key-rotation job in app.workers.jobs.encryption) must go
    through this instead of the private `_get_fernet()` cache — see the
    SLF001 lint rule and §0.1.
    """
    return _get_fernet()


# ---------------------------------------------------------------------------
# TypeDecorator
# ---------------------------------------------------------------------------


class EncryptedString(TypeDecorator[str]):
    """
    A SQLAlchemy column type that transparently encrypts on write and
    decrypts on read using Fernet authenticated encryption (AES-128-CBC
    + HMAC-SHA256).

    The underlying database column is TEXT (no length limit), because
    Fernet tokens are always longer than the source plaintext.

    Filtering on encrypted columns is not supported — ciphertext differs
    on every write due to the random IV. For exact-match lookups, store a
    separate SHA-256 hash column alongside the encrypted value.

    cache_ok = True is safe here because all EncryptedString instances
    share the same module-level singleton from _get_fernet(). If per-instance
    keys are ever introduced, this must be changed to False.
    """

    impl = Text  # TEXT in all major databases; no truncation risk
    cache_ok = True  # See docstring above

    # Fernet tokens are base64url-encoded and always start with this prefix
    # (the base64 encoding of the version byte 0x80).
    _FERNET_PREFIX = "gAAAAA"

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def process_bind_param(self, value: str | None, _dialect: Dialect) -> str | None:
        """
        Called before INSERT / UPDATE.
        Encrypts value and returns the ASCII Fernet token, or None.
        """
        if value is None:
            return None

        try:
            plaintext_bytes = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            # Surrogates or other un-encodable characters in the value.
            raise ValueError(
                f"EncryptedString: value contains characters that cannot be encoded to UTF-8: {exc}"
            ) from exc

        return _get_fernet().encrypt(plaintext_bytes).decode("ascii")

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def process_result_value(self, value: str | None, _dialect: Dialect) -> str | None:
        """
        Called after SELECT.
        Decrypts Fernet tokens; passes through legacy plaintext rows silently.
        Raises ValueError if a value looks like a Fernet token but cannot
        be decrypted — this indicates key mismatch or data corruption and
        must not be silently swallowed.
        """
        if value is None:
            return None

        # --- Step 1: is this even a Fernet token? -----------------------

        try:
            token_bytes = value.encode("ascii")
        except UnicodeEncodeError:
            # Non-ASCII text cannot be a Fernet token.
            # This is a legacy plaintext row stored before encryption was
            # introduced. Return as-is.
            logger.debug(
                "EncryptedString.process_result_value: non-ASCII value treated as legacy plaintext."
            )
            return value

        if not value.startswith(self._FERNET_PREFIX):
            # ASCII text that doesn't carry the Fernet version prefix is
            # also a legacy plaintext row.
            logger.debug(
                "EncryptedString.process_result_value: "
                "value has no Fernet prefix, treated as legacy plaintext."
            )
            return value

        # --- Step 2: it looks like a Fernet token — must decrypt --------

        try:
            return _get_fernet().decrypt(token_bytes).decode("utf-8")
        except InvalidToken as exc:
            # The value looks like a genuine Fernet token but failed
            # authentication. This means either:
            #   a) the key has been rotated without adding the old key to
            #      ENCRYPTION_KEY_OLD, or
            #   b) the ciphertext has been corrupted.
            # Silently returning the raw ciphertext as "plaintext" would
            # be a data-integrity failure, so we raise instead.
            logger.exception(
                "EncryptedString.process_result_value: "
                "Fernet token failed to decrypt. "
                "Check ENCRYPTION_KEY and ENCRYPTION_KEY_OLD. "
                "This may indicate a key mismatch or data corruption."
            )
            raise ValueError(
                "EncryptedString: failed to decrypt a value that looks like "
                "a Fernet token. Possible key mismatch or data corruption. "
                "Check ENCRYPTION_KEY / ENCRYPTION_KEY_OLD and application logs."
            ) from exc


# ---------------------------------------------------------------------------
# Repr mixin
# ---------------------------------------------------------------------------


class RedactedReprMixin:
    """
    Mixin for SQLModel models that replaces sensitive field values with
    '[REDACTED]' in __repr__, preventing accidental leakage into logs,
    Sentry, debug output, etc.

    Declare which fields are sensitive via the __redacted_fields__ class
    variable:

        class TOTPSecret(RedactedReprMixin, SQLModel, table=True):
            __redacted_fields__: ClassVar[set[str]] = {"secret_encrypted"}
    """

    __redacted_fields__: ClassVar[set[str]] = set()

    def __repr__(self) -> str:
        # RedactedReprMixin is always combined with a SQLModel subclass,
        # which provides model_fields (Pydantic v2) at the class level —
        # the mixin alone does not, hence the ignore. Accessed via type(self),
        # not self, since instance access is deprecated since Pydantic 2.11.
        field_names = list(type(self).model_fields.keys())  # type: ignore[attr-defined]

        parts = []
        for name in field_names:
            if name in self.__redacted_fields__:
                parts.append(f"{name}='[REDACTED]'")
            else:
                parts.append(f"{name}={getattr(self, name)!r}")

        return f"{self.__class__.__name__}({', '.join(parts)})"
