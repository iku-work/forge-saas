"""
Verifies T-106's send_email ARQ job: rendering is delegated to
packages/email-templates' compiled CLI via a Node subprocess (`_render`),
and that boundary is mocked here rather than exercised for real -- the same
treatment Resend/SMTP already get below. Real rendering (locale handling,
escaping, brand colors, template content) is covered by that package's own
vitest suite (packages/email-templates/src/render.test.ts); this file only
covers send_email's own responsibilities: invoking `_render` with the right
template/context/locale, computing the subject from @forge/i18n's catalog,
retrying transient (5xx / network) Resend failures (T-206's
app.core.resilience `@resilient("resend")`, exercised here rather than
mocked, since it's the actual retry mechanism now), giving up without
retrying a permanent (4xx) failure, never raising on a final *delivery*
failure (logged + reported to Sentry instead), re-queuing the whole job via
arq.worker.Retry once the "resend" breaker itself trips open, and -- unlike
a delivery failure -- letting a renderer failure propagate (see _render's
own docstring note in email.py for why). Also covers the EMAIL_BACKEND="smtp"
path used to reach MailHog in dev.
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from arq.worker import Retry
from resend.exceptions import ResendError

from app.core.config import get_settings
from app.workers.jobs import email as email_job


class _FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr


@pytest.fixture
def mock_render(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Mocks the `_render` boundary itself -- not used by this file's two
    tests of `_render` directly, which instead mock one layer further down
    (the subprocess call `_render` makes)."""
    render = AsyncMock(return_value=("<p>rendered html</p>", "rendered text"))
    monkeypatch.setattr(email_job, "_render", render)
    return render


@pytest.fixture
def captured_sentry(monkeypatch: pytest.MonkeyPatch) -> Mock:
    capture = Mock()
    monkeypatch.setattr(email_job.sentry_sdk, "capture_exception", capture)
    return capture


def _server_error(code: int | str = 503) -> ResendError:
    return ResendError(
        code=code, error_type="service_unavailable", message="down", suggested_action="retry"
    )


def _network_error() -> ResendError:
    return ResendError(
        code=500, error_type="HttpClientError", message="connection reset", suggested_action="retry"
    )


def _validation_error() -> ResendError:
    return ResendError(
        code=422,
        error_type="validation_error",
        message="invalid `to` field",
        suggested_action="fix the request",
    )


# --- _email_renderer_cli_path: dual-path resolution (mirrors app.core.i18n) --


def test_email_renderer_cli_path_prefers_package_dir_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Both paths are monkeypatched, like the fallback test below, because the
    # real _PACKAGE_EMAIL_RENDERER_CLI points at packages/email-templates/dist/
    # -- a gitignored build output. Asserting against the unpatched constant
    # made this test pass only on a machine that had run `pnpm build` at some
    # point, and fail on any clean checkout: CI's test job never builds the
    # frontend, so it saw the fallback branch and a green local run said
    # nothing about it. What is worth testing here is the branch, not whether
    # somebody happens to have a dist directory.
    package = tmp_path / "package" / "cli.js"
    package.parent.mkdir(parents=True)
    package.touch()
    monkeypatch.setattr(email_job, "_PACKAGE_EMAIL_RENDERER_CLI", package)
    monkeypatch.setattr(email_job, "_BAKED_EMAIL_RENDERER_CLI", tmp_path / "baked" / "cli.js")

    assert email_job._email_renderer_cli_path() == package


def test_email_renderer_cli_path_falls_back_to_baked_path_when_package_dir_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "does-not-exist" / "cli.js"
    baked = tmp_path / "baked" / "cli.js"
    monkeypatch.setattr(email_job, "_PACKAGE_EMAIL_RENDERER_CLI", missing)
    monkeypatch.setattr(email_job, "_BAKED_EMAIL_RENDERER_CLI", baked)

    assert email_job._email_renderer_cli_path() == baked


# --- _render: the Node subprocess boundary -----------------------------------


