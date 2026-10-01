import pytest
from pydantic import ValidationError

from app.modules.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    PasswordResetConfirmRequest,
    RegisterRequest,
    UserPublic,
    Verify2FARequest,
)

# ---------------------------------------------------------------------------
# Password strength + denylist (RegisterRequest / PasswordResetConfirmRequest
# share the same validator)
# ---------------------------------------------------------------------------


def test_register_request_accepts_strong_unique_password() -> None:
    req = RegisterRequest(email="alice@example.com", password="Tr0ub4dor&3xyz")

    assert req.password == "Tr0ub4dor&3xyz"


def test_register_request_rejects_password_shorter_than_12_chars() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="alice@example.com", password="Short1Aa!")


def test_register_request_rejects_password_longer_than_128_chars() -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(email="alice@example.com", password="Correct-Horse9" + "x" * 128)


# T-216: ASVS V2.1.9 / NIST SP 800-63B recommend against composition rules
# (forced upper/lower/digit) in favor of length + breach-list checks alone --
# these three, previously-rejected passwords are now accepted as long as
# they're long enough and not on the common-password denylist.
def test_register_request_accepts_password_without_uppercase() -> None:
    req = RegisterRequest(email="alice@example.com", password="lowercase123only")

    assert req.password == "lowercase123only"


def test_register_request_accepts_password_without_lowercase() -> None:
    req = RegisterRequest(email="alice@example.com", password="UPPERCASE123ONLY")

    assert req.password == "UPPERCASE123ONLY"


def test_register_request_accepts_password_without_digit() -> None:
    req = RegisterRequest(email="alice@example.com", password="NoDigitsHereAtAll")

    assert req.password == "NoDigitsHereAtAll"


def test_register_request_rejects_common_password() -> None:
    # 12 chars, has upper+lower+digit — passes every complexity rule, but is
    # trivially the breached word "password" with a numeric/symbol suffix.
    with pytest.raises(ValidationError):
        RegisterRequest(email="alice@example.com", password="Password123!")


def test_password_reset_confirm_uses_same_validators_as_register() -> None:
    with pytest.raises(ValidationError):
        PasswordResetConfirmRequest(token="tok", new_password="Password123!")


def test_password_reset_confirm_accepts_strong_password() -> None:
    confirm = PasswordResetConfirmRequest(token="tok", new_password="Tr0ub4dor&3xyz")

    assert confirm.new_password == "Tr0ub4dor&3xyz"


# ---------------------------------------------------------------------------
# ChangePasswordRequest schemas -- T-216
# ---------------------------------------------------------------------------


def test_change_password_request_accepts_strong_password() -> None:
    req = ChangePasswordRequest(current_password="whatever", new_password="Tr0ub4dor&3xyz")

    assert req.new_password == "Tr0ub4dor&3xyz"


def test_change_password_request_rejects_short_new_password() -> None:
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="whatever", new_password="short1Aa!")


def test_change_password_request_rejects_common_new_password() -> None:
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="whatever", new_password="Password123!")


def test_change_password_request_rejects_current_password_longer_than_128_chars() -> None:
    with pytest.raises(ValidationError):
        ChangePasswordRequest(current_password="x" * 129, new_password="Tr0ub4dor&3xyz")


# ---------------------------------------------------------------------------
# Password max_length=128 (T-216) -- every password-accepting field
# ---------------------------------------------------------------------------


def test_login_request_rejects_password_longer_than_128_chars() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="alice@example.com", password="x" * 129)


# ---------------------------------------------------------------------------
# Email validation
# ---------------------------------------------------------------------------


def test_login_request_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="not-an-email", password="whatever")


# ---------------------------------------------------------------------------
# UserPublic must never leak sensitive fields
# ---------------------------------------------------------------------------


def test_user_public_excludes_sensitive_fields() -> None:
    assert "hashed_password" not in UserPublic.model_fields
    assert "failed_login_count" not in UserPublic.model_fields
    assert "locked_until" not in UserPublic.model_fields


# ---------------------------------------------------------------------------
# 2FA schemas
# ---------------------------------------------------------------------------


def test_verify_2fa_request_accepts_six_digit_code() -> None:
    req = Verify2FARequest(code="123456")

    assert req.code == "123456"


@pytest.mark.parametrize("code", ["12345", "1234567", "12345a", "abcdef"])
def test_verify_2fa_request_rejects_invalid_code(code: str) -> None:
    with pytest.raises(ValidationError):
        Verify2FARequest(code=code)
