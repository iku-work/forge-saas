"""
ARQ job: renders an email via packages/email-templates (T-106 -- React Email,
replacing T-035's Jinja2 templates) and sends it via Resend, or via plain SMTP
to MailHog when settings.EMAIL_BACKEND == "smtp" (the docker-compose
`mailhog` service -- lets local dev actually see verification/reset/invite
emails without a real Resend account). A failed Resend send retries up to
_MAX_ATTEMPTS times with exponential backoff, but only for transient
failures -- 5xx responses and network errors, both of which the Resend SDK
surfaces as ResendError (see resend.async_request.AsyncRequest.make_request,
which wraps every httpx exception into ResendError(error_type="HttpClientError")).
A 4xx (bad recipient, bad template params) is a permanent failure and isn't
retried. Either way, a final failure is logged + reported to Sentry and the
job returns normally rather than raising -- the worker's max_jobs=20 means
other queued emails run concurrently regardless, but a raised exception here
would still mark this job "failed" for no operational benefit, since there's
no human waiting synchronously on an email send. The SMTP path isn't
retried -- MailHog is a local dev-only catcher, not a real delivery channel.

Rendering itself is *not* wrapped in that same forgiving retry/swallow logic:
a renderer failure (unknown template name, a missing/un-built cli.js, no
`node` binary in the image) is a deploy or programming error, not a
transient delivery problem -- retrying it would just fail identically, so
`_render` is left to raise and mark the ARQ job failed/visible in Sentry
rather than silently never sending the email.

T-068: subject still comes from @forge/i18n's emails.json catalog (read on
the Python side via app.core.i18n.t, keyed by `emails.subject.<template>`)
-- only the *body* moved to the Node renderer. The caller (app.modules.auth.service
/ app.modules.tenancy.service / app.workers.jobs.gdpr) passes
`locale=recipient.locale` so the email actually renders in the recipient's
language, not the server's default.
"""

import asyncio
import json
import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import resend
import sentry_sdk
from resend.exceptions import ResendError

from app.core.config import get_settings
from app.core.i18n import get_default_locale, t
from app.core.module_loader import get_branding

logger = logging.getLogger(__name__)

# packages/email-templates/dist/cli.js lives outside backend/'s own Docker
# build context -- mirrors app.core.i18n's _PACKAGE_LOCALES_DIR /
# _BAKED_LOCALES_DIR split (see that module's docstring for the full
# rationale). _BAKED_EMAIL_RENDERER_CLI is what backend/Dockerfile's COPY
# step and docker-compose.yml's worker volume populate; on a plain host
# checkout (pytest, local `arq` outside Docker) _PACKAGE_EMAIL_RENDERER_CLI
# resolves directly since the whole repo is present.
_PACKAGE_EMAIL_RENDERER_CLI = (
    Path(__file__).resolve().parents[4] / "packages" / "email-templates" / "dist" / "cli.js"
)
_BAKED_EMAIL_RENDERER_CLI = Path(__file__).resolve().parents[3] / "email-templates" / "cli.js"


def _email_renderer_cli_path() -> Path:
    if _PACKAGE_EMAIL_RENDERER_CLI.is_file():
        return _PACKAGE_EMAIL_RENDERER_CLI
    return _BAKED_EMAIL_RENDERER_CLI


async def _render(*, template: str, context: dict[str, Any], locale: str) -> tuple[str, str]:
    """Renders `template` via the Node CLI. Returns (html, text)."""
    branding = get_branding()
    payload = json.dumps(
        {
            "locale": locale,
            "branding": {
                "app_name": branding.app_name,
                "primary_color": branding.primary_color,
            },
            "props": context,
        }
    )
    process = await asyncio.create_subprocess_exec(
        "node",
        str(_email_renderer_cli_path()),
        template,
        payload,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            f"email renderer failed for template={template!r}: {stderr.decode().strip()}"
        )
    rendered = json.loads(stdout)
    return rendered["html"], rendered["text"]


_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0
_SERVER_ERROR_STATUS_THRESHOLD = 500


def _is_retryable(exc: ResendError) -> bool:
    if exc.error_type == "HttpClientError":  # network failure -- see module docstring
        return True
    try:
        return int(exc.code) >= _SERVER_ERROR_STATUS_THRESHOLD
    except (TypeError, ValueError):
        return False


async def _send_via_smtp(*, to: str, subject: str, html: str, text: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.EMAIL_FROM_ADDRESS
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    def _send() -> None:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as smtp:
            smtp.send_message(message)

    # smtplib is synchronous -- offloaded to a thread so it doesn't block the
    # worker's event loop, same as app.workers.jobs.gdpr's boto3 upload.
    await asyncio.to_thread(_send)


async def send_email(
    ctx: dict[str, Any],  # noqa: ARG001 -- ARQ calling convention; registered in app.workers.main
    *,
    to: str,
    template: str,
    context: dict[str, Any],
    locale: str | None = None,
) -> None:
    settings = get_settings()
    effective_locale = locale or get_default_locale()
    html, text = await _render(template=template, context=context, locale=effective_locale)
    subject = t(f"emails.subject.{template}", effective_locale)

    if settings.EMAIL_BACKEND == "smtp":
        await _send_via_smtp(to=to, subject=subject, html=html, text=text)
        return

    resend.api_key = settings.RESEND_API_KEY
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            await resend.Emails.send_async(
                {
                    "from": settings.EMAIL_FROM_ADDRESS,
                    "to": to,
                    "subject": subject,
                    "html": html,
                    "text": text,
                }
            )
        except ResendError as exc:
            if _is_retryable(exc) and attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
                continue
            logger.exception(
                "send_email: giving up after %d attempt(s) (to=%s, template=%s)",
                attempt,
                to,
                template,
            )
            sentry_sdk.capture_exception(exc)
            return
        else:
            return
