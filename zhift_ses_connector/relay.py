"""Inbound relay verification — used by each tenant's receive_event endpoint.

The zhift_ses server relays delivery/bounce/open/click/etc. events back to
the tenant, signing the request with that tenant's signing_secret. The
tenant's endpoint must verify the signature before trusting the event.
"""

from __future__ import annotations

import json
from typing import Any

from .signing import verify as _verify


def verify_relay(signing_secret: str, raw_body: str, signature: str) -> bool:
    """True if ``signature`` (the ``X-Zhift-Signature`` header) matches an
    HMAC-SHA256 of ``raw_body`` keyed by this tenant's ``signing_secret``.

    Pass the EXACT raw request body (in Frappe:
    ``frappe.request.get_data(as_text=True)``), not a re-serialized dict.
    """
    return _verify(signing_secret, raw_body, signature)


def parse_event(raw_body: str) -> dict[str, Any]:
    """Parse a verified relay body into the event dict (see README for schema)."""
    return json.loads(raw_body)
