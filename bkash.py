"""bKash Checkout (URL based) integration (v1.2.0-beta style endpoints).

Flow summary:
1) Grant token
2) Create payment -> returns bkashURL + paymentID
3) User pays on bkashURL -> bKash calls back to our callbackURL with paymentID + status
4) Execute payment with paymentID -> returns trxID (Completed)
"""

import time
import requests

from config import (
    BKASH_APP_KEY,
    BKASH_APP_SECRET,
    BKASH_BASE_URL,
    BKASH_PASSWORD,
    BKASH_USERNAME,
)


_token_cache = {"id_token": None, "expires_at": 0.0}


def is_configured():
    return bool(
        BKASH_BASE_URL
        and BKASH_USERNAME
        and BKASH_PASSWORD
        and BKASH_APP_KEY
        and BKASH_APP_SECRET
    )


def _grant_token():
    """Returns id_token string."""
    if not is_configured():
        raise RuntimeError("bKash not configured")

    now = time.time()
    if _token_cache["id_token"] and now < (_token_cache["expires_at"] - 30):
        return _token_cache["id_token"]

    url = BKASH_BASE_URL.rstrip("/") + "/tokenized/checkout/token/grant"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "username": BKASH_USERNAME,
        "password": BKASH_PASSWORD,
    }
    payload = {"app_key": BKASH_APP_KEY, "app_secret": BKASH_APP_SECRET}
    res = requests.post(url, json=payload, headers=headers, timeout=30)
    data = res.json() if res.content else {}

    if res.status_code >= 400 or not data.get("id_token"):
        msg = data.get("statusMessage") or data.get("errorMessage") or f"HTTP {res.status_code}"
        raise RuntimeError(f"bKash token grant failed: {msg}")

    expires_in = float(data.get("expires_in") or 3600)
    _token_cache["id_token"] = str(data["id_token"])
    _token_cache["expires_at"] = now + expires_in
    return _token_cache["id_token"]


def create_payment(*, amount, payer_reference, callback_url, merchant_invoice_number):
    """Create payment and return bkashURL + paymentID."""
    if not is_configured():
        return {"ok": False, "error": "bKash configure করা নেই (.env/Render env vars)"}

    try:
        token = _grant_token()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    url = BKASH_BASE_URL.rstrip("/") + "/tokenized/checkout/create"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": token,  # docs show raw id_token
        "X-App-Key": BKASH_APP_KEY,
    }
    payload = {
        "mode": "0011",
        "payerReference": str(payer_reference or "")[:255],
        "callbackURL": str(callback_url),
        "amount": str(amount),
        "currency": "BDT",
        "intent": "sale",
        "merchantInvoiceNumber": str(merchant_invoice_number)[:255],
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=30)
        data = res.json() if res.content else {}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"bKash connect fail: {exc}"}
    except ValueError:
        return {"ok": False, "error": "bKash invalid response"}

    if data.get("statusCode") != "0000" or not data.get("bkashURL") or not data.get("paymentID"):
        return {"ok": False, "error": data.get("statusMessage") or data.get("errorMessage") or "Create payment fail"}

    return {
        "ok": True,
        "gateway_url": data["bkashURL"],
        "payment_id": data["paymentID"],
        "raw": data,
    }


def execute_payment(*, payment_id):
    """Execute payment and return trxID if completed."""
    if not is_configured():
        return {"ok": False, "error": "bKash not configured"}

    try:
        token = _grant_token()
    except Exception as e:
        return {"ok": False, "error": str(e)}

    url = BKASH_BASE_URL.rstrip("/") + "/tokenized/checkout/execute"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": token,
        "X-App-Key": BKASH_APP_KEY,
    }
    payload = {"paymentID": str(payment_id)}

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=30)
        data = res.json() if res.content else {}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"bKash execute connect fail: {exc}"}
    except ValueError:
        return {"ok": False, "error": "bKash execute invalid response"}

    if data.get("statusCode") != "0000":
        return {"ok": False, "error": data.get("statusMessage") or data.get("errorMessage") or "Execute fail", "raw": data}

    if str(data.get("transactionStatus") or "").lower() != "completed":
        return {"ok": False, "error": f"Payment not completed ({data.get('transactionStatus')})", "raw": data}

    trx_id = data.get("trxID") or data.get("trxId") or ""
    if not trx_id:
        return {"ok": False, "error": "trxID missing from bKash response", "raw": data}

    return {"ok": True, "trx_id": str(trx_id), "raw": data}

