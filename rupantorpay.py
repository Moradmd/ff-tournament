"""RupantorPay gateway — https://rupantorpay.com"""

import json

import requests

from config import ENTRY_FEE, RUPANTORPAY_API_KEY, RUPANTORPAY_CLIENT

CHECKOUT_URL = "https://payment.rupantorpay.com/api/payment/checkout"
VERIFY_URL = "https://payment.rupantorpay.com/api/payment/verify-payment"


def is_configured():
    return bool(RUPANTORPAY_API_KEY)


def _headers(client_host):
    host = (client_host or RUPANTORPAY_CLIENT or "127.0.0.1").strip()
    return {
        "X-API-KEY": RUPANTORPAY_API_KEY,
        "Content-Type": "application/json",
        "X-CLIENT": host,
    }


def _amount_str(amount):
    try:
        val = float(amount)
    except (TypeError, ValueError):
        val = float(ENTRY_FEE or 0)
    if val == int(val):
        return str(int(val))
    return f"{val:.2f}".rstrip("0").rstrip(".")


def create_checkout(
    *,
    order_id,
    amount,
    fullname,
    email,
    phone,
    success_url,
    cancel_url,
    webhook_url,
    client_host=None,
):
    if not is_configured():
        return {"ok": False, "error": "RupantorPay API key দাও (.env)"}

    payload = {
        "fullname": (fullname or "Player")[:80],
        "email": (email or f"order{order_id}@ff-tournament.local")[:120],
        "amount": _amount_str(amount),
        "success_url": success_url,
        "cancel_url": cancel_url,
        "webhook_url": webhook_url,
        "metadata": {
            "order_id": order_id,
            "phone": (phone or "")[:20],
        },
    }

    try:
        res = requests.post(
            CHECKOUT_URL,
            headers=_headers(client_host),
            json=payload,
            timeout=30,
        )
        data = res.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": f"RupantorPay connect fail: {exc}"}
    except ValueError:
        return {"ok": False, "error": "RupantorPay invalid response"}

    if data.get("status") in (1, True, "1") and data.get("payment_url"):
        return {
            "ok": True,
            "gateway_url": data["payment_url"],
            "message": data.get("message"),
        }

    return {
        "ok": False,
        "error": data.get("message") or "Payment link create fail",
    }


def verify_payment(transaction_id, expected_amount=None):
    if not is_configured():
        return {"ok": False, "error": "RupantorPay not configured"}
    if not transaction_id:
        return {"ok": False, "error": "transaction_id missing"}

    try:
        res = requests.post(
            VERIFY_URL,
            headers={
                "X-API-KEY": RUPANTORPAY_API_KEY,
                "Content-Type": "application/json",
            },
            json={"transaction_id": transaction_id},
            timeout=30,
        )
        data = res.json()
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}
    except ValueError:
        return {"ok": False, "error": "Invalid verify response"}

    if data.get("status") is False:
        return {"ok": False, "error": data.get("message") or "Verify failed"}

    pay_status = str(data.get("status") or "").upper()
    if pay_status not in ("COMPLETED", "SUCCESS"):
        return {"ok": False, "error": f"Payment {pay_status or 'not completed'}"}

    amount = float(str(data.get("amount", "0")).replace(",", "") or 0)
    if expected_amount is not None:
        expected = float(expected_amount)
        if abs(amount - expected) > 0.02:
            return {"ok": False, "error": "Amount mismatch"}

    meta = data.get("metadata") or data.get("meta_data") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}

    order_id = meta.get("order_id")
    if order_id is not None:
        try:
            order_id = int(order_id)
        except (TypeError, ValueError):
            order_id = None

    trx = data.get("trx_id") or data.get("transaction_id") or transaction_id
    method = (data.get("payment_method") or "rupantorpay").lower()[:40]

    return {
        "ok": True,
        "order_id": order_id,
        "trx_id": str(trx),
        "payment_method": method,
        "transaction_id": transaction_id,
        "amount": amount,
    }
