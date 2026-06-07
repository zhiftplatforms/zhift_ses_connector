# Zhift SES Connector

A thin **connection SDK** for the centralized Zhift SES email service (backed by
Postmark). It handles the parts that are identical for every tenant —
**request signing, the wire contract, retries, and inbound relay
verification** — so each application can build its own client logic on top
without re-implementing (or mis-implementing) the security-critical bits.

This README is intended to be the **complete** reference a tenant team needs to
integrate. You should not need to read the server source.

---

## 1. Architecture in one picture

```
                   send (signed)                relay event (signed)
  ┌──────────────┐  ───────────────►  ┌──────────────┐  ───────────────►  ┌──────────────┐
  │ Your app     │   X-Zhift-Key      │  zhift_ses    │   X-Zhift-Key      │ Your app     │
  │ (tenant)     │   X-Zhift-Signature│  (central)    │   X-Zhift-Signature│ receive_event│
  │              │  ◄───────────────  │  → Postmark   │                    │  endpoint    │
  └──────────────┘   {status,...}     └──────────────┘                     └──────────────┘
        │                                                                          ▲
        └──────────────────────── this connector ──────────────────────────────────┘
```

- **zhift_ses** is a single **centralized server**. It owns the Postmark
  account, all sending, deliverability tracking, and per-tenant quota
  enforcement. It is **not** installed in your bench.
- Your app authenticates with a **per-tenant credential** (`api_key` +
  `signing_secret`). Every request is signed; the server derives your
  `tenant_site` from the credential, so you can only ever act as yourself.
- Events (delivery, bounce, complaint, open, click, …) are **relayed back** to
  an endpoint you expose. Those relays are **also signed** with your
  `signing_secret`, so you can verify they really came from zhift_ses.

The connector gives you a `ZhiftSESConnector` for the outbound direction and a
`verify_relay()` helper for the inbound direction. **What** you send, **when**,
and how you handle events is your app's job.

---

## 2. Installation

The connector is a normal pip package in a **public** git repo. Declare it as a
dependency of **your** Frappe app and let bench install it — no auth, deploy key,
or token required.

In your app's `pyproject.toml`:

```toml
dependencies = [
    "zhift-ses-connector @ git+https://github.com/zhiftplatforms/zhift_ses_connector.git@v0.1.0",
]
```

Then, on each bench:

```bash
bench setup requirements        # installs/updates into the bench's env/
```

Notes:
- Pin to a **tag** (`@v0.1.0`). The wire contract is append-only, so older
  connector versions keep working — upgrade by bumping the tag and redeploying.
- Public repo: a plain HTTPS clone works on every bench with no credentials.
- For local development you can install editable: `./env/bin/pip install -e
  /path/to/zhift_ses_connector`.
- Runtime dependency is just `requests`. The crypto core uses only the stdlib.

---

## 3. Getting credentials

Credentials are **per tenant site** and are provisioned on the zhift_ses server
by an operator (System Manager), not self-served. The operator runs:

```
zhift_ses.api.admin.create_tenant_credential(tenant_site="https://crm.daggo.com",
                                              label="CRM360 – Daggo")
```

which returns, **once**:

```json
{
  "status": "success",
  "tenant_site": "https://crm.daggo.com",
  "api_key": "…40 chars…",
  "signing_secret": "…64 chars…",
  "note": "Store the signing_secret now — it cannot be retrieved again."
}
```

The `signing_secret` is stored encrypted on the server and **cannot be read
back**. If it is lost, the operator runs `rotate_tenant_credential(tenant_site)`
to issue a new pair (the old one stops working immediately).

Related operator endpoints: `rotate_tenant_credential`,
`set_credential_enabled(tenant_site, enabled)`, `list_tenant_credentials`.

---

## 4. Configuration

Put the three values in your **site's** `site_config.json` (per tenant site):

```json
{
  "zhift_ses_url": "https://agent.zhiftplatforms.com",
  "zhift_ses_api_key": "…40 chars…",
  "zhift_ses_signing_secret": "…64 chars…"
}
```

> Keep `signing_secret` only in `site_config.json` (or your secrets manager) —
> never in app code or version control.

---

## 5. Quick start (sending)

```python
from zhift_ses_connector.frappe_adapter import from_site
from zhift_ses_connector import QuotaExceeded, Suspended, SenderNotVerified, ZhiftSESError

ses = from_site()   # reads site_config.json

try:
    result = ses.send(
        to="customer@example.com",
        subject="Your order shipped",
        html_body="<p>It's on the way 🚚</p>",
        from_email="store@crm.daggo.com",     # must be a VERIFIED sender (see §8)
        from_name="Daggo Store",
        source_type="Broadcast",              # free-form label for your reporting
        source_name="ship-notif-2026-06",
        broadcast_recipient_id="BR-00123",    # echoed back on every relayed event
        unsubscribe_url="https://crm.daggo.com/unsubscribe?t=abc",
    )
    print(result["message_id"])               # Postmark MessageID
except SenderNotVerified:
    ...   # verify the sender first
except QuotaExceeded as e:
    ...   # e.remaining
except Suspended as e:
    ...   # e.reason — deliverability/billing
except ZhiftSESError as e:
    ...   # anything else
```

