# FreeVibeInvest - It is probably fine

> ⚠️ **Disclaimer:** This is a hobby project built for fun. The "analysis" here is a free LLM reading RSS feeds and vibing. Do not — under any circumstances — use this to make real financial decisions. If you buy or sell stocks based on what a free AI model scraped from the internet at 10 AM, that's on you. Past vibes do not predict future vibes. Not financial advice. Not even close.

A pipeline that pulls financial headlines from Finnhub (market news + company news), Yahoo Finance RSS, MarketWatch, CNBC, Wall Street Journal, and NY Times Business,
runs them through an LLM via OpenRouter, and delivers a daily market briefing to Discord or Telegram.

```
Finnhub (market news + company news)
+ Yahoo Finance RSS (per-ticker)
+ MarketWatch · CNBC · Wall Street Journal · NY Times Business (RSS)
    ↓
scraper.py  — deduplicated headline bundle
    ↓
analyzer.py — OpenRouter LLM (market pulse: bulls / bears / buys)
    ↓
notifier.py — Discord embeds / Telegram message
```

---

## Prerequisites

| Service | Purpose | Free tier |
|---|---|---|
| [OpenRouter](https://openrouter.ai) | LLM inference | Yes (`openrouter/free`) |
| [Finnhub](https://finnhub.io) | Market news & quotes | Yes |
| Discord webhook **or** Telegram bot | Delivery | Yes |

---

## Environment Variables

Set these as **GitHub Secrets** for GitHub Actions, or in a `.env` file for local runs.

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | yes | From https://openrouter.ai/keys |
| `FINNHUB_API_KEY` | yes | From https://finnhub.io/dashboard |
| `DISCORD_WEBHOOK_URL` | yes * | Your Discord channel webhook URL |
| `TELEGRAM_BOT_TOKEN` | yes * | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | yes * | Chat/channel ID for the bot |
| `LOG_LEVEL` | no | `INFO` or `DEBUG` (default: `INFO`) |

\* At least one notification channel (Discord **or** Telegram) is required.

**Pipeline settings** (schedule, analysis mode, RSS feeds, token limits) live in
`pipeline_config.yaml` — no secrets there, safe to commit.

---

## Deploy with GitHub Actions (Recommended)

GitHub Actions runs the pipeline on a cron schedule for free — no server needed.

### Step 1 — Fork the repo

1. Open the repo on GitHub
2. Click **Fork** (top-right corner) → **Create fork**

You now have your own copy at `https://github.com/YOUR_USERNAME/FreeVibeInvest`.

### Step 2 — Add your secrets

1. In your forked repo, click **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** and add each of the following:

| Secret name | Value |
|---|---|
| `OPENROUTER_API_KEY` | your OpenRouter API key |
| `FINNHUB_API_KEY` | your Finnhub API key |
| `DISCORD_WEBHOOK_URL` | your Discord webhook URL |
| `TELEGRAM_BOT_TOKEN` | *(only if using Telegram)* |
| `TELEGRAM_CHAT_ID` | *(only if using Telegram)* |

Secrets are encrypted and never exposed in logs. The workflow reads them via
`${{ secrets.SECRET_NAME }}`.

### Step 3 — The workflow is already included

`.github/workflows/investment-research.yml` is committed in the repo.
It runs the pipeline automatically **every weekday at 10 AM ET (15:00 UTC)** — 30 minutes after market open.

To change the schedule, edit the `cron` line in `.github/workflows/investment-research.yml`. GitHub Actions cron is always UTC:

```yaml
- cron: '0 15 * * 1-5'   # 10 AM EST (UTC-5)
- cron: '0 14 * * 1-5'   # 10 AM EDT (UTC-4, daylight saving)
```

Use [crontab.guru](https://crontab.guru) to build cron expressions.

### Step 4 — Run it immediately

1. Go to the **Actions** tab in your forked repo
2. Click **Investment Research Pipeline** in the left sidebar
3. Click **Run workflow** → **Run workflow**

This triggers a full run on demand — no need to wait for the schedule.

### Viewing logs

- **Actions tab** → click any run → expand the **Run pipeline** step
- On failure, logs and metrics are automatically uploaded as an artifact (kept 7 days)

---

## Local Setup

```bash
git clone https://github.com/YOUR_USERNAME/FreeVibeInvest.git
cd FreeVibeInvest

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export FINNHUB_API_KEY=[your_key]
export DISCORD_WEBHOOK_URL=[your_discord_url]
export OPENROUTER_API_KEY=[your_openrouter_key]

python main.py
```

Create a `.env` file with the variables from the table above before running.

---

## Customization

### Change the LLM model

Edit `llm_model` in `pipeline_config.yaml`:

```yaml
llm_model: google/gemini-2.5-flash-lite   # free, fast
llm_model: anthropic/claude-3.5-haiku     # paid, more reliable
```

Or set it via a **GitHub Actions variable** (no code change needed):

1. Go to **Settings** → **Secrets and variables** → **Actions** → **Variables** tab
2. Click **New repository variable**
3. Name: `LLM_MODEL`, Value: your model string (e.g. `google/gemini-2.0-flash-exp:free`)

The variable overrides `pipeline_config.yaml` at runtime. If unset, defaults to `openrouter/free`.

### Add or remove RSS feeds

Edit the `rss_feeds` list under `live_news` in `pipeline_config.yaml`.
Any feed that returns 403 or 404 is silently skipped.

### Disable a notification channel

```yaml
# pipeline_config.yaml
enable_discord: true
enable_telegram: false
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `OPENROUTER_API_KEY must be set` | Secret not added to your fork, or `.env` missing locally |
| `FINNHUB_API_KEY must be set` | Same as above |
| `finish_reason='length'` in logs | Pipeline retries automatically; if it keeps happening, raise `llm_market_pulse_max_tokens` in `pipeline_config.yaml` |
| Discord `400` error | Already handled by embed chunking; check the logs artifact for details |
| RSS feed `403` / `404` | Feed is blocked or gone — remove it from `pipeline_config.yaml` |
