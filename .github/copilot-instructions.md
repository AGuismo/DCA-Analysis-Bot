# Project Instructions

## AI / Automated Agent Rules

1. **NO AUTOMATED COMMITS**: Under no circumstances should an AI agent or script commit code to this repository automatically.
2. **NO AUTOMATED PUSHES**: Never push to `main` or any branch without explicit, confirmed user command.
3. **READ-ONLY GIT**: All git operations performed by agents must be read-only (e.g., `git status`, `git log`, `git diff`).

---

## Project Overview

This is a **Python 3.9** cryptocurrency DCA (Dollar-Cost Averaging) automation system running on **GitHub Actions**. It consists of:

| File | Role |
|------|------|
| `bitkub_client.py` | Shared API client — HMAC signing, server-time sync, FX rates |
| `crypto_analysis.py` | Daily market analysis using CCXT + Gemini AI, updates `DCA_TARGET_MAP` |
| `crypto_dca.py` | Trade executor — reads `DCA_TARGET_MAP`, places market buy orders |
| `portfolio_balance.py` | Portfolio reporter — fetches balances, sends Discord notifications |
| `portfolio_logger.py` | Logs trades to Ghostfolio portfolio tracker |
| `gist_logger.py` | Logs trades to a GitHub Gist as a markdown trade ledger |

Workflows live in `.github/workflows/`. All secrets and config are injected via GitHub Actions environment variables — **never hardcoded**.

---

## Python Style & Quality Standards

### Language & Compatibility
- Target **Python 3.9** — this is what all GitHub Actions workflows use (`python-version: '3.9'`).
- Use `Optional[X]` from `typing` for optional types — **not** `X | None` (that requires Python 3.10+).
- Use f-strings for all string formatting. Do not use `%` or `.format()`.
- Follow **PEP 8**: snake_case for files, functions, and variables; UPPER_CASE for module-level constants.

### Imports
- Standard library imports first, then third-party (`requests`, `ccxt`, etc.), then local modules.
- Only import what is actually used. Remove unused imports immediately.
- Import shared utilities from `bitkub_client` — never re-implement `bitkub_request`, `get_thb_usd_rate`, or `get_historical_thb_usd_rate` in other files.

### Functions & Structure
- Keep functions focused on a single concern. If a function does more than one logical thing, split it.
- Module-level helper functions (e.g., `_harmonic_mean`) are preferred over nested function definitions.
- Use docstrings on all public functions — one-line for simple functions, multi-line for anything with non-obvious behaviour.
- Avoid dead code: remove stubs, unused variables, commented-out blocks, and placeholder functions.

---

## Error Handling

### Never use bare `except:`
Always catch a specific exception class. Use `except Exception` as the minimum acceptable fallback:

```python
# BAD
try:
    ...
except:
    pass

# GOOD
try:
    ...
except Exception as e:
    print(f"Something failed: {e}")
```

### Non-blocking secondary operations
Trade execution is the critical path. Logging to Ghostfolio, Gist, or Discord must **never** crash the trade:

```python
try:
    log_to_ghostfolio(data, symbol, account_id)
except Exception as e:
    print(f"⚠️ Ghostfolio logging error: {e}")
    # Continue — don't re-raise
```

### API error codes
Bitkub returns `{"error": 0}` on success. Always check the error field before reading `result`:

```python
result = bitkub_request('POST', '/api/v3/market/place-bid', payload)
if result.get('error') != 0:
    raise Exception(f"Bitkub API error: {result.get('error')}")
```

### FX rate failures
`get_thb_usd_rate()` returns `0.0` on total failure (both sources tried). Always guard against a zero rate before dividing or displaying USD values:

```python
fx_rate = get_thb_usd_rate()
usd_value = thb_value * fx_rate if fx_rate > 0 else 0
```

### Retry-worthy operations
Operations that update shared state (e.g., `update_repo_variable`) should retry with short sleeps (3 attempts, 2s delay). Fail loudly (raise or send Discord alert) rather than silently continuing with stale state.

---

## Environment Variables & Secrets