async def test_render_invokes_node_cli_with_locale_branding_and_props(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_subprocess_exec(*args: Any, **kwargs: Any) -> _FakeProcess:  # noqa: ANN401
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProcess(b'{"html": "<p>hi</p>", "text": "hi"}', b"", 0)

    monkeypatch.setattr(email_job.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    html, text = await email_job._render(
        template="welcome", context={"app_url": "https://app.example.com/dashboard"}, locale="fi"
    )

    assert html == "<p>hi</p>"
    assert text == "hi"
    node_bin, cli_path, template_arg, payload_arg = captured["args"]
    assert node_bin == "node"
    assert str(cli_path).endswith("cli.js")
    assert template_arg == "welcome"
    assert json.loads(payload_arg) == {
        "locale": "fi",
        "branding": {
            "app_name": email_job.get_branding().app_name,
            "primary_color": email_job.get_branding().primary_color,
        },
        "props": {"app_url": "https://app.example.com/dashboard"},
    }


async def test_render_raises_on_nonzero_exit_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_subprocess_exec(*_args: Any, **_kwargs: Any) -> _FakeProcess:  # noqa: ANN401
        return _FakeProcess(b"", b'unknown template "bogus"', 1)

    monkeypatch.setattr(email_job.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RuntimeError, match="unknown template"):
        await email_job._render(template="bogus", context={}, locale="en")


# --- send_email: subject + render invocation ---------------------------------


async def test_send_email_invokes_render_and_sends_its_output(
    monkeypatch: pytest.MonkeyPatch, mock_render: AsyncMock
) -> None:
    send = AsyncMock(return_value={"id": "evt_1"})
    monkeypatch.setattr(email_job.resend.Emails, "send_async", send)

    await email_job.send_email(
        {},
        to="alice@example.com",
        template="verify_email",
        context={"verification_url": "https://app.example.com/verify?token=abc123"},
    )

    mock_render.assert_awaited_once_with(
        template="verify_email",
        context={"verification_url": "https://app.example.com/verify?token=abc123"},
        locale="en",
    )
    send.assert_awaited_once()
    params: dict[str, Any] = send.await_args.args[0]
    assert params["to"] == "alice@example.com"
    assert params["subject"] == "Verify your email address"
    assert params["html"] == "<p>rendered html</p>"
    assert params["text"] == "rendered text"


async def test_send_email_passes_context_through_to_render_unmodified(
    mock_render: AsyncMock,
) -> None:
    context = {"inviter_name": "<script>alert(1)</script>", "org_name": "Acme"}

    await email_job.send_email(
        {}, to="erin@example.com", template="invitation", context=dict(context)
    )

    # Escaping is the renderer's job now (see packages/email-templates) --
    # send_email forwards the caller's context verbatim, unmodified.
    assert mock_render.await_args.kwargs["context"] == context


async def test_send_email_defaults_to_default_locale_when_omitted(
    monkeypatch: pytest.MonkeyPatch, mock_render: AsyncMock
) -> None:
    send = AsyncMock(return_value={"id": "evt_locale_default"})
    monkeypatch.setattr(email_job.resend.Emails, "send_async", send)

    await email_job.send_email(
        {}, to="alice@example.com", template="welcome", context={"app_url": "https://x/dash"}
    )

    assert mock_render.await_args.kwargs["locale"] == "en"
    params: dict[str, Any] = send.await_args.args[0]
    assert params["subject"] == "Welcome to ForgeSaaS"


async def test_send_email_renders_subject_in_requested_locale(
    monkeypatch: pytest.MonkeyPatch, mock_render: AsyncMock
) -> None:
    send = AsyncMock(return_value={"id": "evt_locale_fi"})
    monkeypatch.setattr(email_job.resend.Emails, "send_async", send)

    await email_job.send_email(
        {},
        to="erin@example.com",
        template="invitation",
        locale="fi",
        context={"org_name": "Acme", "accept_url": "https://x/a"},
    )

    assert mock_render.await_args.kwargs["locale"] == "fi"
    params: dict[str, Any] = send.await_args.args[0]
    assert params["subject"] == "Sinut on kutsuttu liittymään organisaatioon"


async def test_render_failure_propagates_and_never_reaches_resend(
    monkeypatch: pytest.MonkeyPatch, mock_render: AsyncMock
) -> None:
    mock_render.side_effect = RuntimeError("email renderer failed for template='welcome': boom")
    send = AsyncMock()
    monkeypatch.setattr(email_job.resend.Emails, "send_async", send)

    with pytest.raises(RuntimeError, match="boom"):
        await email_job.send_email(
            {}, to="dave@example.com", template="welcome", context={"app_url": "https://x/dash"}
        )

    send.assert_not_called()


# --- Resend retry/backoff (T-206: app.core.resilience's @resilient("resend")) --


async def test_retries_on_transient_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    mock_render: AsyncMock,
    captured_sentry: Mock,
) -> None:
    send = AsyncMock(side_effect=[_server_error(503), {"id": "evt_2"}])
    monkeypatch.setattr(email_job.resend.Emails, "send_async", send)

    await email_job.send_email(
        {}, to="bob@example.com", template="magic_link", context={"login_url": "https://x/l"}
    )

    assert send.await_count == 2
    # _render is called once per send_email invocation, not once per Resend
    # attempt -- a retried delivery resends the same already-rendered html.
    mock_render.assert_awaited_once()
    captured_sentry.assert_not_called()


async def test_retries_network_error_classified_as_transient(
    monkeypatch: pytest.MonkeyPatch,
    mock_render: AsyncMock,
    captured_sentry: Mock,
) -> None:
    send = AsyncMock(side_effect=[_network_error(), {"id": "evt_3"}])
    monkeypatch.setattr(email_job.resend.Emails, "send_async", send)

    await email_job.send_email(
        {}, to="carol@example.com", template="welcome", context={"app_url": "https://x/dash"}
    )

    assert send.await_count == 2
    mock_render.assert_awaited_once()
    captured_sentry.assert_not_called()


async def test_gives_up_after_max_attempts_without_raising(
    monkeypatch: pytest.MonkeyPatch,
    mock_render: AsyncMock,
    captured_sentry: Mock,
) -> None:
    send = AsyncMock(side_effect=_server_error(503))
    monkeypatch.setattr(email_job.resend.Emails, "send_async", send)

    await email_job.send_email(
        {}, to="dave@example.com", template="password_reset", context={"reset_url": "https://x/r"}
    )

    assert send.await_count == 3  # resilient()'s default retries
    mock_render.assert_awaited_once()
    captured_sentry.assert_called_once()


async def test_permanent_error_fails_fast_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
    mock_render: AsyncMock,
    captured_sentry: Mock,
) -> None:
    send = AsyncMock(side_effect=_validation_error())
    monkeypatch.setattr(email_job.resend.Emails, "send_async", send)

    await email_job.send_email(
        {},
        to="erin@example.com",
        template="invitation",
        context={"org_name": "Acme", "accept_url": "https://x/a"},
    )

    send.assert_awaited_once()
    mock_render.assert_awaited_once()
    captured_sentry.assert_called_once()


