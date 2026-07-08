import json
import logging
from pathlib import Path

import pytest
from loguru import logger
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

from app.core.config import Settings
from app.core.logging import _is_sensitive_key, _scrub, configure_logging

FIXTURE_ENV_FILE = Path(__file__).parent.parent / ".env.test"


@pytest.fixture(autouse=True)
def _restore_default_logging() -> None:
    yield
    configure_logging()


def _staging_settings(admin_audit_log_path: str | None = None) -> Settings:
    kwargs: dict[str, object] = {}
    if admin_audit_log_path is not None:
        kwargs["ADMIN_AUDIT_LOG_PATH"] = admin_audit_log_path
    return Settings(
        _env_file=FIXTURE_ENV_FILE,
        ENVIRONMENT="staging",
        FORCE_HTTPS=True,
        **kwargs,  # type: ignore[arg-type]
    )


def _last_json_line(captured_err: str) -> dict[str, object]:
    return json.loads(captured_err.strip().splitlines()[-1])


@pytest.mark.owasp("A02")
def test_password_key_is_scrubbed_in_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings=_staging_settings())

    logger.bind(password="hunter2").info("login attempt")

    record = _last_json_line(capsys.readouterr().err)
    assert record["password"] == "***"
    assert record["message"] == "login attempt"


@pytest.mark.owasp("A02")
def test_token_key_is_scrubbed_in_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings=_staging_settings())

    logger.bind(refresh_token="abc123").info("token issued")

    record = _last_json_line(capsys.readouterr().err)
    assert record["refresh_token"] == "***"


def test_user_id_key_is_not_scrubbed(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings=_staging_settings())

    logger.bind(user_id="user-1", org_id="org-1").info("request handled")

    record = _last_json_line(capsys.readouterr().err)
    assert record["user_id"] == "user-1"
    assert record["org_id"] == "org-1"


def test_request_id_from_contextvars_is_carried_into_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(settings=_staging_settings())

    with logger.contextualize(request_id="req-123"):
        logger.info("inside request")

    record = _last_json_line(capsys.readouterr().err)
    assert record["request_id"] == "req-123"


def test_active_span_trace_and_span_id_are_carried_into_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # T-203: a log line emitted inside a traced request/job can be pasted
    # straight into the trace viewer. A standalone TracerProvider (not the
    # process-wide one app.main's configure_tracing sets) is enough here --
    # opentelemetry.trace.get_current_span() reads whatever span is active in
    # context regardless of which provider created it.
    configure_logging(settings=_staging_settings())
    tracer = TracerProvider(sampler=ALWAYS_ON).get_tracer("test")

    with tracer.start_as_current_span("test-span") as span:
        logger.info("inside span")
        span_context = span.get_span_context()

    record = _last_json_line(capsys.readouterr().err)
    assert record["trace_id"] == format(span_context.trace_id, "032x")
    assert record["span_id"] == format(span_context.span_id, "016x")


def test_no_trace_id_outside_any_span(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings=_staging_settings())

    logger.info("no active span")

    record = _last_json_line(capsys.readouterr().err)
    assert "trace_id" not in record
    assert "span_id" not in record


def test_nested_secret_is_scrubbed_recursively(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings=_staging_settings())

    logger.bind(payload={"api_key": "sk_live_secret", "plan": "pro"}).info("webhook received")

    record = _last_json_line(capsys.readouterr().err)
    payload = record["payload"]
    assert isinstance(payload, dict)
    assert payload["api_key"] == "***"
    assert payload["plan"] == "pro"


def test_stdlib_logging_is_routed_through_same_sink(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings=_staging_settings())

    logging.getLogger("some.third.party.module").info("plain stdlib log line")

    record = _last_json_line(capsys.readouterr().err)
    assert record["message"] == "plain stdlib log line"


def test_dev_environment_uses_pretty_sink_not_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings=Settings(_env_file=FIXTURE_ENV_FILE, ENVIRONMENT="dev"))

    logger.bind(user_id="user-1").info("dev log line")

    line = capsys.readouterr().err.strip().splitlines()[-1]
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)
    assert "dev log line" in line
    assert "user_id=user-1" in line


def test_is_sensitive_key_matches_known_markers() -> None:
    assert _is_sensitive_key("password")
    assert _is_sensitive_key("refresh_token")
    assert _is_sensitive_key("API_KEY")
    assert _is_sensitive_key("Authorization")
    assert _is_sensitive_key("cookie")
    assert not _is_sensitive_key("user_id")
    assert not _is_sensitive_key("org_id")


def test_scrub_redacts_nested_dicts_and_lists() -> None:
    scrubbed = _scrub({"token": "abc", "nested": [{"secret": "xyz", "ok": 1}]})
    assert scrubbed == {"token": "***", "nested": [{"secret": "***", "ok": 1}]}


def test_exception_traceback_is_included_in_json_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(settings=_staging_settings())

    try:
        raise ValueError("boom")  # noqa: TRY301 -- deliberately raised to capture a traceback
    except ValueError:
        logger.exception("something failed")

    record = _last_json_line(capsys.readouterr().err)
    exception_text = record["exception"]
    assert isinstance(exception_text, str)
    assert "ValueError: boom" in exception_text


def test_admin_action_is_written_to_admin_audit_log_path(tmp_path: Path) -> None:
    log_path = tmp_path / "admin-audit.log"
    configure_logging(settings=_staging_settings(admin_audit_log_path=str(log_path)))

    logger.bind(admin_action=True, actor_id="admin-1", path="/api/v1/admin/orgs/x").info(
        "admin.access"
    )

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["actor_id"] == "admin-1"
    assert record["path"] == "/api/v1/admin/orgs/x"
    assert record["message"] == "admin.access"


def test_admin_action_also_reaches_the_normal_log_stream(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "admin-audit.log"
    configure_logging(settings=_staging_settings(admin_audit_log_path=str(log_path)))

    logger.bind(admin_action=True, actor_id="admin-1").info("admin.access")

    record = _last_json_line(capsys.readouterr().err)
    assert record["message"] == "admin.access"
    assert log_path.exists()


def test_non_admin_log_lines_are_not_written_to_admin_audit_log(tmp_path: Path) -> None:
    log_path = tmp_path / "admin-audit.log"
    configure_logging(settings=_staging_settings(admin_audit_log_path=str(log_path)))

    logger.info("plain log line, no admin_action")

    assert not log_path.exists()


def test_stdlib_custom_level_without_loguru_match_falls_back_to_levelno(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(settings=_staging_settings())
    logging.addLevelName(25, "CUSTOM_LEVEL")

    logging.getLogger("custom").log(25, "custom level message")

    record = _last_json_line(capsys.readouterr().err)
    assert record["message"] == "custom level message"
