"""
Structured logging (Loguru), shared by every module.

Both `from loguru import logger` and stdlib `logging.getLogger(__name__)`
(used in encrypted_type.py, workers/jobs/encryption.py, etc.) end up on the
same sink via the intercept handler below, so every log line gets the same
JSON shape, the same PII scrubbing, and whatever request_id / user_id /
org_id is bound on the ambient Loguru context (see
middleware/request_id.py's `logger.contextualize(...)`).

Log level convention (§0.6): ERROR pages someone, WARNING is reviewed,
INFO is the audit trail, DEBUG is dev-only.
"""

import json
import logging
import sys
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from opentelemetry import trace

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from loguru import Message, Record, RecordException

_SENSITIVE_KEY_MARKERS = (
    "password",
    "token",
    "secret",
    "api_key",
    "authorization",
    "cookie",
)
_REDACTED = "***"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _scrub(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _REDACTED if _is_sensitive_key(str(key)) else _scrub(val)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _scrubbed_extra(record: "Record") -> dict[str, object]:
    scrubbed = _scrub(dict(record["extra"]))
    return scrubbed if isinstance(scrubbed, dict) else {}


def _active_trace_ids() -> dict[str, str]:
    """
    T-203: so a log line emitted inside a traced request/job can be pasted
    straight into the trace viewer. Empty outside any span (no active
    request/job, or tracing's OTLP endpoint unset) -- get_span_context() on
    the no-op span INVALID_SPAN present in that case reports is_valid=False.
    """
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }


def _exception_text(exc: "RecordException") -> str:
    return "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))


def _json_sink(message: "Message") -> None:
    record = message.record
    payload: dict[str, object] = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        **_active_trace_ids(),
        **_scrubbed_extra(record),
    }
    exception = record["exception"]
    if exception is not None:
        payload["exception"] = _exception_text(exception)
    sys.stderr.write(json.dumps(payload, default=str) + "\n")


def _pretty_sink(message: "Message") -> None:
    record = message.record
    extra: dict[str, object] = {**_active_trace_ids(), **_scrubbed_extra(record)}
    extra_str = " ".join(f"{key}={value}" for key, value in extra.items())
    line = f"{record['time'].isoformat()} | {record['level'].name:<8} | {record['message']}"
    if extra_str:
        line += f" | {extra_str}"
    sys.stderr.write(line + "\n")
    exception = record["exception"]
    if exception is not None:
        sys.stderr.write(_exception_text(exception))


def _is_admin_action(record: "Record") -> bool:
    return bool(record["extra"].get("admin_action"))


def _admin_audit_sink_factory(path: str) -> Callable[["Message"], None]:
    """
    Separate file sink for superadmin actions (T-140), on top of whatever the
    normal log stream already wrote via _json_sink/_pretty_sink -- same
    manual-JSON-write shape as those two (see _json_sink above) rather than
    Loguru's own `serialize=True`, so admin-audit.log's line shape matches the
    rest of this app's logs instead of Loguru's default JSON structure.
    """

    def sink(message: "Message") -> None:
        record = message.record
        payload: dict[str, object] = {
            "timestamp": record["time"].isoformat(),
            "level": record["level"].name,
            "message": record["message"],
            **_scrubbed_extra(record),
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")

    return sink


class _InterceptHandler(logging.Handler):
    """Forwards stdlib `logging` records into Loguru so they share its sinks."""

    def emit(self, record: logging.LogRecord) -> None:
        level: int | str
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(exception=record.exc_info).log(level, record.getMessage())


def configure_logging(*, settings: Settings | None = None) -> None:
    settings = settings or get_settings()

    logger.remove()
    if settings.ENVIRONMENT == "dev":
        logger.add(_pretty_sink, level=settings.LOG_LEVEL)
    else:
        logger.add(_json_sink, level=settings.LOG_LEVEL)
    logger.add(
        _admin_audit_sink_factory(settings.ADMIN_AUDIT_LOG_PATH),
        level="INFO",
        filter=_is_admin_action,
    )

    # Route stdlib `logging` (uvicorn, sqlalchemy, third-party libs, and our
    # own logging.getLogger(__name__) call sites) through the same sinks.
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
