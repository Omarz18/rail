
# Telegram Bot (Railway Ready)

This bundle runs your bot with **python-telegram-bot v21** using polling (no webhook needed).

## Files
- `app.py` — bot entry point.
- `requirements.txt` — Python deps (python-telegram-bot, requests, httpx, phonenumbers).
- `Procfile` — declares a long-running worker for Railway.
- `railway.toml` — minimal config for Nixpacks builder.
- `.env.example` — environment variable template.

## Env Vars (set on Railway)
- `TELEGRAM_TOKEN` (required): your bot token from @BotFather.

## Deploy (two easy ways)

### A) Railway Dashboard
1. Create **New Project** → **Deploy from GitHub** (or **Empty Project** then **Deploy**).
2. Upload these files to a GitHub repo (or drag/drop in the web editor if using "Empty Project").
3. In **Variables**, add `TELEGRAM_TOKEN`.
4. In **Services → Settings**, ensure the detected start command is `python app.py` (Procfile sets it).
5. Click **Deploy**. Logs should show `Application is running via long polling`.

### B) railway CLI
```bash
# one time
npm i -g @railway/cli
railway login
railway init
railway up
# then set token
railway variables set TELEGRAM_TOKEN=123:abc
```

## Notes
- This uses **polling**, which is simplest and reliable on Railway free/paid plans.
- If you later need webhooks, add a small web server (FastAPI/Flask) and set `app.run_webhook(...)` instead of polling.
