"""Whether this process can reach its own object storage without leaving the
host.

The backend and worker address MinIO by the same public hostname the browser
gets, because there is one AWS_ENDPOINT_URL and the presigned URLs built from
it are handed to a browser (see Settings.STORAGE_DOMAIN). docker-compose.prod
.yml closes that gap by giving the caddy service that hostname as a network
alias, so inside the container network the name resolves to a container.

Without the alias the name resolves publicly, and a call between two
containers on one host goes out to the internet, crosses whatever CDN or WAF
fronts the domain, and comes back. On 2026-09-09 that meant Cloudflare's bot
protection answering 403 to roughly 7% of botocore's requests, surfacing only
as uploads failing at the confirm step over a stack trace that pointed at
HeadObject and said nothing about the network path.

This refuses to start rather than warning, because the failure it prevents is
invisible: the stack boots, the health checks pass, most requests work, and
only a fraction of uploads fail. A warning in a log nobody reads is not a
defence against that. ALLOW_PUBLIC_STORAGE_ENDPOINT is the way out for the
deployment where a public address is genuinely correct -- MinIO on a
different host, say -- because "refuses to boot" must never be a dead end.
"""

import logging
import socket
from ipaddress import ip_address
from urllib.parse import urlparse

from app.core.config import Settings
from app.core.module_loader import is_enabled

logger = logging.getLogger(__name__)


def enforce_storage_endpoint_is_reachable_internally(settings: Settings) -> None:
    """Raise when self-hosted object storage is addressed over the internet.

    Silent in dev, when the file_storage module is off, and when
    STORAGE_DOMAIN is empty -- that last one means storage is a managed
    service (S3, R2), where a public address is the only kind there is.
    """
    if settings.ENVIRONMENT == "dev" or settings.ALLOW_PUBLIC_STORAGE_ENDPOINT:
        return
    if not settings.STORAGE_DOMAIN or not is_enabled("file_storage"):
        return
    endpoint = settings.AWS_ENDPOINT_URL
    if not endpoint:
        return
    host = urlparse(endpoint).hostname
    if not host:
        return

    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        # Deliberately not fatal. An endpoint that will not resolve at all is
        # a different fault, and the first S3 call reports it far better than
        # a boot failure here would -- while a transient DNS blip during a
        # restart would otherwise take the process down for no reason.
        logger.warning("Could not resolve the storage endpoint %s to check its route", endpoint)
        return

    public = sorted(addr for addr in addresses if not ip_address(addr).is_private)
    if not public:
        return

    raise RuntimeError(
        f"AWS_ENDPOINT_URL ({endpoint}) resolves to the public address(es) {public}, so this "
        f"process would reach its own object storage over the internet instead of across the "
        f"container network. Every server-side S3 call would then cross whatever CDN or WAF "
        f"fronts that domain, and one that challenges non-browser clients fails a fraction of "
        f"them -- visible only as uploads failing to confirm. Set STORAGE_DOMAIN in the compose "
        f"project's .env (the repo-root file, NOT backend/.env, which compose never reads for "
        f"interpolation) so docker-compose.prod.yml can alias the caddy service to this name. "
        f"If a public address really is correct here, set ALLOW_PUBLIC_STORAGE_ENDPOINT=true."
    )
