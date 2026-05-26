"""Active payment gateway — Manual (bKash/Nagad) / Supabase / RupantorPay / bKash API / SSLCommerz."""

import sslcommerz
import rupantorpay
import bkash

from config import (
    PAYMENT_PROVIDER,
    ENABLE_MANUAL_PAYMENT,
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
)


def is_manual_enabled():
    return bool(ENABLE_MANUAL_PAYMENT)


def is_enabled():
    return is_manual_enabled() or bool(provider_slug())


def _is_supabase_configured():
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def provider_name():
    slug = provider_slug()
    if slug == "manual":
        return "bKash / Nagad"
    if slug == "supabase":
        return "Supabase Auto-Detect"
    return {
        "rupantorpay": "RupantorPay",
        "bkash": "bKash",
        "sslcommerz": "SSLCommerz",
    }.get(slug, "")


def provider_slug():
    if is_manual_enabled():
        return "manual"
    p = (PAYMENT_PROVIDER or "auto").strip().lower()
    if p in ("", "auto"):
        if _is_supabase_configured():
            return "supabase"
        if rupantorpay.is_configured():
            return "rupantorpay"
        if bkash.is_configured():
            return "bkash"
        if sslcommerz.is_configured():
            return "sslcommerz"
        return ""

    if p == "supabase" and _is_supabase_configured():
        return "supabase"
    if p in ("bkash", "b-kash") and bkash.is_configured():
        return "bkash"
    if p in ("rupantorpay", "rupantor") and rupantorpay.is_configured():
        return "rupantorpay"
    if p in ("sslcommerz", "ssl") and sslcommerz.is_configured():
        return "sslcommerz"
    return ""


def start_checkout(
    *,
    order_id,
    amount,
    customer_name,
    customer_phone,
    success_url,
    fail_url,
    cancel_url,
    webhook_url,
    product_name,
    client_host=None,
):
    slug = provider_slug()

    if slug == "supabase":
        # Supabase — redirect to internal payment page that polls for transaction
        # success_url will be used as the payment page URL
        return {
            "ok": True,
            "gateway_url": success_url,
            "tran_id": None,
        }

    if slug == "rupantorpay":
        return rupantorpay.create_checkout(
            order_id=order_id,
            amount=amount,
            fullname=customer_name,
            email=f"order{order_id}@ff-tournament.local",
            phone=customer_phone,
            success_url=success_url,
            cancel_url=cancel_url,
            webhook_url=webhook_url,
            client_host=client_host,
        )

    if slug == "bkash":
        # bKash URL-based checkout uses single callback base URL, which bKash expands into
        # success/failure/cancel URLs by adding query params.
        session = bkash.create_payment(
            amount=amount,
            payer_reference=customer_phone,
            callback_url=success_url,
            merchant_invoice_number=f"ORDER{order_id}",
        )
        if session.get("ok"):
            # Keep a common field name so app can store it as gateway_tran_id if needed
            session["tran_id"] = session.get("payment_id")
        return session

    if slug == "sslcommerz":
        session = sslcommerz.create_session(
            order_id=order_id,
            amount=amount,
            customer_name=customer_name,
            customer_phone=customer_phone,
            success_url=success_url,
            fail_url=fail_url,
            cancel_url=cancel_url,
            ipn_url=webhook_url,
            product_name=product_name,
        )
        if session.get("ok"):
            session["tran_id"] = session.get("tran_id")
        return session

    return {"ok": False, "error": "Payment gateway configure করা নেই"}
