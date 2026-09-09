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
    # The compose posture: Caddy answers /metrics with a 404 before the app
    # ever sees the request (verified for both Caddyfiles by
    # test_caddy_config.py). A deployment with no such edge -- Render, Fly,
    # Cloud Run -- sets METRICS_TOKEN instead; one of the two is mandatory.
    "METRICS_BLOCKED_AT_EDGE": True,
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
def test_read_replica_url_missing_sslmode_outside_dev_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # T-246: a third hop to the same rows, so it carries the same requirement
    # as DATABASE_URL and MIGRATIONS_DATABASE_URL -- neither of which says
    # anything about this one.
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="READ_REPLICA_URL"):
        Settings(
            _env_file=FIXTURE_ENV_FILE,
            **{
                **_PROD_SAFE_OVERRIDES,
                "READ_REPLICA_URL": "postgresql+asyncpg://u:p@replica:5432/db",
            },
        )


def test_blank_read_replica_url_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # `READ_REPLICA_URL=` is how .env.example ships it, and it must parse as
    # "no replica" rather than as an invalid DSN.
    _clear_settings_env(monkeypatch)

    settings = Settings(_env_file=FIXTURE_ENV_FILE, READ_REPLICA_URL="")

    assert settings.READ_REPLICA_URL is None


def test_read_replica_url_with_tls_is_accepted_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_settings_env(monkeypatch)

    settings = Settings(
        _env_file=FIXTURE_ENV_FILE,
        **{
            **_PROD_SAFE_OVERRIDES,
            "READ_REPLICA_URL": "postgresql+asyncpg://u:p@replica:5432/db?sslmode=require",
        },
    )

    assert settings.READ_REPLICA_URL is not None


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


@pytest.mark.module_dependent  # the assertion is about billing being on
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


@pytest.mark.owasp("A01")
def test_metrics_unprotected_outside_dev_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Caddy assumption, made explicit.

    `/metrics` was registered with no authentication at all; the only thing
    that ever kept it off the internet was `respond /metrics 404` in the
    Caddyfiles. That is a property of the edge, and a platform deployment
    (Render, Fly, Cloud Run) has no such edge -- there the app is the edge and
    the endpoint answered 200 to anyone. Outside dev the deployment now has to
    say which of the two protections it is relying on.
    """
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="/metrics would be publicly readable"):
        Settings(
            _env_file=FIXTURE_ENV_FILE,
            **{**_PROD_SAFE_OVERRIDES, "METRICS_BLOCKED_AT_EDGE": False},
        )


def test_metrics_token_satisfies_the_requirement_without_an_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_settings_env(monkeypatch)

    settings = Settings(
        _env_file=FIXTURE_ENV_FILE,
        **{
            **_PROD_SAFE_OVERRIDES,
            "METRICS_BLOCKED_AT_EDGE": False,
            "METRICS_TOKEN": "a-scrape-token",
        },
    )

    assert settings.METRICS_TOKEN == "a-scrape-token"


def test_metrics_endpoint_is_unguarded_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev pays nothing for either of the above."""
    _clear_settings_env(monkeypatch)

    settings = Settings(_env_file=FIXTURE_ENV_FILE)

    assert settings.ENVIRONMENT == "dev"
    assert settings.METRICS_TOKEN is None
    assert settings.METRICS_BLOCKED_AT_EDGE is False


@pytest.mark.owasp("A02")
def test_redis_private_network_opt_in_permits_plain_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The managed-Redis case the strict rule could not express.

    Render's Key Value (and several like it) offers `redis://` on a private
    network and reserves TLS for an external URL that costs an IP allowlist
    and a public round trip per session lookup. "Encrypted" and "not on an
    untrusted network" both satisfy ASVS V9.2.2; this is the second, and it
    has to be claimed explicitly.
    """
    _clear_settings_env(monkeypatch)

    settings = Settings(
        _env_file=FIXTURE_ENV_FILE,
        **{
            **_PROD_SAFE_OVERRIDES,
            "REDIS_URL": "redis://red-abc123:6379/0",
            "REDIS_ON_PRIVATE_NETWORK": True,
        },
    )

    assert settings.REDIS_URL.scheme == "redis"


def test_redis_private_network_opt_in_does_not_relax_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The escape hatch is Redis-shaped and must not widen.

    Render's Postgres has no equivalent excuse -- TLS is available on both its
    internal and external connection strings -- so `sslmode` stays mandatory
    no matter what the Redis instance is doing.
    """
    _clear_settings_env(monkeypatch)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings(
            _env_file=FIXTURE_ENV_FILE,
            **{
                **_PROD_SAFE_OVERRIDES,
                "REDIS_ON_PRIVATE_NETWORK": True,
                "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
            },
        )
