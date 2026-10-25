"""Shared helpers for the PRD §18 spike probes. Throwaway tooling, not product code.

Rules kept even here: money values are strings (R4), POSTs carry a PayPal-Request-Id and retries
reuse it (R5), secrets are never printed. Product code must not import this module.
"""

import base64
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_ENV = Path(__file__).resolve().parents[3] / ".env"
RETRIES = 3
SPIKE_AMOUNT = "1.00"  # USD string (R4); every money-moving probe sends this
TIMEOUT_S = 60
# The probes send OAuth credentials and can create payouts: only ever talk to the PayPal sandbox.
SANDBOX_BASE_URLS = frozenset(
    {"https://api-m.sandbox.paypal.com", "https://api.sandbox.paypal.com"}
)


def sandbox_base_url(url):
    """Return the sandbox base URL or exit. Exact HTTPS allowlist: no live, plain HTTP or lookalike hosts."""
    base = str(url or "").strip().rstrip("/")
    if base not in SANDBOX_BASE_URLS:
        sys.exit(
            f"refusing PAYPAL_BASE_URL {base!r}: spikes run only against {sorted(SANDBOX_BASE_URLS)}"
        )
    return base


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect could carry credentials off the allowlisted host; surface it as an HTTPError instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def load_env(path=DEFAULT_ENV):
    """Parse KEY=VALUE lines; strips quotes and trailing ' # comment'."""
    env = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.split(" #")[0].strip().strip("'\"")
    return env