Outside a Frappe app, construct it directly:

```python
from zhift_ses_connector import ZhiftSESConnector
ses = ZhiftSESConnector(base_url, api_key, signing_secret)
```

---

## 6. Connector API reference

### `ZhiftSESConnector(base_url, api_key, signing_secret, *, timeout=30, max_retries=3, backoff_base=0.5, session=None)`

- `timeout` — per-request seconds.
- `max_retries` — automatic retries on `rate_limited` (with exponential
  backoff honoring the server's `retry_after_ms`). Other failures are **not**
  retried.
- `session` — pass a shared `requests.Session` to reuse connections in batch
  jobs.

### `send(*, to, subject, html_body, from_email, from_name="", source_type="Broadcast", source_name="", broadcast_recipient_id="", unsubscribe_url="") -> dict`
Sends one email. Returns `{"status": "sent", "message_id": "..."}`. Raises on
failure (see §7). All args are keyword-only.

### `send_batch(emails: list[dict]) -> dict`
`emails` is a list of dicts with the same keys as `send`. Returns
`{"status": "completed", "results": [{"to", "status", "message_id"|"message"}, ...], "total": N}`.
The call only raises on auth/transport/quota-level failures; **per-recipient
problems appear as that recipient's `status`** in `results` (`sent`,
`sender_not_verified`, or `error`) — always inspect them.

### `verify_sender(identity, identity_type="Domain") -> dict`
Registers a sender. `identity_type` is `"Domain"` or `"Email"`. For a domain,
the response includes `dns_records`, `spf_record`, `dmarc_record` you must
publish. See §8.

### `get_sender_status(identity) -> dict`
Returns `{"status":"success","exists":bool,"identity_type":...,"verified":bool,...}`.

### `list_senders() -> dict`
Returns `{"status":"success","identities":[...]}` for your tenant.

### `remove_sender(identity) -> dict`
Removes a sender identity from your tenant.

---

## 7. Response statuses & exceptions

The server replies with a `status`. The connector maps them so you handle
outcomes as exceptions instead of string-checking:

| status                | Exception            | Notes / attributes                         |
|-----------------------|----------------------|--------------------------------------------|
| `sent` / `completed` / `success` | *(none — returns dict)* |                                |
| `quota_exceeded`      | `QuotaExceeded`      | `.remaining`                               |
| `rate_limited`        | `RateLimited`        | `.retry_after_ms` — auto-retried first     |
| `suspended`           | `Suspended`          | `.reason` (deliverability or billing)      |
| `sender_not_verified` | `SenderNotVerified`  | `from_email` isn't a verified identity     |
| any other / `error`   | `ZhiftSESError`      | base class                                 |
| HTTP 401/403          | `SESAuthError`       | bad/disabled key or signature mismatch     |
| network / non-JSON    | `SESTransportError`  |                                            |

All inherit from `ZhiftSESError`, so a single `except ZhiftSESError` is a valid
catch-all.

---

## 8. Verifying a sender (required before sending)

You can only send from an identity your tenant has **verified**. Unverified
`from_email` ⇒ `SenderNotVerified`.

```python
res = ses.verify_sender("crm.daggo.com", identity_type="Domain")
for rec in res["dns_records"]:
    print(rec["type"], rec["name"], rec["value"])   # publish these in DNS
print("SPF:",   res["spf_record"])
print("DMARC:", res["dmarc_record"])
```

Publish the returned DNS records (DKIM + Return-Path), plus SPF/DMARC. Then poll:

```python
ses.get_sender_status("crm.daggo.com")   # verified flips True once DNS propagates
```

The server also re-checks verification state daily. A domain identity authorizes
any `from_email` on that domain; an email identity authorizes just that address.

---

## 9. Receiving events (the relay back to you)

zhift_ses POSTs every event to a path **you** expose. Two steps:

### 9a. Tell zhift_ses where to deliver

Ask the operator to set `event_callback_path` on your credential, e.g.
`/api/method/crm360.api.ses_events.receive_event`. Events are POSTed to
`tenant_site + event_callback_path`.

### 9b. Implement the endpoint and verify the signature

The relay is signed with your `signing_secret`. **Always verify before
trusting it.** Frappe example:

