from collections.abc import Generator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import module_loader
from app.core.config import Settings, get_settings, validate_stripe_keys
from app.core.modules import EnabledModules, ModuleConfig
from product import config as product_config

FIXTURE_ENV_FILE = Path(__file__).parent.parent / ".env.test"


@pytest.fixture(autouse=True)
def _clear_modules_cache() -> Generator[None]:
    module_loader.get_modules.cache_clear()
    yield
    module_loader.get_modules.cache_clear()


def _clear_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def test_missing_required_field_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://u:p@localhost/db")


@pytest.mark.owasp("A05")
def test_wildcard_origin_outside_dev_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        Settings(
            _env_file=FIXTURE_ENV_FILE,
            ENVIRONMENT="prod",
            ALLOWED_ORIGINS=["*"],
            FORCE_HTTPS=True,
        )


def test_force_https_required_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="FORCE_HTTPS"):
        Settings(_env_file=FIXTURE_ENV_FILE, ENVIRONMENT="prod", FORCE_HTTPS=False)


_PROD_SAFE_OVERRIDES: dict[str, object] = {
    "ENVIRONMENT": "prod",
    "FORCE_HTTPS": True,
    "VIRUS_SCANNER": "clamav",
    "PASSWORD_PEPPER": "a-real-pepper",
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db?sslmode=require",
    "REDIS_URL": "rediss://localhost:6379/0",
}


def test_force_https_true_in_prod_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    settings = Settings(_env_file=FIXTURE_ENV_FILE, **_PROD_SAFE_OVERRIDES)

    assert settings.ENVIRONMENT == "prod"


@pytest.mark.owasp("A05")
def test_virus_scanner_noop_outside_dev_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="VIRUS_SCANNER"):
        Settings(_env_file=FIXTURE_ENV_FILE, **{**_PROD_SAFE_OVERRIDES, "VIRUS_SCANNER": "noop"})


def test_password_pepper_required_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="PASSWORD_PEPPER"):
        Settings(_env_file=FIXTURE_ENV_FILE, **{**_PROD_SAFE_OVERRIDES, "PASSWORD_PEPPER": ""})


@pytest.mark.owasp("A02")
def test_database_url_missing_sslmode_outside_dev_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings(
            _env_file=FIXTURE_ENV_FILE,
            **{
                **_PROD_SAFE_OVERRIDES,
                "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
            },
        )


@pytest.mark.owasp("A02")
def test_redis_url_wrong_scheme_outside_dev_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        Settings(
            _env_file=FIXTURE_ENV_FILE,
            **{**_PROD_SAFE_OVERRIDES, "REDIS_URL": "redis://localhost:6379/0"},
        )


def test_all_invariants_satisfied_in_dev_does_not_require_pepper_or_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # dev is exempt from every non-dev-only invariant above (VIRUS_SCANNER,
    # PASSWORD_PEPPER, sslmode/rediss) -- the fixture's own dev-shaped
    # defaults (VIRUS_SCANNER=noop, blank PASSWORD_PEPPER, plain
    # redis://) must still boot cleanly.
    _clear_settings_env(monkeypatch)

    settings = Settings(_env_file=FIXTURE_ENV_FILE)

    assert settings.ENVIRONMENT == "dev"


def test_validate_stripe_keys_raises_when_billing_enabled_and_keys_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_env(monkeypatch)
    settings = Settings(
        _env_file=FIXTURE_ENV_FILE, STRIPE_SECRET_KEY=None, STRIPE_WEBHOOK_SECRET=None
    )

    with pytest.raises(RuntimeError, match="STRIPE_SECRET_KEY"):
        validate_stripe_keys(settings)


def test_validate_stripe_keys_does_not_raise_when_billing_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_env(monkeypatch)
    settings = Settings(
        _env_file=FIXTURE_ENV_FILE, STRIPE_SECRET_KEY=None, STRIPE_WEBHOOK_SECRET=None
    )
    monkeypatch.setattr(
        product_config,
        "MODULES",
        EnabledModules(
            billing=ModuleConfig(enabled=False),
            notifications=ModuleConfig(enabled=False),
            file_storage=ModuleConfig(enabled=False),
        ),
    )
    module_loader.get_modules.cache_clear()

    validate_stripe_keys(settings)  # must not raise


def test_validate_stripe_keys_does_not_raise_when_billing_enabled_and_keys_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_env(monkeypatch)
    settings = Settings(_env_file=FIXTURE_ENV_FILE)

    validate_stripe_keys(settings)  # must not raise


def test_invalid_encryption_key_raises_environment_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_env(monkeypatch)

    with pytest.raises(EnvironmentError, match="ENCRYPTION_KEY"):
        Settings(_env_file=FIXTURE_ENV_FILE, ENCRYPTION_KEY="not-a-valid-fernet-key")


def test_get_settings_loads_from_env_test_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)
    monkeypatch.setattr(
        Settings, "model_config", {**Settings.model_config, "env_file": str(FIXTURE_ENV_FILE)}
    )
    get_settings.cache_clear()

    try:
        settings = get_settings()
        assert settings.ENVIRONMENT == "dev"
        assert (
            str(settings.DATABASE_URL)
            == "postgresql+asyncpg://postgres:postgres@localhost:5432/forge_test"
        )
        assert get_settings() is settings
    finally:
        get_settings.cache_clear()
