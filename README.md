# Smart DCA Automation (Multi-Symbol Analysis + Execution)

A complete system that automatically analyzes market data to find the best time of day to buy for **multiple cryptocurrencies**, and then executes trades automatically on your configured exchange.

The system consists of two parts:
1.  **The Analyst (`crypto-analysis.py`)**: Runs daily (06:00 BKK / 23:00 UTC). Analyzes **60 days** of price data across **4 periods** (14, 30, 45, 60 days) for **multiple pairs** (e.g., BTC/USDT, LINK/USDT) to find the "Champion Time" for each. Uses AI synthesis to pick optimal buy time. Updates repository variable `DCA_TARGET_MAP`.
2.  **The Trader (`crypto-dca.py`)**: Triggered on **push to main** or **manual dispatch**. Checks if current time matches target time for any enabled symbol. Executes market buy orders and logs to Gist.

## Features

- **Multi-Symbol Support**: Analyze and trade multiple pairs independently (e.g., BTC at 23:00, LINK at 23:45).
- **Self-Optimizing**: Buy time adjusts daily based on 60-day historical analysis with AI-powered recommendations.
- **Multi-Layer Safeguards**: Prevents double-buying with `LAST_BUY_DATE` tracking and workflow concurrency control.
- **Detailed Logging**: All trades logged to GitHub Gist with THB and USD amounts for portfolio tracking.
- **Portfolio Integration**: Automatic trade logging to Ghostfolio portfolio tracker with 8-decimal precision and timezone-aware timestamps.
- **Discord Integration**: Real-time notifications for trades (with THB+USD amounts and Ghostfolio status), errors, and critical alerts including FX rate failures.
- **Timezone Aware**: Fully configurable timezone support via `TIMEZONE` env variable (defaults to Asia/Bangkok).
- **Non-Blocking Logging**: Trade execution succeeds even if Gist or Ghostfolio logging fails (errors logged and notified).

### 1. Secrets (Secure Storage)
Go to `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`:

| Secret Name | Value Description |
| :--- | :--- |
| `BITKUB_API_KEY` | Your exchange API Key. |
| `BITKUB_API_SECRET` | Your exchange API Secret. |
| `GEMINI_API_KEY` | Google AI Studio Key. |
| `DISCORD_WEBHOOK_URL` | Your Discord Webhook URL. |
| `GH_PAT_FOR_VARS` | Personal Access Token (Classic) with `repo` and **`gist`** scope. Used to update variables and write to your log. |
| `GIST_TOKEN` | (Same as GH_PAT_FOR_VARS) Token used specifically by the python script to update Gists. |
| `GIST_ID` | The ID of your `trade_log.md` gist. |
| `GHOSTFOLIO_TOKEN` | Your Ghostfolio access token for portfolio logging. |

### 2. Variables (Configuration)
Go to `Settings` -> `Secrets and variables` -> `Actions` -> `New repository variable`:

