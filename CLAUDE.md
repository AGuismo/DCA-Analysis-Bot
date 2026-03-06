# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Agent Rules

1. **NO AUTOMATED COMMITS OR PUSHES** — all git operations must be read-only unless the user explicitly commands otherwise.
2. **NEVER READ SECRETS** — do not open `run_bot.sh`, `.env`, or any file in `.gitignore` that may contain hardcoded secrets/tokens.

## Project Overview

Python 3.12 cryptocurrency DCA (Dollar-Cost Averaging) automation system running on GitHub Actions with a self-hosted Discord bot. It analyzes markets via CCXT + Gemini AI, executes trades on the Bitkub exchange, and logs to Ghostfolio + GitHub Gist.

## Architecture

### Module Dependency Graph

```
bitkub_client.py  ← shared foundation (HMAC auth, FX rates, server time)
    ├── crypto_dca.py     ← trade executor (imports bitkub_client, gist_logger, portfolio_logger)
    ├── portfolio_balance.py  ← balance reporter (imports bitkub_client)
    └── gist_logger.py    ← trade ledger (imports bitkub_client)

crypto_analysis.py  ← standalone analysis (uses ccxt + pandas + Gemini AI, no bitkub_client)
portfolio_logger.py ← Ghostfolio logger (standalone, uses requests only)
discord_bot.py      ← Discord bot (standalone, triggers GitHub Actions via API)
```

### Data Flow

- `DCA_TARGET_MAP` (GitHub repo variable) is the central config: `{"BTC_THB": {"TIME": "23:00", "AMOUNT": 800, "BUY_ENABLED": true, "LAST_BUY_DATE": ""}}`
- `crypto_analysis.py` updates `TIME` fields via GitHub Actions output → workflow merge step
- `crypto_dca.py` reads the map, executes trades, then updates `LAST_BUY_DATE` via GitHub API with 3-retry logic
- `discord_bot.py` reads/writes `DCA_TARGET_MAP` directly via GitHub API

### Key Patterns

- **Symbol conversion**: THB keys map to USDT pairs for analysis (`BTC_THB` → `BTC/USDT`), back to THB for trading
- **Non-blocking secondary ops**: Trade execution is the critical path; Ghostfolio/Gist/Discord logging must never crash a trade (wrap in `try/except Exception`)
- **Double-buy prevention**: Multi-layer — GitHub Actions concurrency groups, bash quick-check, Python `LAST_BUY_DATE` check, post-trade date update with retry
- **GHA masking**: `_gha_mask()` redacts sensitive values (amounts, order IDs) in GitHub Actions logs
- **Timezone**: All local time ops use `TIMEZONE` env var (default `Asia/Bangkok`); Ghostfolio requires UTC conversion
- **FX rates**: `get_thb_usd_rate()` returns `0.0` on total failure — always guard against zero before dividing

## Workflows (`.github/workflows/`)

| Workflow | Trigger | Python Script | Dependencies |
|---|---|---|---|
| `crypto_analysis.yml` | Daily 23:00 UTC + manual dispatch | `crypto_analysis.py` | `requirements.txt` (ccxt, pandas, google-generativeai, requests) |
| `daily_dca.yml` | Manual dispatch only | `crypto_dca.py` | `requirements.txt` |
| `portfolio_check.yml` | Push to main + monthly 5th + manual | `portfolio_balance.py` | `requests` only (no requirements.txt) |

The analysis workflow has a post-step that merges only `TIME` updates into the live `DCA_TARGET_MAP` (preserving `LAST_BUY_DATE` and other fields).

## Development Commands

```bash
# Syntax check any file
python -m py_compile crypto_dca.py

# Install dependencies (GitHub Actions scripts)
pip install -r requirements.txt

# Install dependencies (Discord bot)
pip install -r bot_requirements.txt

# Run Discord bot locally (requires env vars from .env)
python discord_bot.py

# Docker (Discord bot only)
docker compose up -d --build
docker compose logs -f
```

## Python Conventions

- **Target Python 3.12** — use `X | None` not `Optional[X]`, prefer modern syntax
- **f-strings only** — no `%` or `.format()`
- **PEP 8**: snake_case for functions/variables, UPPER_CASE for module constants
- **Imports**: stdlib → third-party → local modules. Import shared utilities from `bitkub_client` — never re-implement `bitkub_request`, `get_thb_usd_rate`, or `get_historical_thb_usd_rate`
- **Error handling**: Never bare `except:` — use `except Exception as e:` minimum. Bitkub API returns `{"error": 0}` on success; always check before reading `result`
- **Retry pattern**: State-updating operations (e.g., `save_last_buy_date`) use 3 retries with exponential backoff, fail loudly on exhaustion
- **Discord embeds**: Green `0x00C851` for success, red `0xFF4444` for errors, blue `0x33B5E5` / `3447003` for informational

## Environment

All secrets/config are injected via GitHub Actions env vars — never hardcoded. The Discord bot uses its own env vars (`DISCORD_BOT_TOKEN`, `GH_PAT`, `GITHUB_REPO`) and runs separately from GitHub Actions. See README.md for the full secrets/variables table.
