"""
Discord Bot for DCA Automation Control.

Listens for natural language commands in Discord and triggers
GitHub Actions workflows or queries repository variables via the GitHub API.

Setup:
    1. Create a Discord Application at https://discord.com/developers/applications
    2. Enable "Message Content Intent" under Bot settings
    3. Generate a Bot Token and invite the bot to your server with permissions:
       - Send Messages, Read Messages, Add Reactions
    4. Set the required environment variables (see below)
    5. pip install -r bot_requirements.txt
    6. python discord_bot.py

Required environment variables:
    DISCORD_BOT_TOKEN   - Discord bot token (from Discord Developer Portal)
    GEMINI_API_KEY      - Google AI Studio API key (for NL intent classification)
    GH_PAT              - GitHub Personal Access Token (repo scope)
    GITHUB_REPO         - GitHub repo in "owner/repo" format

Optional environment variables:
    DISCORD_CHANNEL_ID  - Restrict bot to one channel (responds to all messages there)
    DISCORD_ALLOWED_USERS - Comma-separated Discord user IDs (security restriction)
"""
import asyncio
import json
import os
import re
import sys
from typing import Optional

import discord
import requests
import google.generativeai as genai


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GH_PAT = os.environ.get("GH_PAT", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

# Optional restrictions
CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID")
ALLOWED_USERS = os.environ.get("DISCORD_ALLOWED_USERS", "")


# ---------------------------------------------------------------------------
# Gemini setup — candidate models in order of preference (fast → fallback)
# ---------------------------------------------------------------------------

genai.configure(api_key=GEMINI_API_KEY)
AI_MODEL_CANDIDATES = [
    "gemini-2.5-flash-lite",   # Optimized for speed/volume
    "gemini-2.5-flash",        # Fast and capable (preferred)
    "gemini-2.5-pro",          # High-capability fallback
]

CLASSIFY_PROMPT = """You are a command classifier for a cryptocurrency DCA automation system.
Given a user message, classify the intent and extract parameters.

IMPORTANT: Users refer to coins by name only — "BTC", "LINK", "SUI", "ETH", "bitcoin", "chainlink", etc.
Never require or expect the user to include "/USDT", "_THB", or any trading pair notation.
Always derive the coin symbol from the name and convert it to the correct internal format.

Available actions:
1. "analyze" - Run crypto market analysis
   - symbols: comma-separated coin names exactly as the user said — e.g. "BTC, LINK, SUI" (default: derive from current DCA config)
     Accept plain names like "BTC", "bitcoin", "link", "chainlink" — do NOT convert to USDT pairs here.
   - short_report: true for AI summary only, false for full breakdown (default: true)

2. "portfolio" - Check portfolio balance
   - short_report: true for balance only, false for full with trade history (default: false)
   - monthly_report: true for entire previous month's trade history (default: false)

3. "update_dca" - Update DCA configuration for a symbol
   - symbol: ALWAYS use the "COIN_THB" format — e.g. "BTC_THB", "LINK_THB", "SUI_THB".
     Convert any coin name or abbreviation the user mentions to this format:
     "btc" → "BTC_THB", "bitcoin" → "BTC_THB", "link" → "LINK_THB", "chainlink" → "LINK_THB".
     Never output COIN/USDT, COIN_USDT, or a bare coin name like "BTC" — always append "_THB".
   - field: one of "TIME", "AMOUNT", "BUY_ENABLED"
   - value: new value (HH:MM for TIME, number 20-1000 for AMOUNT, true/false for BUY_ENABLED)
   Note: "disable X" or "turn off X" means BUY_ENABLED=false; "enable X" or "turn on X" means BUY_ENABLED=true.

4. "status" - Show current DCA configuration

5. "accounts" - Show Ghostfolio portfolio account mapping

6. "help" - Show available commands

7. "unknown" - Message is not a recognized command

Respond with ONLY valid JSON, no markdown fences:
{"action": "...", "params": {...}, "reply": "Brief description of what will be done"}"""


# Valid actions the bot supports
VALID_ACTIONS = {"analyze", "portfolio", "status", "update_dca", "accounts", "help", "unknown"}


def _validate_intent(intent: dict) -> dict:
    """Validate and sanitize the AI-classified intent before use."""
    if not isinstance(intent, dict):
        return {"action": "unknown", "params": {}, "reply": ""}

    action = intent.get("action", "unknown")
    if not isinstance(action, str) or action not in VALID_ACTIONS:
        return {"action": "unknown", "params": {}, "reply": ""}

    params = intent.get("params", {})
    if not isinstance(params, dict):
        params = {}

    # For update_dca, enforce required param types from the AI
    if action == "update_dca":
        symbol = params.get("symbol")
        field = params.get("field")
        value = params.get("value")
        if not isinstance(symbol, str) or not symbol.strip():
            return {"action": "unknown", "params": {}, "reply": "Could not determine symbol"}
        if not isinstance(field, str) or not field.strip():
            return {"action": "unknown", "params": {}, "reply": "Could not determine field"}
        if value is None:
            return {"action": "unknown", "params": {}, "reply": "Could not determine value"}

    return {"action": action, "params": params, "reply": intent.get("reply", "")}


async def classify_intent(text: str) -> dict:
    """Use Gemini to classify user intent from natural language."""
    last_error = None
    prompt = f"{CLASSIFY_PROMPT}\n\nUser message: {text}"

    for model_name in AI_MODEL_CANDIDATES:
        try:
            model = genai.GenerativeModel(model_name)
            response = await asyncio.to_thread(
                model.generate_content,
                prompt,
            )
            raw = response.text.strip()
            # Strip markdown code fences if Gemini wraps them
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            parsed = json.loads(raw)
            result = _validate_intent(parsed)
            print(f"  AI model: {model_name} ✅")
            return result
        except Exception as e:
            last_error = e
            err_str = str(e).split("\n")[0]
            print(f"  AI model {model_name} failed: {err_str}")

    print(f"⚠️ All AI models failed. Last error: {last_error}")
    err_msg = str(last_error).split("\n")[0][:200]
    return {"action": "unknown", "params": {}, "reply": f"All AI models failed: {err_msg}"}


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

GH_HEADERS = {
    "Authorization": f"token {GH_PAT}",
    "Accept": "application/vnd.github.v3+json",
}
GH_API = "https://api.github.com"


def trigger_workflow(workflow_file: str, inputs: Optional[dict] = None) -> bool:
    """Trigger a GitHub Actions workflow via the dispatch API. Returns True on success."""
    url = f"{GH_API}/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/dispatches"
    body = {"ref": "main"}
    if inputs:
        body["inputs"] = inputs
    try:
        r = requests.post(url, json=body, headers=GH_HEADERS, timeout=10)
        return r.status_code == 204
    except Exception as e:
        print(f"❌ GitHub API error: {e}")
        return False


def get_repo_variable(name: str) -> Optional[str]:
    """Fetch a GitHub Actions repository variable value."""
    url = f"{GH_API}/repos/{GITHUB_REPO}/actions/variables/{name}"
    try:
        r = requests.get(url, headers=GH_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json().get("value")
    except Exception as e:
        print(f"❌ GitHub API error: {e}")
    return None


def update_repo_variable(name: str, value: str) -> bool:
    """Update a GitHub Actions repository variable. Returns True on success."""
    url = f"{GH_API}/repos/{GITHUB_REPO}/actions/variables/{name}"
    try:
        r = requests.patch(url, json={"name": name, "value": value}, headers=GH_HEADERS, timeout=10)
        return r.status_code == 204
    except Exception as e:
        print(f"❌ GitHub API error: {e}")
        return False


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _symbols_from_dca_map() -> str:
    """Derive analysis symbols from DCA_TARGET_MAP on GitHub.

    Fetches the current DCA_TARGET_MAP repo variable and converts
    THB trading pair keys to USDT pairs for CCXT analysis.
    Returns comma-separated string like 'BTC/USDT, LINK/USDT, SUI/USDT'.
    Falls back to 'BTC/USDT' if the map cannot be read.
    """
    raw = get_repo_variable("DCA_TARGET_MAP")
    if not raw:
        return "BTC/USDT"
    try:
        target_map = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return "BTC/USDT"

    symbols = []
    for key in target_map:
        if "_THB" in key:
            base = key.replace("_THB", "")
            symbols.append(f"{base}/USDT")
        elif "/" in key:
            symbols.append(key)
        else:
            symbols.append(key)
    result = ", ".join(symbols) if symbols else "BTC/USDT"
    print(f"📋 Derived symbols from DCA_TARGET_MAP: {result}")
    return result


def _to_usdt_pair(coin: str) -> str:
    """Normalise any coin reference to a COIN/USDT pair for CCXT analysis.

    Handles plain names ("BTC", "link"), COIN/USDT, COIN_USDT, COIN_THB, and
    full names mapped via a small lookup table.
    """
    FULL_NAMES: dict = {
        "bitcoin": "BTC", "ethereum": "ETH", "chainlink": "LINK",
        "solana": "SOL", "sui": "SUI", "cardano": "ADA", "ripple": "XRP",
        "dogecoin": "DOGE", "shiba": "SHIB", "polkadot": "DOT",
    }
    raw = coin.strip().lower()
    # Resolve full English names first
    raw = FULL_NAMES.get(raw, raw).upper()
    # Strip known suffixes: COIN/USDT, COIN_USDT, COIN_THB, COIN/THB, etc.
    for sep in ("/USDT", "_USDT", "/BUSD", "_BUSD", "/THB", "_THB", "/USD"):
        if raw.endswith(sep):
            raw = raw[: -len(sep)]
            break
    # Keep only the base if a "/" remains (e.g. "BTC/BNB" edge case)
    if "/" in raw:
        raw = raw.split("/")[0]
    return f"{raw}/USDT"


async def handle_analyze(params: dict, message: discord.Message):
    """Trigger the crypto analysis workflow."""
    symbols_raw = params.get("symbols", "") or ""
    short = params.get("short_report", True)

    if symbols_raw.strip():
        # Normalise plain coin names / any format to COIN/USDT for CCXT
        symbols = ", ".join(
            _to_usdt_pair(s)
            for s in re.split(r"[,\s]+", symbols_raw.strip())
            if s
        )
    else:
        # Fall back to deriving from the live DCA_TARGET_MAP
        symbols = _symbols_from_dca_map()

    inputs = {
        "symbol": str(symbols),
        "short_report": "true" if short else "false",
    }

    if trigger_workflow("crypto_analysis.yml", inputs):
        mode = "short" if short else "full"
        await message.reply(f"✅ Analysis triggered for **{symbols}** ({mode} report)")
    else:
        await message.reply("❌ Failed to trigger analysis workflow. Check bot logs.")


async def handle_portfolio(params: dict, message: discord.Message):
    """Trigger the portfolio balance check workflow."""
    short = params.get("short_report", False)
    monthly = params.get("monthly_report", False)

    inputs = {
        "short_report": "true" if short else "false",
        "monthly_report": "true" if monthly else "false",
    }

    if trigger_workflow("portfolio_check.yml", inputs):
        if monthly:
            label = "monthly"
        elif short:
            label = "short"
        else:
            label = "full"
        await message.reply(f"✅ Portfolio check triggered ({label} report)")
    else:
        await message.reply("❌ Failed to trigger portfolio workflow. Check bot logs.")


async def handle_status(params: dict, message: discord.Message):
    """Fetch and display the current DCA_TARGET_MAP configuration."""
    raw = get_repo_variable("DCA_TARGET_MAP")
    if not raw:
        await message.reply("❌ Could not fetch DCA_TARGET_MAP from GitHub")
        return

    try:
        target_map = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        await message.reply(f"⚠️ DCA_TARGET_MAP is malformed:\n```{raw[:500]}```")
        return

    lines = ["**📋 Current DCA Configuration**\n"]
    for symbol, config in target_map.items():
        if isinstance(config, dict):
            enabled = config.get("BUY_ENABLED", True)
            status = "🟢" if enabled else "🔴"
            lines.append(
                f"{status} **{symbol}** — "
                f"Time: `{config.get('TIME', '?')}`, "
                f"Amount: `{config.get('AMOUNT', '?')}` THB, "
                f"Last Buy: `{config.get('LAST_BUY_DATE', 'never')}`"
            )
        else:
            lines.append(f"🟢 **{symbol}** — `{config}`")

    await message.reply("\n".join(lines))


async def handle_update_dca(params: dict, message: discord.Message):
    """Update a field in DCA_TARGET_MAP and save to GitHub."""
    symbol = str(params.get("symbol", "")).upper().strip()
    field = str(params.get("field", "")).upper()
    value = params.get("value")

    if not symbol or not field or value is None:
        await message.reply("❌ Missing required params: `symbol`, `field`, `value`")
        return

    # Normalise symbol to COIN_THB format regardless of what the AI returned
    # e.g. "BTC", "BTC/USDT", "BTC_USDT", "BTC/THB" all → "BTC_THB"
    for sep in ("/USDT", "_USDT", "/BUSD", "_BUSD", "/THB", "/USD"):
        if symbol.endswith(sep):
            symbol = symbol[: -len(sep)]
            break
    if not symbol.endswith("_THB"):
        symbol = f"{symbol}_THB"

    # Validate field
    allowed_fields = {"TIME", "AMOUNT", "BUY_ENABLED"}
    if field not in allowed_fields:
        await message.reply(f"❌ Can only update: {', '.join(sorted(allowed_fields))}")
        return

    # Validate and normalize value
    if field == "TIME":
        val_str = str(value)
        if not re.match(r"^\d{2}:\d{2}$", val_str):
            await message.reply("❌ TIME must be in HH:MM format (e.g., `23:00`)")
            return
        h, m = map(int, val_str.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            await message.reply("❌ TIME must be between 00:00 and 23:59")
            return

    elif field == "AMOUNT":
        try:
            value = float(value)
            if value < 20 or value > 1000:
                raise ValueError("out of range")
            if value == int(value):
                value = int(value)
        except (ValueError, TypeError):
            await message.reply("❌ AMOUNT must be a number between 20 and 1000")
            return

    elif field == "BUY_ENABLED":
        if str(value).lower() in ("true", "yes", "on", "1", "enable", "enabled"):
            value = True
        elif str(value).lower() in ("false", "no", "off", "0", "disable", "disabled"):
            value = False
        else:
            await message.reply("❌ BUY_ENABLED must be true or false")
            return

    # Fetch current map
    raw = get_repo_variable("DCA_TARGET_MAP")
    if not raw:
        await message.reply("❌ Could not fetch current DCA_TARGET_MAP")
        return

    try:
        target_map = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        await message.reply("❌ DCA_TARGET_MAP is malformed, cannot update safely")
        return

    # Verify symbol exists
    if symbol not in target_map:
        available = ", ".join(target_map.keys())
        await message.reply(f"❌ Symbol **{symbol}** not found. Available: {available}")
        return

    if not isinstance(target_map[symbol], dict):
        await message.reply(f"❌ Config for {symbol} is not in dict format, cannot update")
        return

    # Apply update
    old_value = target_map[symbol].get(field)
    target_map[symbol][field] = value

    # Save back to GitHub
    new_json = json.dumps(target_map, separators=(",", ":"))
    if update_repo_variable("DCA_TARGET_MAP", new_json):
        await message.reply(
            f"✅ Updated **{symbol}** → **{field}**: `{old_value}` → `{value}`"
        )
    else:
        await message.reply("❌ Failed to save DCA_TARGET_MAP to GitHub")


async def handle_accounts(params: dict, message: discord.Message):
    """Fetch and display the PORTFOLIO_ACCOUNT_MAP configuration."""
    raw = get_repo_variable("PORTFOLIO_ACCOUNT_MAP")
    if not raw:
        await message.reply("❌ Could not fetch PORTFOLIO_ACCOUNT_MAP from GitHub")
        return

    try:
        account_map = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        await message.reply(f"⚠️ PORTFOLIO_ACCOUNT_MAP is malformed:\n```{raw[:500]}```")
        return

    lines = ["**🏦 Ghostfolio Account Mapping**\n"]
    for symbol, account_id in account_map.items():
        label = "(default fallback)" if symbol == "DEFAULT" else ""
        lines.append(f"• **{symbol}** → `{account_id}` {label}".rstrip())

    await message.reply("\n".join(lines))


HELP_TEXT = """**🤖 DCA Bot — Natural Language Commands**

**Analysis:**
• "Run analysis" / "Analyze BTC and LINK"
• "Full analysis for BTC/USDT" (detailed report)

**Portfolio:**
• "Check portfolio" / "Show my balance"
• "Monthly report" / "Full portfolio report"

**DCA Config:**
• "Show status" / "What's the current config?"
• "Show accounts" / "Portfolio account map"
• "Set BTC amount to 600" / "Change LINK amount to 200"
• "Set BTC time to 22:00"
• "Disable LINK" / "Enable BTC"
✅ AMOUNT range: 20–1000 THB per coin

All commands are interpreted via AI — just type naturally!
"""


async def handle_help(params: dict, message: discord.Message):
    """Show available commands."""
    await message.reply(HELP_TEXT)


# ---------------------------------------------------------------------------
# Discord client
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

ACTION_HANDLERS = {
    "analyze": handle_analyze,
    "portfolio": handle_portfolio,
    "status": handle_status,
    "update_dca": handle_update_dca,
    "accounts": handle_accounts,
    "help": handle_help,
}


@client.event
async def on_ready():
    """Log connection details on startup."""
    print(f"✅ Bot connected as {client.user} (ID: {client.user.id})")
    if CHANNEL_ID:
        print(f"📌 Restricted to channel ID: {CHANNEL_ID}")
    if ALLOWED_USERS:
        print(f"🔒 Allowed user IDs: {ALLOWED_USERS}")
    else:
        print("⚠️ No DISCORD_ALLOWED_USERS set — any user in the channel can trigger actions")


@client.event
async def on_message(message: discord.Message):
    """Process incoming messages and dispatch to action handlers."""
    # Ignore own messages
    if message.author == client.user:
        return

    # Channel restriction: if set, only respond in that channel
    if CHANNEL_ID and str(message.channel.id) != CHANNEL_ID:
        return

    # User restriction: if set, only allow listed users
    if ALLOWED_USERS:
        allowed_ids = [u.strip() for u in ALLOWED_USERS.split(",")]
        if str(message.author.id) not in allowed_ids:
            return

    # If no channel restriction, only respond to @mentions or DMs
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = client.user in message.mentions
    if not CHANNEL_ID and not is_dm and not is_mentioned:
        return

    # Clean the message text (strip bot mention)
    text = message.content
    for mention in message.mentions:
        text = text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
    text = text.strip()

    if not text:
        await message.reply(HELP_TEXT)
        return

    # Classify intent via Gemini (show typing indicator while processing)
    async with message.channel.typing():
        intent = await classify_intent(text)

    action = intent.get("action", "unknown")
    params = intent.get("params", {})

    print(f"[{message.author}] {text} → action={action} params={params}")

    handler = ACTION_HANDLERS.get(action)
    if handler:
        await handler(params, message)
    elif action == "unknown":
        reply = intent.get("reply", "")
        if reply:
            # Truncate to stay under Discord's 2000-char limit
            reply = reply[:300]
            await message.reply(f"❓ I didn't understand that: *{reply}*\nType **help** to see available commands.")
        else:
            await message.reply("❓ I didn't understand that. Type **help** to see available commands.")
    else:
        await message.reply("❓ I didn't understand that. Type **help** to see available commands.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    missing = [v for v in ("DISCORD_BOT_TOKEN", "GEMINI_API_KEY", "GH_PAT", "GITHUB_REPO")
               if not os.environ.get(v)]
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        print("\nRequired:")
        print("  DISCORD_BOT_TOKEN   - Discord bot token")
        print("  GEMINI_API_KEY      - Google AI Studio API key")
        print("  GH_PAT             - GitHub PAT with repo scope")
        print("  GITHUB_REPO        - owner/repo format")
        print("\nOptional:")
        print("  DISCORD_CHANNEL_ID  - Restrict to one channel")
        print("  DISCORD_ALLOWED_USERS - Comma-separated Discord user IDs")
        sys.exit(1)

    print("🚀 Starting DCA Discord Bot...")
    client.run(DISCORD_BOT_TOKEN)
