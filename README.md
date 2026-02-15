# Smart DCA Automation (Analysis + Execution)

A complete system that automatically analyzes market data to find the best time of day to buy, and then executes that trade on **Bitkub** (Thai Exchange) automatically.

The system consists of two parts:
1.  **The Analyst (`crypto-analysis.py`)**: Runs daily (06:00 BKK). Checks the last 30 days of price action to find the "Champion Time" (lowest median absolute miss). It updates a repository variable `DCA_TARGET_TIME`.
2.  **The Trader (`bitkub-dca.py`)**: Triggered externally via API. It checks if the current time matches the `DCA_TARGET_TIME` (±10 mins) and if it hasn't bought yet today. If matched, it buys a fixed THB amount on Bitkub.

## Features

- **Self-Optimizing**: The buy time adjusts automatically based on recent market behavior (e.g., if dips shift from 07:00 to 14:00, the bot follows).
- **Precision Timing**: Uses an external cron trigger to bypass GitHub Actions' scheduling delays.
- **Safety Locks**:
  - **Once-Per-Day**: Uses a `LAST_BUY_DATE` variable to ensure it never double-buys on the same day.
  - **Time Window**: Only executes if triggered within 10 minutes of the target time.
- **Smart Metrics**: 
  - **Median Miss**: Calculates efficiency (how close to the exact daily bottom?).
  - **Win Rate**: Calculates consistency (% of days hitting the bottom).
- **AI Analysis**: Gemini (1.5 Flash/Pro) provides a daily summary of *why* that time was chosen.
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

### 3. External Scheduler (Required)

Because GitHub Actions' internal scheduler can be unreliable (delayed by 30+ minutes), we use an external service to trigger the trade exactly when we want.

**Recommended: Google Cloud Scheduler (Free)**

1.  **Create a GitHub `PAT`**: Go to GitHub Settings -> Developer settings -> Personal access tokens -> Tokens (classic). Generate one with `repo` and `workflow` scopes.
2.  **Go to Google Cloud Scheduler**: Create a Job.
    *   **Frequency**: Your desired time (e.g. `45 14 * * *` for 14:45).
    *   **Timezone**: `Asia/Bangkok` (or your local time).
    *   **Target**: `HTTP`
    *   **URL**: `https://api.github.com/repos/YOUR_USERNAME/DCA-Analysis/actions/workflows/daily_dca.yml/dispatches`
    *   **Method**: `POST`
    *   **Body**: `{"ref":"main"}`
    *   **Headers**:
        *   `Authorization`: `Bearer YOUR_GITHUB_PAT`
        *   `Accept`: `application/vnd.github.v3+json`
        *   `User-Agent`: `Google-Cloud-Scheduler`

**Alternative: Manual Run**
- Go to the "Actions" tab in GitHub.
- Select "Daily Bitkub DCA".
- Click "Run workflow" -> Enter Amount (Default: 100) -> Run.

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
- `.github/workflows/crypto-analysis.yml`: Scheduled analysis job (daily).
- `.github/workflows/daily_dca.yml`: API-Triggered trading job.

## Configuration Defaults

- **Analysis**: Defaults to 30-day "Champion" and "Recency" logic (50/50 weight).
- **Symbol**: Defaults to `BTC/USDT` (Analysis) and `BTC_THB` (Trading).