| Variable Name | Example Value | Description |
| :--- | :--- | :--- |
| `DCA_TARGET_MAP` | `{"BTC_THB": {"TIME": "07:00", "AMOUNT": 800, "BUY_ENABLED": true, "LAST_BUY_DATE": ""}}` | **Key config.** Dictionary mapping Symbol to settings (Time, Amount, Enabled, LastBuy). |
| `TIMEZONE` | `Asia/Bangkok` | Timezone for operations. |
| `PORTFOLIO_ACCOUNT_MAP` | `{"BTC": "3cced5d3-f219-47c8-bb73-878466060d7a", "DEFAULT": "9069984b-3c2b-48d8-831d-b7d73b5bafb7"}` | Maps crypto symbols to Ghostfolio account IDs. Falls back to DEFAULT if symbol not found. |
| `GHOSTFOLIO_URL` | `https://ghostfol.io` | Ghostfolio instance URL (optional, defaults to https://ghostfol.io). |

### 3. Workflow Configuration

**Analysis Workflow (`crypto-analysis.yml`)**:
- **Schedule**: Daily at 23:00 UTC (06:00 Bangkok)
- **Trigger**: Manual dispatch or push to main
- **Concurrency**: Only one analysis runs at a time (cancel-in-progress)
- **Environment**: Uses `binanceus` exchange to avoid geo-restrictions

**Trader Workflow (`daily_dca.yml`)**:
- **Trigger**: **Manual dispatch ONLY** (no automatic cron schedule by design). Triggered via GitHub Actions UI or workflow_dispatch API
- **Concurrency**: Only one trade workflow runs at a time (queued, not cancelled)
- **Pre-Check**: Bash Quick Check runs first (no checkout/Python needed). Only checks out code and installs dependencies if a trade is needed
- **Safeguards**: Multiple layers check `BUY_ENABLED`, `LAST_BUY_DATE`, and time window
- **Rationale**: Manual dispatch gives you full control over when trades execute. Analysis updates DCA_TARGET_MAP daily, but you decide when to run the trader

## How It Works

### Daily Analysis Cycle
1. At 06:00 Bangkok time, `crypto-analysis.yml` triggers
2. Fetches 60 days of 15-minute OHLCV data from Binance
3. Calculates metrics: `median_miss`, `win_rate`, `dca_price` for each 15-min slot
4. Gemini AI synthesizes recommendation across 14/30/45/60-day periods
5. Updates `DCA_TARGET_MAP["BTC_THB"]["TIME"]` with optimal buy time

### Trade Execution Cycle
1. **Manual trigger** via GitHub Actions UI (Actions tab → Daily Crypto DCA → Run workflow) or workflow_dispatch API call
2. **Bash Quick Check** (no checkout/Python required): Filters by `BUY_ENABLED`, `LAST_BUY_DATE`, time window
3. If no match → Workflow ends (fast exit, no resources used)
4. If match found → Checkout repo → Setup Python → Install deps → Run Python
5. **Python**: Validates time window (±5 min or catch-up), checks `LAST_BUY_DATE`
6. Places market bid order (waits 5 seconds for fill)
7. Fetches THB→USD exchange rate for logging
8. **Logs to Ghostfolio** (non-blocking): Authenticates with 30s timeout, creates activity with 8-decimal precision, maps symbol to account (falls back to DEFAULT)
9. **Logs to Gist** (non-blocking): Records trade with THB+USD amounts and Ghostfolio save status
10. Sends Discord alert with trade details and Ghostfolio status
11. Updates `LAST_BUY_DATE` with 3 retries (fails loudly on error)

**Why Manual Dispatch?**: The system intentionally has NO automatic cron schedule on the trader workflow. This gives you complete control over trade execution timing. While analysis runs daily to update optimal buy times, you decide when to actually execute trades.

## Currency Conversion

The system fetches real-time THB→USD exchange rates from multiple sources:
- **Primary**: Frankfurter API (`api.frankfurter.app`)
- **Secondary**: Open Exchange Rate API (`open.er-api.com`)
- **Fallback**: If all sources fail, USD values show as `$0.00` and an error notification is sent to Discord

## Portfolio Logging

Trades are automatically logged to Ghostfolio for portfolio tracking:
- **Account Mapping**: Maps crypto symbols to Ghostfolio accounts via `PORTFOLIO_ACCOUNT_MAP` (falls back to DEFAULT)
- **Precision**: 8-decimal quantity formatting (e.g., 0.00012345 BTC)
- **Comment Format**: `฿800.00 - $25.10` (shows both THB and USD spent)
- **Data Source**: Yahoo Finance (BTCUSD, LINKUSD, etc.) - free tier compatible
- **Timezone Support**: Uses configured TIMEZONE, converts to UTC for Ghostfolio
- **Timeout**: 30 seconds for all Ghostfolio API requests (doubled from standard)
- **Error Handling**: Non-blocking - trade executes even if Ghostfolio fails (errors logged to console and Discord)
- **Gist Integration**: "Saved" column reflects Ghostfolio logging success (`true`/`false`)

## Safeguards Against Double-Buying

| Layer | Location | Check | Prevents |
|-------|----------|-------|----------|
| **Concurrency** | GitHub Actions | Only 1 workflow runs at a time | Race conditions |
| **Bash Filter** | Quick Check step | `LAST_BUY_DATE == today` | Unnecessary Python execution |
| **Python Filter** | Symbol processing | `BUY_ENABLED == false` | Disabled symbols |
| **Time Window** | `is_time_to_trade()` | Within ±5 min or catch-up | Out-of-window execution |
| **Date Check** | Per-symbol loop | `LAST_BUY_DATE == today` | Same-day duplicate |
| **API Update** | Post-trade | 3 retries, fail loudly | Silent failure risk |

