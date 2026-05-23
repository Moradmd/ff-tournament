# Free Fire Tournament — Slot + Payment Flow

## Flow

1. Admin **join link** WhatsApp এ দেয়
2. Player **৪ জন** name বা UID দেয় (UID → API থেকে auto name)
3. **Payment** — Manual (bKash/Nagad trx) অথবা **SSLCommerz** online (bKash, Nagad, card)
4. **Admin approve** → পরের খালি slot এ squad যায়
5. **Main Lobby** (`/`) — সবাই দেখে (শুধু approved)
6. Reject / fail → lobby তে name যায় **না**

## Payment gateway (SSLCommerz)

1. [SSLCommerz Developer](https://developer.sslcommerz.com/registration/) এ sandbox account
2. `.env` এ যোগ করো:

```
SSLCOMMERZ_STORE_ID=your_store_id
SSLCOMMERZ_STORE_PASS=your_store_password
SSLCOMMERZ_IS_LIVE=0
```

3. Sandbox test card: https://developer.sslcommerz.com/doc/v4/
4. Live চালু: `SSLCOMMERZ_IS_LIVE=1` + live store credentials

Online pay শেষে order `pending_approval` — admin এখনও squad verify করে।

## Run

```powershell
cd c:\Users\Admin\Documents\docu\ff-tournament
.\install-and-run.ps1
```

- **Lobby:** http://127.0.0.1:5000/
- **Admin:** http://127.0.0.1:5000/admin  (PIN = `ADMIN_PIN` environment variable)
- Join link admin panel এ copy করো

## UID API setup

Default: **ffuidchack.vercel.app**

```
GET https://ffuidchack.vercel.app/bd/{UID}
GET https://ffuidchack.vercel.app/pk/{UID}
```

`.env` এ `FF_SERVER=bd` বা `pk`। Fail করলে **apiinfo-flame** তারপর **freefire-api-six** auto try।

Custom API override:

```
FF_UID_API_URL=https://your-api.com/player?uid={uid}
```
