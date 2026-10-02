import threading
import time
from typing import ClassVar

import pytest
from cryptography.fernet import Fernet, MultiFernet
from sqlmodel import SQLModel

from app.core import encrypted_type
from app.core.config import get_settings
from app.core.encrypted_type import EncryptedString, RedactedReprMixin


def _set_keys(monkeypatch: pytest.MonkeyPatch, primary: str, old: str = "") -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", primary)
    monkeypatch.setenv("ENCRYPTION_KEY_OLD", old)
    get_settings.cache_clear()
    encrypted_type._fernet_instance = None


class _FakeSettings:
    def __init__(self, encryption_key: str, encryption_key_old: str = "") -> None:
        self.ENCRYPTION_KEY = encryption_key
        self.ENCRYPTION_KEY_OLD = encryption_key_old


# ---------------------------------------------------------------------------
# EncryptedString round trip / data integrity
# ---------------------------------------------------------------------------


@pytest.mark.owasp("A02")
def test_round_trip_encrypts_and_decrypts() -> None:
    enc_type = EncryptedString()

    ciphertext = enc_type.process_bind_param("hello", None)

    assert ciphertext is not None
    assert ciphertext.startswith("gAAAAA")
    assert enc_type.process_result_value(ciphertext, None) == "hello"


def test_none_passes_through_bind_and_result() -> None:
    enc_type = EncryptedString()

    assert enc_type.process_bind_param(None, None) is None
    assert enc_type.process_result_value(None, None) is None


@pytest.mark.owasp("A02")
def test_tampered_ciphertext_raises_value_error() -> None:
    enc_type = EncryptedString()
    ciphertext = enc_type.process_bind_param("hello", None)
    assert ciphertext is not None
    flipped_last_char = "A" if ciphertext[-1] != "A" else "B"
    tampered = ciphertext[:-1] + flipped_last_char

    with pytest.raises(ValueError, match="failed to decrypt"):
        enc_type.process_result_value(tampered, None)


def test_legacy_ascii_plaintext_passes_through_unchanged() -> None:
    enc_type = EncryptedString()

    assert enc_type.process_result_value("plain-legacy-value", None) == "plain-legacy-value"


def test_legacy_non_ascii_plaintext_passes_through_unchanged() -> None:
    enc_type = EncryptedString()

    assert enc_type.process_result_value("café-legacy-value", None) == "café-legacy-value"


def test_bind_param_rejects_unencodable_surrogate() -> None:
    enc_type = EncryptedString()

    with pytest.raises(ValueError, match="cannot be encoded to UTF-8"):
        enc_type.process_bind_param("\ud800", None)


def test_key_rotation_old_key_still_decrypts_after_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    enc_type = EncryptedString()

    _set_keys(monkeypatch, primary=key_a)
    ciphertext = enc_type.process_bind_param("secret", None)
    assert ciphertext is not None

    _set_keys(monkeypatch, primary=key_b, old=key_a)
    assert enc_type.process_result_value(ciphertext, None) == "secret"


# ---------------------------------------------------------------------------
# _load_fernet — key sourcing via get_settings(), not os.environ
# ---------------------------------------------------------------------------


def test_load_fernet_uses_get_settings_not_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(encrypted_type, "get_settings", lambda: _FakeSettings(key))

    multi = encrypted_type._load_fernet()

    token = Fernet(key.encode("ascii")).encrypt(b"x")
    assert multi.decrypt(token) == b"x"


def test_load_fernet_raises_when_key_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(encrypted_type, "get_settings", lambda: _FakeSettings(""))

    with pytest.raises(OSError, match="ENCRYPTION_KEY is not set"):
        encrypted_type._load_fernet()


def test_load_fernet_raises_for_invalid_primary_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(encrypted_type, "get_settings", lambda: _FakeSettings("not-a-valid-key"))

    with pytest.raises(OSError, match="ENCRYPTION_KEY is set but invalid"):
        encrypted_type._load_fernet()


def test_load_fernet_raises_for_invalid_old_key(monkeypatch: pytest.MonkeyPatch) -> None:
    valid_key = Fernet.generate_key().decode()
    monkeypatch.setattr(
        encrypted_type, "get_settings", lambda: _FakeSettings(valid_key, "not-valid,")
    )

    with pytest.raises(OSError, match=r"Key #1 in ENCRYPTION_KEY_OLD is invalid"):
        encrypted_type._load_fernet()


def test_load_fernet_skips_blank_old_key_entries_and_loads_valid_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = Fernet.generate_key().decode()
    old = Fernet.generate_key().decode()
    monkeypatch.setattr(
        encrypted_type, "get_settings", lambda: _FakeSettings(primary, f" {old} , ")
    )

    multi = encrypted_type._load_fernet()

    token = Fernet(old.encode("ascii")).encrypt(b"y")
    assert multi.decrypt(token) == b"y"


# ---------------------------------------------------------------------------
# _get_fernet — caching + thread safety
# ---------------------------------------------------------------------------


def test_get_fernet_is_thread_safe_under_concurrent_first_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_load = encrypted_type._load_fernet

    def slow_load() -> MultiFernet:
        time.sleep(0.05)
        return original_load()

    monkeypatch.setattr(encrypted_type, "_load_fernet", slow_load)

    results: list[MultiFernet] = []
    threads = [
        threading.Thread(target=lambda: results.append(encrypted_type._get_fernet()))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 2
    assert results[0] is results[1]


# ---------------------------------------------------------------------------
# RedactedReprMixin
# ---------------------------------------------------------------------------


class _DemoSecretModel(RedactedReprMixin, SQLModel):
    __redacted_fields__: ClassVar[set[str]] = {"secret"}

    name: str
    secret: str


class _DemoUnredactedModel(RedactedReprMixin, SQLModel):
    name: str


def test_redacted_repr_hides_only_declared_fields() -> None:
    obj = _DemoSecretModel(name="alice", secret="shh")

    text_repr = repr(obj)

    assert "secret='[REDACTED]'" in text_repr
    assert "name='alice'" in text_repr
    assert "shh" not in text_repr


def test_redacted_repr_defaults_to_no_redactions() -> None:
    obj = _DemoUnredactedModel(name="bob")

    assert repr(obj) == "_DemoUnredactedModel(name='bob')"
