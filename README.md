# Smart DCA Automation (Multi-Symbol Analysis + Execution)

A complete system that automatically analyzes market data to find the best time of day to buy for **multiple cryptocurrencies**, and then executes trades on **Bitkub** (Thai Exchange) automatically.

The system consists of two parts:
1.  **The Analyst (`crypto-analysis.py`)**: Runs daily (06:00 BKK / 23:00 UTC). Analyzes **60 days** of price data across **4 periods** (14, 30, 45, 60 days) for **multiple pairs** (e.g., BTC/USDT, LINK/USDT) to find the "Champion Time" for each. Uses AI synthesis to pick optimal buy time. Updates repository variable `DCA_TARGET_MAP`.
2.  **The Trader (`bitkub-dca.py`)**: Triggered on **push to main** or **manual dispatch**. Checks if current time matches target time for any enabled symbol. Executes market buy orders on Bitkub and logs to Gist.

## Features

- **Multi-Symbol Support**: Analyze and trade multiple pairs independently (e.g., BTC at 23:00, LINK at 23:45).
- **Self-Optimizing**: Buy time adjusts daily based on 60-day historical analysis with AI-powered recommendations.
- **Multi-Layer Safeguards**: Prevents double-buying with `LAST_BUY_DATE` tracking and workflow concurrency control.
- **Detailed Logging**: All trades logged to GitHub Gist with THB→USD conversion for portfolio tracking.
- **Discord Integration**: Real-time notifications for trades, errors, and critical alerts.
- **Timezone Aware**: Configurable timezone support (defaults to Asia/Bangkok).

### 1. Secrets (Secure Storage)
Go to `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`:

| Secret Name | Value Description |
| :--- | :--- |
| `BITKUB_API_KEY` | Your Bitkub API Key. |
| `BITKUB_API_SECRET` | Your Bitkub API Secret. |
| `GEMINI_API_KEY` | Google AI Studio Key. |
| `DISCORD_WEBHOOK_URL` | Your Discord Webhook URL. |
| `GH_PAT_FOR_VARS` | Personal Access Token (Classic) with `repo` and **`gist`** scope. Used to update variables and write to your log. |
| `GIST_TOKEN` | (Same as GH_PAT_FOR_VARS) Token used specifically by the python script to update Gists. |
| `GIST_ID` | The ID of your `trade_log.md` gist. |

### 2. Variables (Configuration)
Go to `Settings` -> `Secrets and variables` -> `Actions` -> `New repository variable`:

| Variable Name | Example Value | Description |
| :--- | :--- | :--- |
| `DCA_TARGET_MAP` | `{"BTC_THB": {"TIME": "07:00", "AMOUNT": 800, "BUY_ENABLED": true, "LAST_BUY_DATE": ""}}` | **Key config.** Dictionary mapping Symbol to settings (Time, Amount, Enabled, LastBuy). |
| `TIMEZONE` | `Asia/Bangkok` | Timezone for operations. |

### 3. Workflow Configuration

**Analysis Workflow (`crypto-analysis.yml`)**:
- **Schedule**: Daily at 23:00 UTC (06:00 Bangkok)
- **Trigger**: Manual dispatch or push to main
- **Concurrency**: Only one analysis runs at a time (cancel-in-progress)
- **Environment**: Uses `binanceus` exchange to avoid geo-restrictions

**Trader Workflow (`daily_dca.yml`)**:
- **Trigger**: Push to main or manual dispatch
- **Concurrency**: Only one trade workflow runs at a time (queued, not cancelled)
- **Pre-Check**: Bash script filters symbols before running Python
- **Safeguards**: Multiple layers check `BUY_ENABLED`, `LAST_BUY_DATE`, and time window

## How It Works

### Daily Analysis Cycle
1. At 06:00 Bangkok time, `crypto-analysis.yml` triggers
2. Fetches 60 days of 15-minute OHLCV data from Binance
3. Calculates metrics: `median_miss`, `win_rate`, `dca_price` for each 15-min slot
4. Gemini AI synthesizes recommendation across 14/30/45/60-day periods
5. Updates `DCA_TARGET_MAP["BTC_THB"]["TIME"]` with optimal buy time

### Trade Execution Cycle
1. Workflow triggers (push/manual)
2. **Bash Quick Check**: Filters by `BUY_ENABLED`, `LAST_BUY_DATE`, time window
3. If match found → Install deps → Run Python
4. **Python**: Validates time window (±5 min or catch-up), checks `LAST_BUY_DATE`
5. Places market bid on Bitkub (waits 5 seconds for fill)
6. Logs to Gist, sends Discord alert
7. Updates `LAST_BUY_DATE` with 3 retries (fails loudly on error)

## Safeguards Against Double-Buying

| Layer | Location | Check | Prevents |
|-------|----------|-------|----------|
| **Concurrency** | GitHub Actions | Only 1 workflow runs at a time | Race conditions |
| **Bash Filter** | Quick Check step | `LAST_BUY_DATE == today` | Unnecessary Python execution |
| **Python Filter** | Symbol processing | `BUY_ENABLED == false` | Disabled symbols |
| **Time Window** | `is_time_to_trade()` | Within ±5 min or catch-up | Out-of-window execution |
| **Date Check** | Per-symbol loop | `LAST_BUY_DATE == today` | Same-day duplicate |
| **API Update** | Post-trade | 3 retries, fail loudly | Silent failure risk |

