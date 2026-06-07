"""Zhift SES Connector — connection essentials for the centralized Zhift SES.

Apps build their own client logic on top of this. See README.md for the
full wire contract, configuration, and worked examples.
"""

from .connector import (
    BATCH_PATH,
    LIST_SENDERS_PATH,
    REMOVE_SENDER_PATH,
    SEND_PATH,
    SENDER_STATUS_PATH,
    VERIFY_SENDER_PATH,
    QuotaExceeded,
    RateLimited,
    SenderNotVerified,
    SESAuthError,
    SESTransportError,
    Suspended,
    ZhiftSESConnector,
    ZhiftSESError,
)
from .relay import parse_event, verify_relay
from .signing import canonical_body, sign, verify

__version__ = "0.1.0"

__all__ = [
    "ZhiftSESConnector",
    "ZhiftSESError",
    "SESAuthError",
    "SESTransportError",
    "QuotaExceeded",
    "RateLimited",
    "Suspended",
    "SenderNotVerified",
    "verify_relay",
    "parse_event",
    "canonical_body",
    "sign",
    "verify",
    "SEND_PATH",
    "BATCH_PATH",
    "VERIFY_SENDER_PATH",
    "SENDER_STATUS_PATH",
    "LIST_SENDERS_PATH",
    "REMOVE_SENDER_PATH",
    "__version__",
]
