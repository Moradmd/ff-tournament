# Deploy — proper build (Banglish)

## 1) PC te build

```powershell
cd c:\Users\Admin\Documents\docu\ff-tournament
.\build.ps1
```

Output:
- `ff-tournament-upload.zip` — GitHub upload er jonno (project folder er vitore)
- `RENDER-SETTINGS.txt` — Render copy-paste

## 2) GitHub

1. https://github.com/new → `ff-tournament`
2. ZIP extract → **sob file repo root e** (app.py directly visible)
3. Commit

**GitHub Pages OFF rakho** (important):

- Repo → **Settings** → **Pages** → Source: **None** / Deploy from branch: **off**
- GitHub Pages shudhu static HTML — **Flask app eikhane cholena**
- Real site = **Render.com** (niche step 3)

## 3) Render

`RENDER-SETTINGS.txt` er moto fill koro. **Start Command:**

```
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

## 4) RupantorPay

Brand domain = `YOUR-SERVICE-NAME.onrender.com`

## Fix 500 error

- `--workers 1` must
- `DATABASE_PATH=/tmp/tournament.db`
- Root Directory empty
- Logs check Render dashboard
