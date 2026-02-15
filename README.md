# Smart DCA Automation (Analysis + Execution)

A complete system that automatically analyzes market data to find the best time of day to buy, and then executes that trade on **Bitkub** (Thai Exchange) automatically.

The system consists of two parts:
1.  **The Analyst (`crypto-analysis.py`)**: Runs daily (06:00 BKK). Checks the last 30 days of price action to find the "Champion Time" (lowest median absolute miss). It updates a repository variable `DCA_TARGET_TIME`.
2.  **The Trader (`bitkub-dca.py`)**: Triggered externally via API. It checks if the current time matches the `DCA_TARGET_TIME` (±10 mins) and if it hasn't bought yet today. If matched, it buys a fixed THB amount on Bitkub and logs the specific details to a private Gist.

## Features

- **Self-Optimizing**: The buy time adjusts automatically based on recent market behavior.
- **Precision Timing**: Uses an external cron trigger to bypass GitHub Actions' scheduling delays.
- **Private Ledger**: Automatically logs every trade to a private GitHub Gist, including the exact THB spent and the USD value at purchase time.
- **Safety Locks**:
  - **Once-Per-Day**: Uses a `LAST_BUY_DATE` variable to ensure it never double-buys on the same day.
  - **Time Window**: Checks if the current time matches the target (±5 mins). Includes "overshoot protection" to buy anyway if triggered late.
- **Smart Metrics**: 
  - **Median Miss**: Calculates efficiency.
  - **Win Rate**: Calculates consistency.
- **AI Analysis**: Gemini (1.5 Flash/Pro) provides a daily summary of *why* that time was chosen.
- **Discord Integration**: Get reports and trade confirmations directly to your channel.

## Prerequisites

- **Bitkub Account**: API Key and Secret (enable "Trade" permission).
- **Google Gemini API**: For AI summaries (Free tier available).
- **Discord Webhook**: For notifications.
- **GitHub**: Account to host the Actions.
- **GitHub Gist**: A private gist ID for logging trades.

## Setup & Installation

### 1. Secrets (Secure Storage)
Go to `Settings` -> `Secrets and variables` -> `Actions` -> `New repository secret`:

| Secret Name | Value Description |
| :--- | :--- |
| `BITKUB_API_KEY` | Your Bitkub API Key. |
| `BITKUB_API_SECRET` | Your Bitkub API Secret. |
| `GEMINI_API_KEY` | Google AI Studio Key. |
| `DISCORD_WEBHOOK_URL` | Your Discord Webhook URL. |
| `GH_PAT_FOR_VARS` | Personal Access Token (Classic) with `repo` and **`gist`** scope. Used to update variables and write to your log. |
| `GIST_ID` | The ID of your private Gist (see below). |

### 2. Variables (State Management)
Go to `Settings` -> `Secrets and variables` -> `Actions` -> `Variables` -> `New repository variable`:

| Variable Name | Initial Value | Description |
| :--- | :--- | :--- |
| `DCA_TARGET_TIME` | `12:00` | The HH:MM time to buy (will be auto-updated by the analysis). |
| `LAST_BUY_DATE` | `1970-01-01` | Tracks the last successful buy date (prevents double buys). |
| `TIMEZONE` | `Asia/Bangkok` | Your IANA Timezone (e.g., `Asia/Bangkok`, `America/New_York`). |

### 3. Gist Setup (Log Book)

1.  **Create a Gist**: Go to [gist.github.com](https://gist.github.com).
2.  Create a new Gist (Secret Gist recommended). 
    - Filename: `dca_log.md` (or anything you like).
    - Content: Just write "Log Start" or leave it empty.
3.  **Get ID**: Look at the URL after you save. It will look like: `gist.github.com/username/THIS_LONG_STRING`.
    - `THIS_LONG_STRING` is your **GIST_ID**.
    - Add this ID as a secret named `GIST_ID` in your repository.
4.  **Token Rights**:
    - The `GH_PAT_FOR_VARS` token you created needs the **`gist`** scope enabled.
    - Go to GitHub Settings -> Developer Settings -> Tokens (Classic) -> Click your token -> Tick the `gist` box -> Update Token.
    - If you are using a Fine-grained token, give it "Read and Write" access to "Gists".

### 4. External Scheduler (Required)

Because GitHub Actions' internal scheduler can be unreliable (delayed by 30+ minutes), we use an external service to trigger the trade exactly when we want.

**Recommended: cron-job.org (Completely Free, No Credit Card)**

1.  **Create a GitHub `PAT`**: Go to GitHub Settings -> Developer settings -> Personal access tokens -> Tokens (classic). Generate one with `repo` and `workflow` scopes.
2.  **Go to cron-job.org**: Sign up and "Create Cronjob".
    *   **URL**: `https://api.github.com/repos/YOUR_USERNAME/DCA-Analysis/actions/workflows/daily_dca.yml/dispatches`
    *   **Execution Schedule**: Your desired time (e.g. `14:45` daily).
    *   **Timezone**: `Asia/Bangkok`.
    *   **Advanced** -> **HTTP Method**: `POST`.
    *   **Advanced** -> **Request Body**: `{"ref":"main"}`.
    *   **Advanced** -> **Req. Headers**:
        ```text
        Authorization: Bearer YOUR_GITHUB_PAT
        Accept: application/vnd.github.v3+json
        User-Agent: CronJobOrg
        ```

**Alternative: Manual Run**
- Go to the "Actions" tab in GitHub.
- Select "Daily Bitkub DCA".
- Click "Run workflow" -> Enter Amount -> Run.

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

- **Analysis**: Uses Multi-Timeframe (14/30/45/60 days) logic + AI Recommendation to select the optimal time.
- **Symbol**: Defaults to `BTC/USDT` (Analysis) and `BTC_THB` (Trading).


