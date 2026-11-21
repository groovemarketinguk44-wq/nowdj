# NowDJ

NowDJ: DJ booking/catalogue app with pricing, email import and PDF quotes.

## Stack

FastAPI + Jinja2 (single-folder app: main.py), SQLite, WeasyPrint

## Commands

```bash
# install
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
# run
uvicorn main:app --reload --port 8080
# test
(no automated tests yet: run it and check the changed pages by hand)
```

## Deploy

Fly app `nowdj`: `fly deploy`.

## Environment variables (names only; values live in Fly secrets / local .env, never in git)

DATABASE_URL SECRET_KEY BASE_DOMAIN MICROSOFT_CLIENT_ID MICROSOFT_CLIENT_SECRET MICROSOFT_REDIRECT_URI MICROSOFT_TENANT

Needed in a cloud session: SECRET_KEY

## Conventions

- Work on a branch and open a PR; Ben merges. Never commit secrets or .env files.
- Keep changes small and explain them in plain words.
- Ben's defaults for his apps: OneSignal push alerts, an admin Visitors tab, full SEO (landing pages, sitemap) where it fits.
