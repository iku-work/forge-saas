"""That this process cannot serve while addressing its own object storage
over the internet.

The backend and worker use the same public hostname for MinIO that the
browser gets, because there is one AWS_ENDPOINT_URL and the presigned URLs
built from it go to a browser. docker-compose.prod.yml closes that gap with a
network alias on the caddy service, so inside the network the name resolves
to a container.

Two things are pinned here, because there are two ways to lose it: the alias
being edited out of the compose file, and the alias silently expanding to its
default because the compose project's .env is missing (compose interpolates
from that file and never from backend/.env). The second is the dangerous one
-- it does not fail, it just puts every server-side S3 call back on the
public path, where a WAF that challenges non-browser clients drops a fraction
of them and uploads fail at the confirm step with a stack trace pointing
somewhere else entirely.

The check is driven directly rather than through app startup: what is pinned
is the decision given a resolution result.
"""

import logging
from pathlib import Path

import pytest
import yaml

from app.core.modules import EnabledModules
from app.core.storage_endpoint import enforce_storage_endpoint_is_reachable_internally
from product import config as product_config


@pytest.fixture(autouse=True)
def _file_storage_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check stands down when file_storage is off, so every test here
    sets the defaults (file_storage and its billing dependency on) rather
    than inheriting whatever product config the run uses."""
    monkeypatch.setattr(product_config, "MODULES", EnabledModules())


REPO_ROOT = Path(__file__).parent.parent.parent


class _Settings:
    def __init__(
        self,
        *,
        environment: str = "production",
        storage_domain: str = "storage.example.com",
        endpoint: str | None = "https://storage.example.com",
        allow_public: bool = False,
    ) -> None:
        self.ENVIRONMENT = environment
        self.STORAGE_DOMAIN = storage_domain
        self.AWS_ENDPOINT_URL = endpoint
        self.ALLOW_PUBLIC_STORAGE_ENDPOINT = allow_public


def _resolving_to(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    monkeypatch.setattr(
        "app.core.storage_endpoint.socket.getaddrinfo",
        lambda *_a, **_k: [(None, None, None, "", (addr, 0)) for addr in addresses],
    )


# --- the compose file that makes it true ------------------------------------


def test_prod_compose_aliases_caddy_to_the_storage_hostname() -> None:
    """Delete this alias and every server-side S3 call goes out to the
    internet. Nothing else in the suite would notice."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.prod.yml").read_text())

    aliases = compose["services"]["caddy"]["networks"]["forge-net"]["aliases"]

    # Interpolated, not hardcoded -- and from the compose project's own .env,
    # which is why .env.example at the repo root exists and says so.
    assert "${STORAGE_DOMAIN" in "".join(aliases)


def test_repo_root_env_example_documents_storage_domain() -> None:
    """The alias above expands from this file. Without it the expansion falls
    back to a default and the misconfiguration is silent."""
    example = (REPO_ROOT / ".env.example").read_text()

    assert "STORAGE_DOMAIN" in example


# --- the boot check ---------------------------------------------------------


def test_refuses_to_start_when_the_storage_hostname_resolves_off_the_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolving_to(monkeypatch, "104.21.41.91")

    with pytest.raises(RuntimeError, match="resolves to the public address"):
        enforce_storage_endpoint_is_reachable_internally(_Settings())  # type: ignore[arg-type]


def test_starts_when_the_alias_is_doing_its_job(monkeypatch: pytest.MonkeyPatch) -> None:
    # 172.18.x is the compose bridge -- what the caddy alias resolves to.
    _resolving_to(monkeypatch, "172.18.0.8")

    enforce_storage_endpoint_is_reachable_internally(_Settings())  # type: ignore[arg-type]


def test_a_public_address_alongside_a_private_one_still_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-robin DNS that answers with both is not half-safe: the public
    entry will be picked sooner or later."""
    _resolving_to(monkeypatch, "172.18.0.8", "104.21.41.91")

    with pytest.raises(RuntimeError):
        enforce_storage_endpoint_is_reachable_internally(_Settings())  # type: ignore[arg-type]


def test_managed_storage_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty STORAGE_DOMAIN means S3 or R2, where public is the only kind
    of address there is."""
    _resolving_to(monkeypatch, "52.219.44.1")

    enforce_storage_endpoint_is_reachable_internally(
        _Settings(storage_domain="", endpoint="https://s3.eu-central-1.amazonaws.com")  # type: ignore[arg-type]
    )


def test_dev_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolving_to(monkeypatch, "203.0.113.7")

    enforce_storage_endpoint_is_reachable_internally(_Settings(environment="dev"))  # type: ignore[arg-type]


def test_the_escape_hatch_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "Refuses to boot" must never be a dead end -- MinIO on a separate host
    is a real deployment, and this is how it says so."""
    _resolving_to(monkeypatch, "104.21.41.91")

    enforce_storage_endpoint_is_reachable_internally(_Settings(allow_public=True))  # type: ignore[arg-type]


def test_unresolvable_is_not_fatal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A DNS blip during a restart must not take the process down. The first
    S3 call reports a genuinely dead endpoint far better than this could."""

    def _raise(*_a: object, **_k: object) -> None:
        raise OSError("Name or service not known")

    monkeypatch.setattr("app.core.storage_endpoint.socket.getaddrinfo", _raise)

    with caplog.at_level(logging.WARNING, logger="app.core.storage_endpoint"):
        enforce_storage_endpoint_is_reachable_internally(_Settings())  # type: ignore[arg-type]

    assert "Could not resolve" in caplog.text