- **All secrets and config are injected by GitHub Actions** — read via `os.environ.get(...)`.
- Secrets (`BITKUB_API_KEY`, `BITKUB_API_SECRET`, `DISCORD_WEBHOOK_URL`, etc.) are **never** committed to the repo.
- `bitkub_client.py` reads `BITKUB_API_KEY` / `BITKUB_API_SECRET` at module level and raises `ValueError` if they are missing when `bitkub_request()` is called.
- Configuration variables (`DCA_TARGET_MAP`, `TIMEZONE`, `PORTFOLIO_ACCOUNT_MAP`) are stored as GitHub Actions repository variables and passed via workflow `env:` blocks.
- Always provide a sensible default for optional env vars: `os.environ.get("TIMEZONE", "Asia/Bangkok")`.

---

## Shared Module: `bitkub_client.py`

This is the **single source of truth** for all Bitkub API interaction and FX rate fetching.

- `bitkub_request(method, endpoint, payload=None, params=None)` — authenticated GET/POST
- `get_thb_usd_rate()` — current THB→USD rate with two-source fallback
- `get_historical_thb_usd_rate(date_str)` — historical rate, falls back to current
- `get_server_time()` — Bitkub server timestamp, falls back to `time.time()`

**Do not** duplicate any of these in consumer files. Import them directly:

```python
from bitkub_client import bitkub_request, get_thb_usd_rate
```

---

## Discord Notifications

- All user-facing alerts go through Discord via `DISCORD_WEBHOOK_URL`.
- Use coloured embeds (`color` field) to distinguish message types:
  - `0x00C851` (green) — success / trade executed
  - `0xFF4444` (red) — error / critical failure
  - `0x33B5E5` (blue) — informational / portfolio report
- The `SHORT_REPORT` env var (string `"true"`/`"false"`) controls report verbosity:
  - Push events → short report (balance only, no trade history)
  - Schedule / manual dispatch → full report (balances + trade history)
- Always handle Discord webhook failures gracefully — a failed notification must never abort a trade.

---

## GitHub Actions Workflows

### Dependency installation
- `daily_dca.yml` and `crypto_analysis.yml` install from `requirements.txt`.
- `portfolio_check.yml` installs only `requests` (sufficient — `bitkub_client.py` only needs stdlib + requests).
- Always use `pip install --upgrade pip && pip install ...` for reliability.
- All workflows use `cache: 'pip'` on the `setup-python` step.

### Workflow design principles
- **Quick Check first**: `daily_dca.yml` runs a pure-bash check before checkout/Python to avoid resource waste on misses.
- **Concurrency groups**: Prevent race conditions — one running instance per workflow group. Trader queues (`cancel-in-progress: false`); analyst cancels stale runs (`cancel-in-progress: true`).
- **Conditional steps**: Use `if: steps.check.outputs.should_run == 'true'` to skip expensive steps when not needed.
- **SHORT_REPORT expression**: Use ternary-like YAML expressions and always default to the safer/shorter mode on automated triggers.

### Adding a new workflow
1. Use `python-version: '3.9'` on the setup-python step.
2. Pass all required secrets via `env:` on the run step — not globally.
3. Install dependencies before running Python.
4. Add a concurrency group to prevent parallel runs.

---

## Data & Configuration

### `DCA_TARGET_MAP` schema
```json
{
  "BTC_THB": {
    "TIME": "23:00",
    "AMOUNT": 800,
    "BUY_ENABLED": true,
    "LAST_BUY_DATE": "2026-02-18"
  }
}
```
- `TIME` is in `HH:MM` local time (controlled by `TIMEZONE`).
- `LAST_BUY_DATE` is updated post-trade to prevent same-day duplicate buys.
- Always parse this with `json.loads()` and guard against malformed JSON with `except (json.JSONDecodeError, ValueError)`.

### Timezone handling
- Use `pytz` or `datetime.timezone` — never assume UTC.
- The `TIMEZONE` env var (default `Asia/Bangkok`) controls all local-time operations.
- Ghostfolio requires UTC timestamps; convert from local before sending.

---

## Testing & Validation

- Before any manual or local test run that touches the Bitkub API, verify `BITKUB_API_KEY` and `BITKUB_API_SECRET` are set.
- For syntax checks across all Python files: `python -m py_compile <file>.py` or `python -c "import ast; ast.parse(open('<file>.py').read())"`.
- Do not add `print` debug statements permanently — use them during debugging and remove before committing.
- Non-production test trades should use small amounts and be verified against Bitkub order history.
