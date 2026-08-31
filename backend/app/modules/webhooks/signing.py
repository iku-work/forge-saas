"""
Request signing for outbound webhooks (T-320).

The header a consumer verifies is

    X-Forge-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "{t}.{raw body}">

alongside `X-Forge-Event` and `X-Forge-Delivery`. The scheme is Stripe's, and
copying it is the point: an integrator who has verified a Stripe webhook has
already written this code once, and the versioned `v1=` prefix is what lets a
second algorithm be added later without every existing consumer breaking on
the day it is.

**Sign the raw bytes, never a re-serialised dict.** This is the one rule in
this file that costs people days. A consumer verifies against the bytes it
received; if the producer signs `json.dumps(payload)` and then sends a
*different* serialisation of the same object -- different key order, a space
after a comma, a re-encoded unicode escape -- every signature fails and
nothing in either log says why. So `build_signature` takes `bytes`, the
delivery job serialises exactly once, and the bytes it signs are the bytes it
sends.

The timestamp is inside the signed string rather than beside it, which is what
makes it tamper-proof and therefore usable as a replay defence:
`SIGNATURE_TOLERANCE_SECONDS` is the window we document for consumers, and
`verify_signature` implements it so the template's own tests -- and anyone
writing a consumer against this product -- have a reference implementation
that is known to agree with the producer.
"""

from __future__ import annotations

import hashlib
import hmac
import time

SIGNATURE_HEADER = "X-Forge-Signature"
EVENT_HEADER = "X-Forge-Event"
DELIVERY_HEADER = "X-Forge-Delivery"
# Documented for consumers, and enforced by verify_signature below: a
# signature older than this is rejected, so a captured request cannot be
# replayed tomorrow. Five minutes is Stripe's default tolerance and is
# generous enough to survive ordinary clock drift between two servers.
SIGNATURE_TOLERANCE_SECONDS = 300

_SCHEME = "v1"


def signed_message(timestamp: int, body: bytes) -> bytes:
    """The exact byte string the HMAC is computed over: `"{t}.{body}"`.

    Separate from `build_signature` so a test -- and a consumer's own
    implementation -- can assert on the construction rather than only on the
    digest, which is what makes a mismatch debuggable.
    """
    return f"{timestamp}.".encode() + body


def compute_digest(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), signed_message(timestamp, body), hashlib.sha256).hexdigest()


def build_signature(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """The full `X-Forge-Signature` value for one request."""
    stamp = int(time.time()) if timestamp is None else timestamp
    return f"t={stamp},{_SCHEME}={compute_digest(secret, stamp, body)}"


def parse_signature(header: str) -> tuple[int, list[str]] | None:
    """`(timestamp, [digest, ...])`, or None if the header is not shaped like
    a signature at all.

    A list of digests rather than one, because a header may legitimately carry
    several `v1=` parts -- that is how a producer rotating its secret signs
    one request with both, and this implementation does exactly that during
    the rotation overlap (see WebhookEndpoint.secret_previous).
    """
    timestamp: int | None = None
    digests: list[str] = []
    for part in header.split(","):
        name, separator, value = part.strip().partition("=")
        if separator == "":
            return None
        if name == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return None
        elif name == _SCHEME:
            digests.append(value)
    if timestamp is None or not digests:
        return None
    return timestamp, digests


def verify_signature(
    secret: str,
    body: bytes,
    header: str,
    *,
    now: int | None = None,
    tolerance: int = SIGNATURE_TOLERANCE_SECONDS,
) -> bool:
    """The reference consumer-side check, used by this repo's own tests and by
    the delivery-log documentation.

    Deliberately shipped rather than only described: "verify the HMAC" is the
    instruction every integration guide gives and roughly half of them get
    subtly wrong -- `==` instead of a constant-time compare, verifying a
    re-serialised body, or skipping the timestamp entirely and accepting a
    replayed request forever.
    """
    parsed = parse_signature(header)
    if parsed is None:
        return False
    timestamp, digests = parsed
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > tolerance:
        return False
    expected = compute_digest(secret, timestamp, body)
    # compare_digest against each candidate, and `any` over the results rather
    # than a short-circuiting loop with an early return -- the timing of the
    # comparison itself is what is being protected, not the number of
    # candidates, which is public.
    return any(hmac.compare_digest(expected, digest) for digest in digests)


def signature_headers(secrets: list[str], body: bytes, timestamp: int | None = None) -> str:
    """One header value carrying a `v1=` part per secret, in the order given.

    During a rotation overlap the endpoint has two valid secrets and this
    signs with both, so a consumer that has already switched to the new one
    and a consumer that has not are both satisfied by the same request. That
    is the whole mechanism behind "rotate without downtime" -- there is no
    dual-delivery, just a second digest.
    """
    stamp = int(time.time()) if timestamp is None else timestamp
    parts = [f"t={stamp}"]
    parts.extend(f"{_SCHEME}={compute_digest(secret, stamp, body)}" for secret in secrets)
    return ",".join(parts)