```python
import frappe
from zhift_ses_connector.relay import verify_relay, parse_event
from zhift_ses_connector.frappe_adapter import site_signing_secret

@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive_event():
    raw = frappe.request.get_data(as_text=True)          # EXACT bytes, do not re-parse first
    sig = frappe.get_request_header("X-Zhift-Signature")
    if not verify_relay(site_signing_secret(), raw, sig):
        frappe.local.response["http_status_code"] = 401
        return {"status": "unauthorized"}

    event = parse_event(raw)
    # ... your business logic: update the broadcast recipient, suppress on
    #     bounce/complaint, record opens/clicks, etc. ...
    return {"status": "ok"}      # return HTTP 200 so the server marks it delivered
```

Return **HTTP 200** on success. Non-200 makes zhift_ses mark the relay failed; it
retries hourly for up to 3 days.

### 9c. Event payload schema

```json
{
  "event_type": "Delivery",            // see values below
  "message_id": "postmark-message-id", // ties back to your send() result
  "recipient_email": "customer@example.com",
  "broadcast_recipient_id": "BR-00123",// whatever you passed to send()
  "bounce_type": "",                   // set when event_type == "Bounce"
  "complaint_type": "",                // set when event_type == "Complaint"
  "link_url": "",                      // set when event_type == "Click"
  "user_agent": "",                    // set for Open / Click
  "timestamp": "2026-06-07 10:30:00"
}
```

`event_type` is one of: **`Delivery`, `Bounce`, `Complaint`, `Open`, `Click`,
`SubscriptionChange`**.

Guidance:
- **Dedupe by `message_id` + `event_type`** — an event may be delivered more
  than once (retries). Make your handler idempotent.
- On `Bounce`/`Complaint`, suppress the recipient on your side. (The server also
  auto-suspends a tenant whose rolling rates get too high.)
- `broadcast_recipient_id` is the value **you** chose at send time — use it to
  attribute events without a lookup.

---

## 10. Security model

- **Signing**: `X-Zhift-Signature = hex( HMAC_SHA256(signing_secret, raw_body) )`,
  computed over the **exact UTF-8 bytes** of the request body. The connector's
  `canonical_body()` serializes with compact separators and `ensure_ascii=False`
  and signs *that* string — never let an HTTP layer re-serialize the body, or
  the signature won't match.
- **Identification**: `X-Zhift-Key` carries your `api_key`. The server looks up
  the (enabled) credential, verifies the signature with its secret, and derives
  `tenant_site` from it. You **cannot** spoof another tenant.
- **Same secret, both directions**: outbound sends and inbound relays use the
  same per-tenant `signing_secret`. Rotating it (operator) re-keys both.
- **Storage**: keep `signing_secret` in `site_config.json`/secrets only. It is
  unrecoverable from the server after provisioning.
- **Transport**: always use HTTPS `base_url`.

---

## 11. Rate limits, retries, batching

- The server enforces a global and a **per-tenant** per-second cap. Over the
  cap ⇒ `rate_limited`; the connector auto-retries `send`/`send_batch` up to
  `max_retries` with backoff.
- For large volumes, prefer `send_batch` (the server paces inter-send delay for
  you) and reuse one connector instance (it keeps a `requests.Session`).
- Quota is per billing period and decremented per email; `quota_exceeded`
  carries `.remaining`.

---

## 12. Worked example: a minimal app client

A complete per-app client is usually just config + a thin wrapper:

```python
# myapp/email_client.py
from functools import lru_cache
from zhift_ses_connector.frappe_adapter import from_site

@lru_cache(maxsize=1)
def ses():
    return from_site()

def send_order_update(customer_email, order, html):
    return ses().send(
        to=customer_email,
        subject=f"Update on order {order.name}",
        html_body=html,
        from_email=order.company_sender,     # a verified identity
        source_type="OrderUpdate",
        source_name=order.name,
        broadcast_recipient_id=order.name,
        unsubscribe_url=order.unsubscribe_url,
    )
```

Pair it with the `receive_event` endpoint from §9 and you have a full
integration: signing, retries, and relay verification all handled by the
connector; everything app-specific stays in your app.

---

## 13. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `SESAuthError: Invalid API key` | wrong/disabled `api_key`, or credential not provisioned for this site |
| `SESAuthError: Signature mismatch` | body re-serialized after signing; not using `canonical_body`/connector; wrong `signing_secret` |
| `SenderNotVerified` | `from_email` (or its domain) not verified for this tenant — run `verify_sender` and publish DNS |
| `Suspended` | deliverability thresholds tripped or billing inactive — contact operator |
| relay `401` on your endpoint | you're verifying against the wrong secret, or parsing the body before reading raw bytes |
| events never arrive | `event_callback_path` not set on your credential, or your endpoint isn't `allow_guest` / returns non-200 |

---

## 14. Versioning

Semantic versioning via git tags. The wire contract is **append-only** (new
fields/statuses may be added; existing ones won't change meaning), so pinning an
older tag stays safe. Upgrade deliberately by bumping the tag in your app's
`pyproject.toml`.
