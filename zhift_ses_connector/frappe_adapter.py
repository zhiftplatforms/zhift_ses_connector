"""Optional Frappe convenience layer.

Lets a Frappe app build a connector straight from site_config.json instead
of threading credentials through code. Importing this module does NOT import
Frappe at module load — only when ``from_site()`` is actually called — so the
connector core stays framework-free.

Expected site_config.json keys (set per tenant site at provisioning):

    "zhift_ses_url":            "https://agent.zhiftplatforms.com",
    "zhift_ses_api_key":        "<api_key from create_tenant_credential>",
    "zhift_ses_signing_secret": "<signing_secret from create_tenant_credential>"
"""

from __future__ import annotations

from typing import Any

from .connector import ZhiftSESConnector


def from_site(**overrides: Any) -> ZhiftSESConnector:
    """Build a connector from the current site's config.

    Any of base_url / api_key / signing_secret can be overridden via kwargs;
    otherwise they are read from site_config.json.
    """
    import frappe

    conf = frappe.conf
    base_url = overrides.get("base_url") or conf.get("zhift_ses_url")
    api_key = overrides.get("api_key") or conf.get("zhift_ses_api_key")
    signing_secret = overrides.get("signing_secret") or conf.get("zhift_ses_signing_secret")
    if not (base_url and api_key and signing_secret):
        raise ValueError(
            "Missing zhift_ses_url / zhift_ses_api_key / zhift_ses_signing_secret in site_config.json"
        )
    kwargs = {k: v for k, v in overrides.items() if k in ("timeout", "max_retries", "backoff_base", "session")}
    return ZhiftSESConnector(base_url, api_key, signing_secret, **kwargs)


def site_signing_secret() -> str:
    """Return this site's signing_secret (for verify_relay in receive endpoints)."""
    import frappe

    secret = frappe.conf.get("zhift_ses_signing_secret")
    if not secret:
        raise ValueError("zhift_ses_signing_secret is not set in site_config.json")
    return secret
