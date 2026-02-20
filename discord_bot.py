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
# Gemini setup — uses flash-lite for fast, cheap intent classification
# ---------------------------------------------------------------------------

genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel("gemini-2.5-flash-lite")

CLASSIFY_PROMPT = """You are a command classifier for a cryptocurrency DCA automation system.
Given a user message, classify the intent and extract parameters.

Available actions:
1. "analyze" - Run crypto market analysis
   - symbols: comma-separated pairs like "BTC/USDT, LINK/USDT" (default: "BTC/USDT, LINK/USDT")
   - short_report: true for AI summary only, false for full breakdown (default: true)

2. "portfolio" - Check portfolio balance
   - short_report: true for balance only, false for full with trade history (default: false)
   - monthly_report: true for entire previous month's trade history (default: false)

3. "status" - Show current DCA configuration

4. "help" - Show available commands

5. "unknown" - Message is not a recognized command

Respond with ONLY valid JSON, no markdown fences:
{"action": "...", "params": {...}, "reply": "Brief description of what will be done"}"""


async def classify_intent(text: str) -> dict:
    """Use Gemini to classify user intent from natural language."""
    try:
        response = await asyncio.to_thread(
            ai_model.generate_content,
            f"{CLASSIFY_PROMPT}\n\nUser message: {text}",
        )
        raw = response.text.strip()
        # Strip markdown code fences if Gemini wraps them
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        print(f"⚠️ Gemini classification failed: {e}")
        return {"action": "unknown", "params": {}, "reply": str(e)}


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


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

async def handle_analyze(params: dict, message: discord.Message):
    """Trigger the crypto analysis workflow."""
    symbols = params.get("symbols", "BTC/USDT, LINK/USDT")
    short = params.get("short_report", True)

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


HELP_TEXT = """**🤖 DCA Bot — Natural Language Commands**

**Analysis:**
• "Run analysis" / "Analyze BTC and LINK"
• "Full analysis for BTC/USDT" (detailed report)

**Portfolio:**
• "Check portfolio" / "Show my balance"
• "Monthly report" / "Full portfolio report"

**DCA Config:**
• "Show status" / "What's the current config?"

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
    else:
        await message.reply(HELP_TEXT)


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
