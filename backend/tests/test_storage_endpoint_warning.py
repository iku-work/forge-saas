"""Whether this process reaches its own object storage across the container
network or over the internet.

The backend and worker use the same public hostname for MinIO that the
browser gets, because there is only one AWS_ENDPOINT_URL and the presigned
URLs handed to a browser are built from it (see Settings.STORAGE_DOMAIN).
docker-compose.prod.yml closes that gap by aliasing the caddy service to the
hostname, so inside the network it resolves to a container.

When the alias is missing the stack still boots and mostly works, which is
what makes it worth warning about. On 2026-09-09 that configuration meant
every server-side S3 call crossed Cloudflare, whose bot protection answered
403 to ~7% of them in a measured sample -- surfacing only as uploads failing
at the confirm step, with a stack trace that pointed at HeadObject and gave
no hint that the network path was the problem.

The check is driven directly rather than through app startup: what is being
pinned is the decision given a resolution result, and importing app.main
re-runs it against whatever backend/.env happens to say.
"""

import logging

import pytest

from app.main import _warn_if_storage_endpoint_leaves_the_host


class _Settings:
    def __init__(self, environment: str, storage_domain: str, endpoint: str | None) -> None:
        self.ENVIRONMENT = environment
        self.STORAGE_DOMAIN = storage_domain
        self.AWS_ENDPOINT_URL = endpoint


def _run(monkeypatch: pytest.MonkeyPatch, settings: _Settings, resolves_to: list[str]) -> None:
    monkeypatch.setattr("app.main.settings", settings)
    monkeypatch.setattr(
        "app.main.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, "", (addr, 0)) for addr in resolves_to],
    )
    _warn_if_storage_endpoint_leaves_the_host()


PROD = dict(environment="production", storage_domain="storage.example.com")


def test_warns_when_the_storage_hostname_resolves_off_the_box(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.main"):
        _run(
            monkeypatch,
            _Settings(**PROD, endpoint="https://storage.example.com"),
            ["104.21.41.91"],
        )

    assert "resolves to a public address" in caplog.text


def test_silent_when_the_alias_is_doing_its_job(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.main"):
        # 172.18.x is the compose bridge -- what the caddy alias resolves to.
        _run(monkeypatch, _Settings(**PROD, endpoint="https://storage.example.com"), ["172.18.0.8"])

    assert caplog.text == ""


def test_silent_for_managed_storage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty STORAGE_DOMAIN means S3 or R2, where public is the only option
    and the warning would be noise on every boot."""
    with caplog.at_level(logging.WARNING, logger="app.main"):
        _run(
            monkeypatch,
            _Settings(
                environment="production",
                storage_domain="",
                endpoint="https://s3.eu-central-1.amazonaws.com",
            ),
            ["52.219.44.1"],
        )

    assert caplog.text == ""


def test_silent_in_dev(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.main"):
        _run(
            monkeypatch,
            _Settings(
                environment="dev", storage_domain="storage.localhost", endpoint="http://minio:9000"
            ),
            ["203.0.113.7"],
        )

    assert caplog.text == ""


def test_resolution_failure_is_not_reported_here(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unresolvable endpoint is a different fault, and the first S3 call
    reports it far more clearly than a startup warning could."""
    monkeypatch.setattr("app.main.settings", _Settings(**PROD, endpoint="https://nope.invalid"))

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError("Name or service not known")

    monkeypatch.setattr("app.main.socket.getaddrinfo", _raise)

    with caplog.at_level(logging.WARNING, logger="app.main"):
        _warn_if_storage_endpoint_leaves_the_host()

    assert caplog.text == ""
