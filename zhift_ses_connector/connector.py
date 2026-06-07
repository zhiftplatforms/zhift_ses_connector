"""ZhiftSESConnector — the outbound client for the centralized Zhift SES.

Pure ``requests`` + stdlib. Construct with explicit credentials (or use
``from_site()`` in a Frappe app) and call ``send`` / ``send_batch`` /
``verify_sender`` etc. Failures raise typed exceptions; ``rate_limited`` is
retried automatically with backoff.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import requests

from .signing import canonical_body, sign

# Whitelisted endpoint paths on the zhift_ses server.
SEND_PATH = "/api/method/zhift_ses.api.send.send_marketing_email"
BATCH_PATH = "/api/method/zhift_ses.api.send.send_marketing_email_batch"
VERIFY_SENDER_PATH = "/api/method/zhift_ses.api.send.verify_sender"
SENDER_STATUS_PATH = "/api/method/zhift_ses.api.send.get_sender_status"
LIST_SENDERS_PATH = "/api/method/zhift_ses.api.send.list_senders"
REMOVE_SENDER_PATH = "/api/method/zhift_ses.api.send.remove_sender"


# ── Exceptions ──────────────────────────────────────────────────────────
class ZhiftSESError(Exception):
    """Base class for all connector errors."""


class SESAuthError(ZhiftSESError):
    """Bad/disabled api_key or signature mismatch (HTTP 401/403)."""


class SESTransportError(ZhiftSESError):
    """Network failure or unexpected HTTP/non-JSON response."""


class QuotaExceeded(ZhiftSESError):
    def __init__(self, message: str, remaining: int = 0):
        super().__init__(message)
        self.remaining = remaining


class RateLimited(ZhiftSESError):
    def __init__(self, message: str, retry_after_ms: int = 1000):
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class Suspended(ZhiftSESError):
    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


class SenderNotVerified(ZhiftSESError):
    """from_email is not a verified sender identity for this tenant."""


class ZhiftSESConnector:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        signing_secret: str,
        *,
        timeout: int = 30,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        session: Optional[requests.Session] = None,
    ):
        if not base_url or not api_key or not signing_secret:
            raise ValueError("base_url, api_key and signing_secret are all required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._secret = signing_secret
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._session = session or requests.Session()

    # ── low level ───────────────────────────────────────────────────────
    def _headers(self, raw: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Zhift-Key": self.api_key,
            "X-Zhift-Signature": sign(self._secret, raw),
        }

    def _post(self, path: str, payload: dict) -> dict[str, Any]:
        raw = canonical_body(payload)
        url = self.base_url + path
        try:
            resp = self._session.post(
                url, data=raw.encode("utf-8"), headers=self._headers(raw), timeout=self.timeout
            )
        except requests.RequestException as e:
            raise SESTransportError(f"request to {url} failed: {e}") from e

        if resp.status_code in (401, 403):
            raise SESAuthError(self._err_message(resp) or "authentication failed")
        if resp.status_code >= 400:
            raise SESTransportError(
                f"HTTP {resp.status_code}: {self._err_message(resp) or resp.text[:200]}"
            )
        try:
            body = resp.json()
        except ValueError as e:
            raise SESTransportError(f"non-JSON response: {resp.text[:200]}") from e
        # Frappe wraps a whitelisted return value in {"message": ...}.
        return body.get("message", body) if isinstance(body, dict) else body

    @staticmethod
    def _err_message(resp: requests.Response) -> str:
        try:
            data = resp.json()
        except ValueError:
            return ""
        if not isinstance(data, dict):
            return ""
        msgs = data.get("_server_messages")
        if msgs:
            try:
                arr = json.loads(msgs)
                if arr:
                    first = json.loads(arr[0]) if isinstance(arr[0], str) else arr[0]
                    if isinstance(first, dict):
                        return first.get("message", "")
                    return str(first)
            except (ValueError, TypeError):
                pass
        return data.get("exception") or data.get("message") or ""

    @staticmethod
    def _raise_for_status(result: dict) -> dict:
        status = (result or {}).get("status")
        if status in (None, "sent", "completed", "success"):
            return result
        if status == "quota_exceeded":
            raise QuotaExceeded(result.get("message", "quota exceeded"), remaining=result.get("remaining", 0))
        if status == "rate_limited":
            raise RateLimited(result.get("message", "rate limited"), retry_after_ms=result.get("retry_after_ms", 1000))
        if status == "suspended":
            raise Suspended(result.get("reason", "sending suspended"), reason=result.get("reason", ""))
        if status == "sender_not_verified":
            raise SenderNotVerified(result.get("message", "sender not verified"))
        raise ZhiftSESError(result.get("message", f"request failed: {status}"))

    def _call(self, path: str, payload: dict) -> dict:
        return self._raise_for_status(self._post(path, payload))

    def _call_with_retry(self, path: str, payload: dict) -> dict:
        attempt = 0
        while True:
            try:
                return self._raise_for_status(self._post(path, payload))
            except RateLimited as e:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                delay = max(e.retry_after_ms / 1000.0, self.backoff_base * (2 ** (attempt - 1)))
                time.sleep(delay)

    # ── public API ──────────────────────────────────────────────────────
    def send(
        self,
        *,
        to: str,
        subject: str,
        html_body: str,
        from_email: str,
        from_name: str = "",
        source_type: str = "Broadcast",
        source_name: str = "",
        broadcast_recipient_id: str = "",
        unsubscribe_url: str = "",
    ) -> dict:
        """Send one email. Returns ``{"status": "sent", "message_id": ...}``.

        Raises QuotaExceeded / Suspended / SenderNotVerified / SESAuthError /
        ZhiftSESError on failure. ``rate_limited`` is retried automatically.
        """
        payload = {
            "to": to,
            "subject": subject,
            "html_body": html_body,
            "from_email": from_email,
            "from_name": from_name,
            "source_type": source_type,
            "source_name": source_name,
            "broadcast_recipient_id": broadcast_recipient_id,
            "unsubscribe_url": unsubscribe_url,
        }
        return self._call_with_retry(SEND_PATH, payload)

    def send_batch(self, emails: list[dict]) -> dict:
        """Send many emails in one call. ``emails`` is a list of dicts with the
        same keys as :meth:`send`. Returns
        ``{"status": "completed", "results": [{to, status, message_id|message}, ...]}``
        — inspect per-email ``status`` (the call itself only raises on
        auth/transport/quota failures, not per-recipient errors).
        """
        return self._call_with_retry(BATCH_PATH, {"emails": emails})

    def verify_sender(self, identity: str, identity_type: str = "Domain") -> dict:
        """Register a sender domain or email. For a domain, the returned
        ``dns_records`` (+ ``spf_record`` / ``dmarc_record``) must be published
        in DNS before sending. ``identity_type`` is ``"Domain"`` or ``"Email"``.
        """
        return self._call(VERIFY_SENDER_PATH, {"identity": identity, "identity_type": identity_type})

    def get_sender_status(self, identity: str) -> dict:
        """Return current verification status for a sender identity."""
        return self._call(SENDER_STATUS_PATH, {"identity": identity})

    def list_senders(self) -> dict:
        """List this tenant's sender identities."""
        return self._call(LIST_SENDERS_PATH, {})

    def remove_sender(self, identity: str) -> dict:
        """Remove a sender identity from this tenant."""
        return self._call(REMOVE_SENDER_PATH, {"identity": identity})
