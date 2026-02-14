# Smart DCA Automation (Analysis + Execution)

A complete system that automatically analyzes market data to find the best time of day to buy, and then executes that trade on **Bitkub** (Thai Exchange) automatically.

The system consists of two parts:
1.  **The Analyst (`crypto-analysis.py`)**: Runs every 2 days (06:00 BKK). Checks the last 30 days of price action to find the "Champion Time" (lowest median absolute miss). It updates a repository variable `DCA_TARGET_TIME`.
2.  **The Trader (`bitkub-dca.py`)**: Runs every 15 minutes. It checks if the current time matches the `DCA_TARGET_TIME` and if it hasn't bought yet today. If matched, it buys a fixed THB amount on Bitkub.

## Features

- **Self-Optimizing**: The buy time adjusts automatically based on recent market behavior (e.g., if dips shift from 07:00 to 14:00, the bot follows).
- **Cost Efficient**: GitHub Actions logic uses `bash` for time-checking to minimize billable runtime minutes (~165 mins/month).
- **Safety Locks**:
  - **Once-Per-Day**: Uses a `LAST_BUY_DATE` variable to ensure it never double-buys on the same day.
  - **Time Window**: Only triggers execution within 7 minutes of the target time.
- **Smart Metrics**: 
  - **Median Miss**: Calculates efficiency (how close to the exact daily bottom?).
  - **Win Rate**: Calculates consistency (% of days hitting the bottom).
- **AI Analysis**: Gemini (2.5 Flash) provides a daily summary of *why* that time was chosen.
- **Discord Integration**: Get reports and trade confirmations directly to your channel.

## Prerequisites

- **Bitkub Account**: API Key and Secret (enable "Trade" permission).
- **Google Gemini API**: For AI summaries (Free tier available).
- **Discord Webhook**: For notifications.
- **GitHub Repository**: To host the Actions.

## Setup & Installation

### 1. Secrets (Secure Storage)
Go to `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`:

| Secret Name | Value Description |
| :--- | :--- |
| `BITKUB_API_KEY` | Your Bitkub API Key. |
| `BITKUB_API_SECRET` | Your Bitkub API Secret. |
| `GEMINI_API_KEY` | Google AI Studio Key. |
| `DISCORD_WEBHOOK_URL` | Your Discord Webhook URL. |
| `GH_PAT_FOR_VARS` | Personal Access Token (Classic) with `repo` scope. Needed to update variables automatically. |

### 2. Variables (State Management)
Go to `Settings` -> `Secrets and variables` -> `Actions` -> `Variables` -> `New repository variable`:

| Variable Name | Initial Value | Description |
| :--- | :--- | :--- |
| `DCA_TARGET_TIME` | `12:00` | The HH:MM time to buy (will be auto-updated by the analysis). |
| `LAST_BUY_DATE` | `1970-01-01` | Tracks the last successful buy date (prevents double buys). |
| `TIMEZONE` | `Asia/Bangkok` | Your IANA Timezone (e.g., `Asia/Bangkok`, `America/New_York`). |

### 3. Usage

The system runs entirely on GitHub Actions:

- **Analysis Workflow**: Runs every 2 days at 06:00 BKK (23:00 UTC previous day). It finds the best time and updates `DCA_TARGET_TIME`.
- **DCA Workflow**: Runs every 15 minutes. It reads `DCA_TARGET_TIME` and buys if the time is right.

**Manual Interaction:**
- You can manually trigger the "Crypto Analysis (Every 48h)" workflow from the Actions tab to force an analysis update.
- You can manually trigger the "Daily Bitkub DCA" workflow with `force_run: true` to buy immediately (bypassing time/date checks).

## Local Development (Optional)

If you want to run scripts locally:

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Run Analysis**:
   ```bash
   export GEMINI_API_KEY="AIza..."
   python crypto-analysis.py
   ```

3. **Run Trader**:
   ```bash
   export BITKUB_API_KEY="key"
   export BITKUB_API_SECRET="secret"
   export DCA_AMOUNT_THB="500"
   export SYMBOL_THB="BTC_THB"
   python bitkub-dca.py
   ```

## Files

- `crypto-analysis.py`: Market analysis logic.
- `bitkub-dca.py`: Trading execution logic.
- `.github/workflows/crypto-analysis.yml`: Scheduled analysis job.
- `.github/workflows/daily_dca.yml`: Scheduled trading job.

## Configuration Defaults

- **Analysis**: Defaults to 30-day "Champion" logic.
- **DCA Amount**: Defaults to 350 THB (minimum Bitkub trade size is usually 10 THB, but higher is safer).
- **Symbol**: Defaults to `BTC/USDT` (Analysis) and `BTC_THB` (Trading).


