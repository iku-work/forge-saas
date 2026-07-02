import json
import logging
from pathlib import Path

import pytest
from loguru import logger

from app.core.config import Settings
from app.core.logging import _is_sensitive_key, _scrub, configure_logging

FIXTURE_ENV_FILE = Path(__file__).parent.parent / ".env.test"


@pytest.fixture(autouse=True)
def _restore_default_logging() -> None:
    yield
    configure_logging()


def _staging_settings() -> Settings:
    return Settings(_env_file=FIXTURE_ENV_FILE, ENVIRONMENT="staging", FORCE_HTTPS=True)


def _last_json_line(captured_err: str) -> dict[str, object]:
    return json.loads(captured_err.strip().splitlines()[-1])


def test_password_key_is_scrubbed_in_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(settings=_staging_settings())

    logger.bind(password="hunter2").info("login attempt")

    record = _last_json_line(capsys.readouterr().err)
    assert record["password"] == "***"
    assert record["message"] == "login attempt"


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


def test_stdlib_custom_level_without_loguru_match_falls_back_to_levelno(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(settings=_staging_settings())
    logging.addLevelName(25, "CUSTOM_LEVEL")

    logging.getLogger("custom").log(25, "custom level message")

    record = _last_json_line(capsys.readouterr().err)
    assert record["message"] == "custom level message"
