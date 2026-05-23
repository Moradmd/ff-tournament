# Tournament App — Setup (Banglish)

## Tumi amake ja dibe (eita copy kore WhatsApp/chat e pathate paro)

1. **RupantorPay API Key** — dashboard → Brands → API key  
1b. **bKash Checkout credentials** (jodi bKash gateway chai) — onboarding theke: base URL + username/password + app key/secret  
2. **bKash number** (manual payment)  
3. **Nagad number** (manual payment)  
4. **Entry fee** — kototaka? (example: 50)  
5. **Admin PIN** — admin panel er password (secret, notun ekta set koro)  
6. **Tournament name** — lobby te ki naam dekhabe?

API key pele `.env` e boshiye debo / tumi nijei boshate paro:

```
RUPANTORPAY_API_KEY=ekhane_key
PUBLIC_BASE_URL=https://tomar-site.onrender.com
RUPANTORPAY_CLIENT=tomar-site.onrender.com
```

### bKash gateway enable (URL based checkout)
`.env`/Render Environment e ei gula dite hobe (bKash theke pawa):

```
PAYMENT_PROVIDER=bkash
BKASH_BASE_URL=https://checkout.sandbox.bka.sh/v1.2.0-beta
BKASH_USERNAME=...
BKASH_PASSWORD=...
BKASH_APP_KEY=...
BKASH_APP_SECRET=...
PUBLIC_BASE_URL=https://tomar-site.onrender.com
```

bKash callback app er moddhe:
`https://YOUR-SITE/payment/bkash/callback`

---

## Free hosting + domain (sabcheye easy)

### Option A — **Render** (recommend, 100% free subdomain)

| Ki | Details |
|----|---------|
| Hosting | https://render.com — free Web Service |
| Domain | Free: `ff-tournament.onrender.com` (nijer naam choose) |
| SSL | Auto HTTPS |
| Flask | Already ready (`Procfile` + `render.yaml`) |

**Steps:**

1. GitHub e project upload (or Render direct connect folder)
2. Render → New → Web Service → repo select
3. Environment variables add:
   - `PUBLIC_BASE_URL` = `https://YOUR-NAME.onrender.com`
   - `RUPANTORPAY_API_KEY` = tor key
   - `RUPANTORPAY_CLIENT` = `YOUR-NAME.onrender.com`
   - `BKASH_NUMBER`, `NAGAD_NUMBER`, `ENTRY_FEE`, `ADMIN_PIN`
4. Deploy → link: `https://YOUR-NAME.onrender.com`

RupantorPay dashboard e **Brand** e ei domain allow korte hobe.

---

### Option B — **DuckDNS** (free subdomain, PC te app cholale)

| Ki | Details |
|----|---------|
| Domain | Free: `myff.duckdns.org` — https://www.duckdns.org |
| Hosting | Tomar PC (install-and-run.ps1) + router port forward |
| Hard | Network setup onek |

Payment er jonno **public URL** lagbe — beginner er jonno Render better.

---

### Option C — Cheap real domain (paid, but kom)

| Site | Price |
|------|-------|
| https://www.namecheap.com | `.xyz` / `.shop` ~ $1–3 first year |
| https://porkbun.com | Similar cheap TLD |

Domain kinle Render e **Custom Domain** connect koro + `PUBLIC_BASE_URL` update.

---

## RupantorPay callback URLs (auto generate hoy)

Deploy er por ei URLs Rupantor e allow korte hobe (brand settings):

- Success: `https://YOUR-SITE/payment/rupantor/success`
- Cancel: `https://YOUR-SITE/payment/rupantor/cancel`
- Webhook: `https://YOUR-SITE/payment/rupantor/webhook`

`PUBLIC_BASE_URL` thik thakle app nijei generate kore.

---

## Local test (PC only)

```
.\install-and-run.ps1
```

- PC: http://127.0.0.1:5000/
- Phone (same WiFi): http://192.168.x.x:5000/

Online payment **live** test er jonno Render deploy kora best — `127.0.0.1` Rupantor theke reach korte pare na.
