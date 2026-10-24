import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError

from app.core.exceptions import NotFoundError
from app.core.handlers import register_exception_handlers


class _Payload(BaseModel):
    name: str


class _PgError(Exception):
    """Stands in for asyncpg's error as SQLAlchemy wraps it: what the handler
    reads is the SQLSTATE."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def _build_test_app() -> FastAPI:
    test_app = FastAPI()
    register_exception_handlers(test_app)

    @test_app.get("/raise-not-found")
    async def raise_not_found() -> None:
        # "errors.widget_not_found" is deliberately not a real catalog key --
        # exercises the missing-key -> fallback path (§T-068) the same way a
        # one-off DomainError without a matching translation would in
        # production, while keeping this test's asserted message stable.
        raise NotFoundError(
            "errors.widget_not_found", fallback="widget not found", details={"id": "abc"}
        )

    @test_app.get("/raise-known-error-key")
    async def raise_known_error_key() -> None:
        raise NotFoundError("errors.org_not_found", fallback="Organization not found")

    @test_app.post("/validate")
    async def validate(payload: _Payload) -> dict[str, str]:
        return {"name": payload.name}

    @test_app.get("/raise-db/{sqlstate}")
    async def raise_db(sqlstate: str) -> None:
        raise DBAPIError("SELECT 1", None, _PgError(sqlstate))

    @test_app.get("/raise-unhandled")
    async def raise_unhandled() -> None:
        raise RuntimeError("boom")

    return test_app


def test_domain_error_returns_envelope_with_status_and_code() -> None:
    client = TestClient(_build_test_app())

    response = client.get("/raise-not-found")

    assert response.status_code == 404
    body = response.json()
    assert body["data"] is None
    assert body["error"] == {
        "code": "not_found",
        "message_key": "errors.widget_not_found",
        "message": "widget not found",
        "details": {"id": "abc"},
    }
    assert body["meta"]["request_id"]


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_domain_error_with_missing_key_logs_warning_and_uses_fallback() -> None:
    # Not caplog: any test file in this suite that runs a real alembic
    # upgrade (alembic/env.py calls logging.config.fileConfig, which defaults
    # disable_existing_loggers=True) permanently disables every logger that
    # already existed, "app.core.handlers" included, for the rest of the
    # session -- caplog's capture relies on that logger's propagation/
    # disabled state and silently sees nothing once that's happened. Same
    # workaround as tests/committed/test_worker_scheduler.py's scheduler_log_messages
    # fixture: attach a handler directly and force disabled=False.
    target = logging.getLogger("app.core.handlers")
    handler = _ListHandler()
    previous_level, previous_disabled = target.level, target.disabled
    target.addHandler(handler)
    target.setLevel(logging.WARNING)
    target.disabled = False
    try:
        client = TestClient(_build_test_app())
        response = client.get("/raise-not-found")
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)
        target.disabled = previous_disabled

    assert response.json()["error"]["message"] == "widget not found"
    assert any("errors.widget_not_found" in message for message in handler.messages)


def test_domain_error_with_real_key_translates_in_default_locale() -> None:
    client = TestClient(_build_test_app())

    response = client.get("/raise-known-error-key")

    assert response.json()["error"]["message"] == "Organization not found."


def test_domain_error_translates_using_request_state_locale() -> None:
    test_app = _build_test_app()

    @test_app.middleware("http")
    async def _set_locale(request, call_next):  # type: ignore[no-untyped-def]
        request.state.locale = "fi"
        return await call_next(request)

    client = TestClient(test_app)
    response = client.get("/raise-known-error-key")

    assert response.json()["error"]["message"] == "Organisaatiota ei löytynyt."


def test_validation_error_returns_422_envelope() -> None:
    client = TestClient(_build_test_app())

    response = client.post("/validate", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["errors"]


def test_unhandled_exception_returns_500_with_no_traceback() -> None:
    client = TestClient(_build_test_app(), raise_server_exceptions=False)

    response = client.get("/raise-unhandled")

    assert response.status_code == 500
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "internal_error"
    assert "boom" not in response.text
    assert "Traceback" not in response.text


def test_wrong_method_returns_405_and_logs_warning() -> None:
    # T-225/ASVS V14.5.1: same caplog workaround as
    # test_domain_error_with_missing_key_logs_warning_and_uses_fallback above.
    target = logging.getLogger("app.core.handlers")
    handler = _ListHandler()
    previous_level, previous_disabled = target.level, target.disabled
    target.addHandler(handler)
    target.setLevel(logging.WARNING)
    target.disabled = False
    try:
        client = TestClient(_build_test_app())
        response = client.post("/raise-not-found")  # a GET-only route
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)
        target.disabled = previous_disabled

    assert response.status_code == 405
    assert any("405" in message and "/raise-not-found" in message for message in handler.messages)


def test_404_does_not_log_a_405_warning() -> None:
    target = logging.getLogger("app.core.handlers")
    handler = _ListHandler()
    previous_level, previous_disabled = target.level, target.disabled
    target.addHandler(handler)
    target.setLevel(logging.WARNING)
    target.disabled = False
    try:
        client = TestClient(_build_test_app())
        response = client.get("/this-route-does-not-exist")
    finally:
        target.removeHandler(handler)
        target.setLevel(previous_level)
        target.disabled = previous_disabled

    assert response.status_code == 404
    assert not any("405" in message for message in handler.messages)


def test_request_id_echoed_when_already_set_on_request_state() -> None:
    test_app = _build_test_app()

    @test_app.middleware("http")
    async def _set_request_id(request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = "fixed-request-id"
        return await call_next(request)

    client = TestClient(test_app)
    response = client.get("/raise-not-found")

    assert response.json()["meta"]["request_id"] == "fixed-request-id"


def test_a_nul_byte_postgres_refuses_is_a_422_not_a_500() -> None:
    # SQLSTATE 22021: Postgres refusing a NUL byte in text. Schemathesis found it
    # through GET /files?bucket=%00.
    client = TestClient(_build_test_app(), raise_server_exceptions=False)

    response = client.get("/raise-db/22021")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_any_other_database_error_is_still_a_500() -> None:
    client = TestClient(_build_test_app(), raise_server_exceptions=False)

    response = client.get("/raise-db/40001")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
