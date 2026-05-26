"""SSLCommerz payment gateway (Bangladesh)."""

import secrets
from urllib.parse import urlencode

import requests

from config import (
    ENTRY_FEE,
    SSLCOMMERZ_IS_LIVE,
    SSLCOMMERZ_STORE_ID,
    SSLCOMMERZ_STORE_PASS,
)

SESSION_API = {
    False: "https://sandbox.sslcommerz.com/gwprocess/v4/api.php",
    True: "https://securepay.sslcommerz.com/gwprocess/v4/api.php",
}
VALIDATE_API = {
    False: "https://sandbox.sslcommerz.com/validator/api/validationserverAPI.php",
    True: "https://securepay.sslcommerz.com/validator/api/validationserverAPI.php",
}


def is_configured():
    return bool(SSLCOMMERZ_STORE_ID and SSLCOMMERZ_STORE_PASS)


def make_tran_id(order_id):
    return f"FF{order_id}{secrets.token_hex(3).upper()}"


def parse_order_id(tran_id):
    if not tran_id or not str(tran_id).startswith("FF"):
        return None
    digits = ""
    for ch in str(tran_id)[2:]:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def create_session(
    *,
    order_id,
    amount,
    customer_name,
    customer_phone,
    success_url,
    fail_url,
    cancel_url,
    ipn_url,
    product_name="Tournament Entry",
):
    if not is_configured():
        return {"ok": False, "error": "SSLCommerz configure করা নেই (.env)"}

    tran_id = make_tran_id(order_id)
    payload = {
        "store_id": SSLCOMMERZ_STORE_ID,
        "store_passwd": SSLCOMMERZ_STORE_PASS,
        "total_amount": f"{float(amount):.2f}",
        "currency": "BDT",
        "tran_id": tran_id,
        "success_url": success_url,
        "fail_url": fail_url,
        "cancel_url": cancel_url,
        "ipn_url": ipn_url,
        "cus_name": (customer_name or "Player")[:50],
        "cus_phone": (customer_phone or "01700000000")[:20],
        "cus_email": f"order{order_id}@ff-tournament.local",
        "product_name": product_name[:120],
        "product_category": "Sports",
        "product_profile": "general",
        "shipping_method": "NO",
        "num_of_item": 1,
        "emi_option": 0,
    }

    try:
        res = requests.post(
            SESSION_API[SSLCOMMERZ_IS_LIVE],
            data=payload,
            timeout=30,
        )
        data = res.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": f"Gateway connect fail: {exc}"}
    except ValueError:
        return {"ok": False, "error": "Gateway invalid response"}

    if data.get("status") != "SUCCESS" or not data.get("GatewayPageURL"):
        return {
            "ok": False,
            "error": data.get("failedreason") or data.get("error") or "Session create fail",
        }

    return {
        "ok": True,
        "gateway_url": data["GatewayPageURL"],
        "tran_id": tran_id,
        "session_key": data.get("sessionkey"),
    }


def validate_payment(val_id, expected_amount=None):
    if not is_configured():
        return {"ok": False, "error": "SSLCommerz not configured"}
    if not val_id:
        return {"ok": False, "error": "val_id missing"}

    params = urlencode(
        {
            "val_id": val_id,
            "store_id": SSLCOMMERZ_STORE_ID,
            "store_passwd": SSLCOMMERZ_STORE_PASS,
            "format": "json",
        }
    )
    url = f"{VALIDATE_API[SSLCOMMERZ_IS_LIVE]}?{params}"

    try:
        res = requests.get(url, timeout=30)
        data = res.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}
    except ValueError:
        return {"ok": False, "error": "Invalid validation response"}

    status = (data.get("status") or "").upper()
    if status not in ("VALID", "VALIDATED"):
        return {
            "ok": False,
            "error": data.get("error") or f"Payment {status or 'failed'}",
        }

    amount = float(data.get("amount") or 0)
    if expected_amount is not None:
        expected = float(expected_amount)
        if abs(amount - expected) > 0.01:
            return {"ok": False, "error": "Amount mismatch"}

    tran_id = data.get("tran_id") or ""
    order_id = parse_order_id(tran_id)
    method = (
        data.get("card_type")
        or data.get("card_brand")
        or data.get("card_issuer")
        or "sslcommerz"
    )
    trx = data.get("bank_tran_id") or data.get("tran_id") or val_id

    return {
        "ok": True,
        "order_id": order_id,
        "tran_id": tran_id,
        "trx_id": str(trx),
        "payment_method": str(method).lower()[:40],
        "amount": amount,
        "raw": data,
    }


def expected_entry_fee():
    try:
        return float(ENTRY_FEE)
    except (TypeError, ValueError):
        return 0.0
