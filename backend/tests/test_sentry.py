import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.transport import Transport

from app.core.config import get_settings
from app.core.handlers import register_exception_handlers
from app.core.sentry import _before_send, init_sentry
from app.middleware.request_id import RequestIDMiddleware


class _CapturingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, object]] = []

    def capture_envelope(self, envelope: object) -> None:
        for item in envelope.items:  # type: ignore[attr-defined]
            if item.data_category == "error":
                self.events.append(item.payload.json)


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestIDMiddleware)

    @app.get("/raise-unhandled")
    async def raise_unhandled() -> None:
        raise RuntimeError("boom")

    return app


@pytest.fixture
def restore_global_sentry_client() -> None:
    yield
    init_sentry(get_settings())


def test_before_send_redacts_secret_keys() -> None:
    event = {"extra": {"password": "hunter2", "safe": "ok"}}

    scrubbed = _before_send(event, {})

    assert scrubbed["extra"] == {"password": "***", "safe": "ok"}


def test_init_sentry_noop_when_dsn_unset() -> None:
    settings = get_settings()
    assert not settings.SENTRY_DSN  # .env.test leaves SENTRY_DSN blank

    init_sentry(settings)  # must not raise

    client = sentry_sdk.get_client()
    assert client.transport is None
    assert sentry_sdk.capture_exception(RuntimeError("boom")) is None


@pytest.mark.usefixtures("restore_global_sentry_client")
def test_unhandled_exception_reports_exactly_once_tagged_with_request_id() -> None:
    transport = _CapturingTransport()
    sentry_sdk.init(
        dsn="https://public@example.com/1",
        transport=transport,
        before_send=_before_send,
        integrations=[
            StarletteIntegration(failed_request_status_codes=set()),
            FastApiIntegration(failed_request_status_codes=set()),
        ],
    )

    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.get("/raise-unhandled")

    assert response.status_code == 500
    assert len(transport.events) == 1
    assert transport.events[0]["tags"]["request_id"] == response.json()["meta"]["request_id"]