class Sandbox:
    def __init__(self, env=None):
        self.env = env or load_env()
        self.base = sandbox_base_url(self.env.get("PAYPAL_BASE_URL"))
        self.run = secrets.token_hex(3)  # makes ids unique per run

    def call(self, method, path, token=None, basic=None, body=None, form=None, request_id=None):
        """Returns (status, paypal-debug-id, json body). Retries network errors with the SAME request id."""
        headers = {"Accept": "application/json"}
        data = None
        if basic:
            headers["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
        if token:
            headers["Authorization"] = "Bearer " + token
        if form is not None:
            data = form.encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        for attempt in range(RETRIES):
            try:
                with _OPENER.open(req, timeout=TIMEOUT_S) as resp:
                    raw = resp.read().decode()
                    return (
                        resp.status,
                        resp.headers.get("paypal-debug-id"),
                        json.loads(raw) if raw else {},
                    )
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode()
                try:
                    return exc.code, exc.headers.get("paypal-debug-id"), json.loads(raw)
                except ValueError:
                    return exc.code, exc.headers.get("paypal-debug-id"), {"raw": raw[:300]}
            except (urllib.error.URLError, TimeoutError):
                if attempt == RETRIES - 1:
                    raise
                print(f"  retry {attempt + 1}")

    def token(self, app):
        """app = 'VENDOR' or 'TREASURY'. Returns (access_token, scope list)."""
        cred = f"{self.env[app + '_CLIENT_ID']}:{self.env[app + '_CLIENT_SECRET']}"
        status, _, body = self.call(
            "POST", "/v1/oauth2/token", basic=cred, form="grant_type=client_credentials"
        )
        if status != 200:
            sys.exit(f"oauth {app} failed: HTTP {status}")
        return body["access_token"], body.get("scope", "").split()

    @staticmethod
    def brief(label, res):
        status, debug_id, body = res
        extra = ""
        if status >= 400:
            extra = (
                " "
                + json.dumps(
                    {
                        "name": body.get("name"),
                        "message": body.get("message"),
                        "details": body.get("details"),
                    }
                )[:500]
            )
        print(f"[{label}] HTTP {status} debug-id={debug_id}{extra}")
        return res

    def new_invoice(self, token, total="10.00", partial=False, send=True):
        """Vendor-app invoice to the treasury email. Returns invoice id."""
        invoice = {
            "detail": {
                "invoice_number": f"SPIKE-{self.run}",
                "currency_code": "USD",
                "payment_term": {"term_type": "NET_10"},
            },
            "invoicer": {"email_address": self.env["VENDOR_ACCOUNT_EMAIL"]},
            "primary_recipients": [
                {"billing_info": {"email_address": self.env["TREASURY_ACCOUNT_EMAIL"]}}
            ],
            "items": [
                {
                    "name": "Spike item",
                    "quantity": "1",
                    "unit_of_measure": "QUANTITY",
                    "unit_amount": {"currency_code": "USD", "value": total},
                }
            ],
        }
        if partial:
            invoice["configuration"] = {
                "partial_payment": {
                    "allow_partial_payment": True,
                    "minimum_amount_due": {"currency_code": "USD", "value": "1.00"},
                }
            }
        status, _, body = self.brief(
            "create invoice",
            self.call(
                "POST",
                "/v2/invoicing/invoices",
                token=token,
                body=invoice,
                request_id=f"aegis-spike-{self.run}-create",
            ),
        )
        if status >= 400:
            sys.exit(1)
        invoice_id = body["href"].rsplit("/", 1)[-1]
        if send:
            self.brief(
                "send invoice",
                self.call(
                    "POST",
                    f"/v2/invoicing/invoices/{invoice_id}/send",
                    token=token,
                    body={"send_to_invoicer": False, "send_to_recipient": False},
                    request_id=f"aegis-spike-{self.run}-send",
                ),
            )
        return invoice_id

    def invoice_state(self, token, invoice_id, label):
        status, debug_id, body = self.call(
            "GET", f"/v2/invoicing/invoices/{invoice_id}", token=token
        )
        print(
            f"[{label}] HTTP {status} status={body.get('status')} total={body.get('amount', {}).get('value')} "
            f"paid={body.get('payments', {}).get('paid_amount', {}).get('value')} "
            f"due={body.get('due_amount', {}).get('value')} debug-id={debug_id}"
        )
        return status, body

    def payout_body(self, receiver, value, tag, recipient_type="EMAIL"):
        """One-item USD payout body; sender_batch_id is fixed per (tag, run)."""
        return {
            "sender_batch_header": {
                "sender_batch_id": f"AEGIS-SPIKE-{tag}-{self.run}",
                "email_subject": f"AegisPay spike {tag}",
            },
            "items": [
                {
                    "recipient_type": recipient_type,
                    "receiver": receiver,
                    "amount": {"value": value, "currency": "USD"},
                    "sender_item_id": f"spike-{tag}-{self.run}-1",
                    "note": f"spike {tag}",
                }
            ],
        }

    def payout(self, token, receiver, value, tag, recipient_type="EMAIL"):
        """Create a one-item USD payout. Returns batch id."""
        body = self.payout_body(receiver, value, tag, recipient_type)
        status, _, resp = self.brief(
            "create payout",
            self.call(
                "POST",
                "/v1/payments/payouts",
                token=token,
                body=body,
                request_id=f"aegis-spike-{tag}-{self.run}",
            ),
        )
        if status >= 400:
            sys.exit(1)
        batch_id = resp["batch_header"]["payout_batch_id"]
        print(f"  batch_id: {batch_id} batch_status: {resp['batch_header']['batch_status']}")
        return batch_id

    def poll_payout(self, token, batch_id, until_item, until_batch=None, limit_s=300, every_s=5):
        """Poll a batch, print each (batch, item) status change with elapsed seconds.
        Stops when the item status is in until_item (and the batch status in until_batch, if given)."""
        start, last, item_id = time.time(), None, None
        while time.time() - start < limit_s:
            _, _, body = self.call("GET", f"/v1/payments/payouts/{batch_id}", token=token)
            item = (body.get("items") or [{}])[0]
            item_id = item.get("payout_item_id", item_id)
            current = (
                body.get("batch_header", {}).get("batch_status"),
                item.get("transaction_status"),
            )
            if current != last:
                print(
                    f"  t+{time.time() - start:5.1f}s batch_status={current[0]} item_status={current[1]}"
                )
                last = current
            if current[1] in until_item and (until_batch is None or current[0] in until_batch):
                break
            time.sleep(every_s)
        return item_id, last
