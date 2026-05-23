import os
import secrets
from pathlib import Path

ENV_FILE = Path(__file__).parent / ".env"


def _load_dotenv():
    if not ENV_FILE.exists():
        return
    # Local dev e .env file ta priority (change korle reload e effect dekha jabe).
    # Hosting/Render e real env vars priority pabe.
    is_hosted = bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"))
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if not k:
            continue
        if is_hosted:
            # Hosting e: env var set thakle override koro na
            if k not in os.environ or os.environ.get(k, "") == "":
                os.environ[k] = v
        else:
            # Local e: .env always apply
            os.environ[k] = v


_load_dotenv()

# Admin PIN: always set via environment (.env locally / Render env vars).
# No hardcoded default here—so it won't be exposed in public repos or UI.
ADMIN_PIN = os.getenv("ADMIN_PIN", "").strip()
BKASH_NUMBER = os.getenv("BKASH_NUMBER", "01XXXXXXXXX")
NAGAD_NUMBER = os.getenv("NAGAD_NUMBER", "01XXXXXXXXX")
ENTRY_FEE = os.getenv("ENTRY_FEE", "50")

# Payment provider selection:
# - auto (default): RupantorPay -> bKash -> SSLCommerz (first configured wins)
# - rupantorpay | bkash | sslcommerz
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "auto").strip().lower()

# Manual payment toggle (for testing or manual trx approval workflow)
ENABLE_MANUAL_PAYMENT = os.getenv("ENABLE_MANUAL_PAYMENT", "0") == "1"

# WhatsApp group (approved players only)
WHATSAPP_GROUP_LINK = os.getenv(
    "WHATSAPP_GROUP_LINK",
    "https://chat.whatsapp.com/IzvsrACcYWxBDZBfouTVEq",
).strip()

# UID API — ffuidchack (default) · apiinfo-flame fallback
FF_API_BASE = os.getenv("FF_API_BASE", "https://ffuidchack.vercel.app")
FF_API_FLAME_BASE = os.getenv("FF_API_FLAME_BASE", "https://apiinfo-flame.vercel.app")
FF_API_LEGACY_BASE = os.getenv(
    "FF_API_LEGACY_BASE", "https://freefire-api-six.vercel.app"
)
FF_SERVER = os.getenv("FF_SERVER", "bd")  # bd | pk — Bangladesh server default

# Custom override (optional — set korle official API skip)
FF_UID_API_URL = os.getenv("FF_UID_API_URL", "")
FF_UID_API_KEY = os.getenv("FF_UID_API_KEY", "")
FF_UID_API_MOCK = os.getenv("FF_UID_API_MOCK", "0") == "1"

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))

# Live site URL (Render/hosting) — payment callback er jonno
# Example: https://ff-tournament.onrender.com
# Render e auto-detect hoy, locally PUBLC_BASE_URL set korar dorkar nei
_render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL", "") or _render_url).rstrip("/")
SECRET_KEY = os.getenv("SECRET_KEY", "") or secrets.token_hex(32)
DATABASE_PATH = os.getenv("DATABASE_PATH", "")

# Gateway payment success hole auto approve (manual trx ID e admin approve thakbe)
AUTO_APPROVE_GATEWAY = os.getenv("AUTO_APPROVE_GATEWAY", "0") == "1"

# RupantorPay — https://rupantorpay.com/user/dashboard → Brands → API key
RUPANTORPAY_API_KEY = os.getenv("RUPANTORPAY_API_KEY", "")
RUPANTORPAY_CLIENT = os.getenv("RUPANTORPAY_CLIENT", "")  # domain/host for X-CLIENT header

# bKash Checkout (URL based) — onboarding theke credentials pabe
# base URL examples:
# - sandbox: https://checkout.sandbox.bka.sh/v1.2.0-beta
# - live:    https://checkout.pay.bka.sh/v1.2.0-beta   (exact base_URL bKash share kore)
BKASH_BASE_URL = os.getenv("BKASH_BASE_URL", "")
BKASH_USERNAME = os.getenv("BKASH_USERNAME", "")
BKASH_PASSWORD = os.getenv("BKASH_PASSWORD", "")
BKASH_APP_KEY = os.getenv("BKASH_APP_KEY", "")
BKASH_APP_SECRET = os.getenv("BKASH_APP_SECRET", "")

# SSLCommerz (optional fallback)
SSLCOMMERZ_STORE_ID = os.getenv("SSLCOMMERZ_STORE_ID", "")
SSLCOMMERZ_STORE_PASS = os.getenv("SSLCOMMERZ_STORE_PASS", "")
SSLCOMMERZ_IS_LIVE = os.getenv("SSLCOMMERZ_IS_LIVE", "0") == "1"
