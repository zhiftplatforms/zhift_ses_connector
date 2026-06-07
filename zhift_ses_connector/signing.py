"""Request/relay signing primitives — the shared cryptographic core.

This module has NO framework dependency so it can be unit-tested in
isolation and reused identically on both ends of the wire:

  * the connector signs OUTBOUND send requests, and
  * the tenant's receive endpoint verifies INBOUND relayed events.

The rule that makes signatures match: sign the EXACT bytes you transmit.
``canonical_body`` produces the string the caller must both sign and send
as the raw HTTP body — never let the HTTP layer re-serialize it.
"""

from __future__ import annotations

import hashlib
import hmac
import json


def canonical_body(payload: dict) -> str:
    """Serialize a payload to the exact JSON string that gets signed AND sent.

    Compact separators + ``ensure_ascii=False`` keep the body small and
    UTF-8 clean; the server re-hashes the raw bytes it receives, so the only
    requirement is that this same string is what goes on the wire.
    """
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def sign(secret: str, raw: str) -> str:
    """Return the hex HMAC-SHA256 of ``raw`` (a str) keyed by ``secret``."""
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def verify(secret: str, raw: str, signature: str) -> bool:
    """Constant-time check that ``signature`` matches HMAC-SHA256(secret, raw)."""
    expected = sign(secret, raw)
    return hmac.compare_digest(expected, (signature or "").strip())