async def test_breaker_open_requeues_the_job_instead_of_dropping_the_email(
    monkeypatch: pytest.MonkeyPatch,
    mock_render: AsyncMock,  # noqa: ARG001 -- required so send_email skips the real Node subprocess
    captured_sentry: Mock,
) -> None:
    send = AsyncMock(side_effect=_server_error(503))
    monkeypatch.setattr(email_job.resend.Emails, "send_async", send)

    # 5 separate send_email calls, each exhausting its own internal retries
    # and failing -- exactly enough real (transient) failures to trip the
    # shared "resend" breaker (fail_max=5). Each behaves like
    # test_gives_up_after_max_attempts_without_raising above.
    for i in range(5):
        await email_job.send_email(
            {}, to=f"user{i}@example.com", template="password_reset", context={"reset_url": "x"}
        )
    captured_sentry.reset_mock()
    send.reset_mock()

    with pytest.raises(Retry) as exc_info:
        await email_job.send_email(
            {}, to="last@example.com", template="password_reset", context={"reset_url": "x"}
        )

    assert exc_info.value.defer_score == email_job._RESEND_RETRY_DEFER_SECONDS * 1000
    # The breaker rejected the call outright -- Resend was never even hit.
    send.assert_not_called()
    captured_sentry.assert_not_called()


# --- SMTP backend (dev / MailHog) ---------------------------------------------


async def test_smtp_backend_sends_via_smtp_not_resend(
    monkeypatch: pytest.MonkeyPatch, mock_render: AsyncMock
) -> None:
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    get_settings.cache_clear()
    send_resend = AsyncMock()
    monkeypatch.setattr(email_job.resend.Emails, "send_async", send_resend)
    sent_messages = []
    smtp_instance = Mock()
    smtp_instance.send_message.side_effect = sent_messages.append
    smtp_instance.__enter__ = Mock(return_value=smtp_instance)
    smtp_instance.__exit__ = Mock(return_value=False)
    smtp_class = Mock(return_value=smtp_instance)
    monkeypatch.setattr(email_job.smtplib, "SMTP", smtp_class)

    try:
        await email_job.send_email(
            {},
            to="frank@example.com",
            template="welcome",
            context={"app_url": "https://x/dash"},
        )
    finally:
        get_settings.cache_clear()

    send_resend.assert_not_called()
    mock_render.assert_awaited_once()
    smtp_class.assert_called_once_with(
        get_settings().SMTP_HOST, get_settings().SMTP_PORT, timeout=10
    )
    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert message["To"] == "frank@example.com"
    assert message["Subject"] == "Welcome to ForgeSaaS"
    assert message.is_multipart()
    plain_part = message.get_body(("plain",))
    html_part = message.get_body(("html",))
    assert plain_part is not None
    assert html_part is not None
    assert plain_part.get_content().strip() == "rendered text"
    assert html_part.get_content().strip() == "<p>rendered html</p>"
