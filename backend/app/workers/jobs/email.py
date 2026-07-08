"""
ARQ job: renders an email via packages/email-templates (T-106 -- React Email,
replacing T-035's Jinja2 templates) and sends it via Resend, or via plain SMTP
to MailHog when settings.EMAIL_BACKEND == "smtp" (the docker-compose
`mailhog` service -- lets local dev actually see verification/reset/invite
emails without a real Resend account). Either way, a final delivery failure
is logged + reported to Sentry and the job returns normally rather than
raising -- the worker's max_jobs=20 means other queued emails run
concurrently regardless, but a raised exception here would still mark this
job "failed" for no operational benefit, since there's no human waiting
synchronously on an email send. The SMTP path isn't retried -- MailHog is a
local dev-only catcher, not a real delivery channel.

T-206: the actual Resend call (`_send_via_resend`) goes through
app.core.resilience's `@resilient("resend")` -- transient failures (5xx,
network errors, both of which the Resend SDK surfaces as ResendError; see
resend.async_request.AsyncRequest.make_request, which wraps every httpx
exception into ResendError(error_type="HttpClientError")) are retried a few
times inline, and once the shared "resend" breaker has actually tripped
(repeated transient failures across *many* send_email calls, not just this
one), the ExternalServiceUnavailableError it raises is turned into an
arq.worker.Retry here -- the whole job gets re-queued with backoff rather
than the email being dropped, per T-206's "Resend down -- email jobs
re-queue with ARQ backoff; emails delayed, never dropped" degraded-mode
contract. A permanent (4xx) failure is neither retried nor re-queued -- it's
logged + reported to Sentry and the job still returns normally, same as
before.

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
from arq.worker import Retry
from resend.exceptions import ResendError

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceUnavailableError
from app.core.i18n import get_default_locale, t
from app.core.metrics import FORGE_EMAILS_SENT_TOTAL
from app.core.module_loader import get_branding
from app.core.resilience import resilient
from app.core.tracing import trace_job

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


# >= app.core.resilience's reset_timeout (30s): re-queuing sooner than the
# breaker could possibly have recovered would just spend an ARQ retry on
# another guaranteed CircuitBreakerError.
_RESEND_RETRY_DEFER_SECONDS = 30


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


@resilient("resend")
async def _send_via_resend(*, to: str, subject: str, html: str, text: str) -> None:
    await resend.Emails.send_async(
        {
            "from": get_settings().EMAIL_FROM_ADDRESS,
            "to": to,
            "subject": subject,
            "html": html,
            "text": text,
        }
    )


@trace_job
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
        FORGE_EMAILS_SENT_TOTAL.inc()
        return

    resend.api_key = settings.RESEND_API_KEY
    try:
        await _send_via_resend(to=to, subject=subject, html=html, text=text)
    except ExternalServiceUnavailableError:
        # The "resend" breaker is open -- re-queue the whole job rather than
        # dropping the email; see module docstring.
        raise Retry(defer=_RESEND_RETRY_DEFER_SECONDS) from None
    except ResendError as exc:
        logger.exception("send_email: giving up (to=%s, template=%s)", to, template)
        sentry_sdk.capture_exception(exc)
        return
    else:
        FORGE_EMAILS_SENT_TOTAL.inc()
