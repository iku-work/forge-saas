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


def test_force_https_true_in_prod_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    settings = Settings(_env_file=FIXTURE_ENV_FILE, ENVIRONMENT="prod", FORCE_HTTPS=True)

    assert settings.ENVIRONMENT == "prod"


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
