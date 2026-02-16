# Smart DCA Automation (Multi-Symbol Analysis + Execution)

A complete system that automatically analyzes market data to find the best time of day to buy for **multiple cryptocurrencies**, and then executes trades on **Bitkub** (Thai Exchange) automatically.

The system consists of two parts:
1.  **The Analyst (`crypto-analysis.py`)**: Runs daily (06:00 BKK). Checks the last 30 days of price action for **multiple pairs** (e.g., BTC/USDT, LINK/USDT) to find the "Champion Time" for each. It updates a repository variable `DCA_TARGET_MAP`.
2.  **The Trader (`bitkub-dca.py`)**: Triggered frequently (e.g., every 15-30 mins). It checks the `DCA_TARGET_MAP` to see if the current time matches the target for any configured symbol. If matched, it executes the trade and logs details to a Gist.

## Features

- **Multi-Symbol Support**: Analyze and trade multiple pairs independently (e.g., BTC at 23:00, LINK at 23:45).
- **Self-Optimizing**: The buy time adjusts automatically based on the last 30 days of data to find the historical "dip" time.
- **Detailed Logging**: All trades are logged to a single GitHub Gist (`trade_log.md`) for easy tracking.
- **Discord Integration**: Get real-time notifications for trades and errors.
- **Smart Scheduling**: The system checks every 15 minutes to catch the optimal buy window for each currency.

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

### 3. Workflow Configuration (Environment Variables)

**Analysis Workflow (`crypto-analysis.yml`)**:
- `SYMBOL`: Comma-separated list of pairs to analyze (e.g., `'BTC/USDT, LINK/USDT'`). Defaults to this list for scheduled runs.

**Trader Workflow (`daily_dca.yml`)**:
- `DCA_TARGET_MAP`: The JSON map defining time, amount, and enabled status for each coin (derived from repo variable).

