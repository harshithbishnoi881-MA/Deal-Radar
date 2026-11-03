# Deal Radar: setup (about 10 minutes, no laptop needed afterwards)

1. Create a **private** GitHub repo and upload all these files (keep the folder structure, including `.github/workflows/daily.yml`).
2. Repo → Settings → Secrets and variables → Actions → add:
   - `ANTHROPIC_API_KEY` (recommended; without it the tool uses keyword matching only)
   - `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (optional, for a phone push each morning; create a bot via @BotFather)
3. Repo → Actions tab → "Deal Radar daily" → **Run workflow** (first run).
4. Repo → Settings → Pages → Source: *Deploy from a branch* → branch `main`, folder `/docs`.
   Note: Pages on a private repo needs a paid GitHub plan; otherwise use a public repo (the digest will then be public) or rely on Telegram only.
5. Open `https://<your-username>.github.io/<repo>/` on phone or laptop. Add to home screen for an app-like icon.

It then runs every day at 07:00 IST. Edit `GOOGLE_QUERIES` and `DIRECT_FEEDS` in `fetch_deals.py` to add sources or keywords.

Local test: `pip install -r requirements.txt && python fetch_deals.py`
