import os
import time
import json
import hmac
import hashlib
import requests
import sys
from datetime import datetime, timedelta
from gist_logger import update_gist_log

try:
    from zoneinfo import ZoneInfo
    TZ_BKK = ZoneInfo("Asia/Bangkok")
except ImportError:
    from datetime import timezone
    TZ_BKK = timezone(timedelta(hours=7))

# --- Configuration ---
API_KEY = os.environ.get("BITKUB_API_KEY")
API_SECRET = os.environ.get("BITKUB_API_SECRET")

# Default settings (fallback)
DEFAULT_DCA_AMOUNT = 20.0 # Default trade amount if missing in JSON
DEFAULT_TARGET_TIME = os.environ.get("DCA_TARGET_TIME", "07:00")

# Target Map (JSON String)
# New Format: {"BTC_THB": {"TIME": "07:00", "AMOUNT": 800, "BUY_ENABLED": true, "LAST_BUY_DATE": ""}}
DCA_TARGET_MAP_JSON = os.environ.get("DCA_TARGET_MAP", "{}")

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
BASE_URL = "https://api.bitkub.com"

def get_server_time():
    """Fetch server timestamp to ensure sync."""
    try:
        r = requests.get(f"{BASE_URL}/api/v3/servertime", timeout=5)
        return int(r.text)
    except:
        return int(time.time())

def send_discord_alert(message, is_error=False):
    if not DISCORD_WEBHOOK_URL:
        # print(f"[Discord Mock] {message}")
        return

    color = 16711680 if is_error else 65280 # Red or Green
    payload = {
        "embeds": [{
            "title": "Bitkub DCA Execution",
            "description": message,
            "color": color,
            "timestamp": datetime.utcnow().isoformat()
        }]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"Failed to send Discord: {e}")

def get_config_for_symbol(symbol_thb, target_map):
    """
    Resolves the configuration for a given symbol.
    Returns a dict: {"TIME": "HH:MM", "AMOUNT": float, "BUY_ENABLED": bool}
    """
    config = {
        "TIME": DEFAULT_TARGET_TIME, 
        "AMOUNT": DEFAULT_DCA_AMOUNT, 
        "BUY_ENABLED": True,
        "LAST_BUY_DATE": None,
        "KEY": symbol_thb # Store the key used in map for updates later
    }
    
    # keys to check in order: "BTC_THB", "BTC/USDT"
    keys_to_check = [symbol_thb]
    try:
        base = symbol_thb.split('_')[0]
        keys_to_check.append(f"{base}/USDT")
    except:
        pass

    found_entry = None
    target_key = symbol_thb
    
    for key in keys_to_check:
        if key in target_map:
            found_entry = target_map[key]
            target_key = key
            break
            
    config["KEY"] = target_key
            
    if found_entry:
        if isinstance(found_entry, dict):
            # New Format
            config["TIME"] = found_entry.get("TIME", DEFAULT_TARGET_TIME)
            config["AMOUNT"] = float(found_entry.get("AMOUNT", DEFAULT_DCA_AMOUNT))
            config["BUY_ENABLED"] = found_entry.get("BUY_ENABLED", True)
            config["LAST_BUY_DATE"] = found_entry.get("LAST_BUY_DATE", None)
        else:
            # Old Format (String Time)
            config["TIME"] = str(found_entry)
            
    else:
        print(f"⚠️ No config found for {symbol_thb}. Using defaults.")

    return config

def is_time_to_trade(target_time_str):
    """
    Checks if current BKK time matches the target time (HH:MM) within a small window.
    Assumes script runs frequently (e.g. every 15-30 mins).
    We check if current time is within [target, target + 15m).
    """
    now = datetime.now(TZ_BKK)
    current_hm = now.strftime("%H:%M")
    
    # Parse target
    try:
        t_hour, t_minute = map(int, target_time_str.split(':'))
        target_dt = now.replace(hour=t_hour, minute=t_minute, second=0, microsecond=0)
    except:
        print(f"❌ Invalid target time format: {target_time_str}")
        return False
    
    # If target is tomorrow (e.g. now=23:50, target=00:10), this naive compare fails.
    # But usually we run daily cycle. If target is 00:10 and now is 23:50, diff is huge.
    # If target is 23:50 and now is 00:05 (next day), diff is negative.
    # Simple fix: we only care if NOW is "just after" TARGET.
    
    diff = (now - target_dt).total_seconds()
    
    # Handle day wrap for "just after midnight" if target was late night?
    # No, typically cron runs same day. 
    # If target=23:55 and now=00:05, diff is negative huge?
    # Wait: now(00:05) - target(23:55 today) -> target is in future? No.
    # If now is 00:05, target 23:55 of TODAY is in future. Diff is large negative.
    # So we missed yesterday's window.
    
    # Rules:
    # 1. If within +/- 5 mins of target time -> BUY
    # 2. If target time is in the past (today) -> BUY (Catch-up mechanism)
    #    (The catch-up relies on the "Not bought today" check in main loop)
    
    abs_diff = abs(diff)
    
    # Rule 1: Window check (+/- 5 mins = 300s)
    if abs_diff <= 300:
        print(f"✅ Within window (+/- 5m). Diff={diff:.0f}s")
        return True
        
    # Rule 2: Late check (Target passed today)
    # If diff is positive (Now > Target)
    if diff > 0:
        print(f"✅ Target time passed today. Diff={diff:.0f}s. Catch-up mode.")
        return True
        
    return False

def bitkub_request(method, endpoint, payload=None):
    if not API_KEY or not API_SECRET:
        # raise ValueError("Missing BITKUB_API_KEY or BITKUB_API_SECRET")
        pass # Allow running without keys for testing time logic (will fail later on execute)

    ts = str(get_server_time())
    payload_str = json.dumps(payload, separators=(',', ':')) if payload else ''
    sig_message = f"{ts}{method}{endpoint}{payload_str}"
    
    signature = ""
    if API_SECRET:
        signature = hmac.new(
            API_SECRET.encode('utf-8'),
            sig_message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-BTK-APIKEY': API_KEY or "",
        'X-BTK-TIMESTAMP': ts,
        'X-BTK-SIGN': signature
    }
    
    url = BASE_URL + endpoint
    # print(f"Sending {method} to {url}...") # Verbose
    try:
        response = requests.request(method, url, headers=headers, data=payload_str, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        try:
            err_json = response.json()
            error_code = err_json.get('error', 'Unknown')
            # raise Exception(f"Bitkub API Error {error_code}: {err_json}")
            return err_json # Return error dict instead of exception to handle gracefully
        except:
            raise e

import subprocess

def update_repo_variable(map_key, new_last_buy_date):
    """
    Updates the DCA_TARGET_MAP variable in the GitHub repository.
    Because we can't easily modify one key in the JSON via gh cli,
    we have to read the current full map, update it locally, and push the whole JSON.
    NOTE: This is tricky with reading env vars vs repo vars.
    We'll rely on generating a new JSON string valid for the NEXT run.
    """
    # 1. Load Current Map
    # We must use the 'current' map loaded in memory, update it, and push back.
    # But wait, main() has the map. We need to pass it or reload it?
    # Better: This function takes the *whole* map object, modifies it, and pushes.
    pass 

def save_last_buy_date(target_map, symbol_key, date_str):
    print(f"💾 Saving LAST_BUY_DATE for {symbol_key} as {date_str}...")
    
    # Update local object
    if symbol_key not in target_map:
        target_map[symbol_key] = {}
        
    if not isinstance(target_map[symbol_key], dict):
        # Convert simple "07:00" to dict object to support LAST_BUY_DATE
        target_map[symbol_key] = {
            "TIME": str(target_map[symbol_key]),
            "AMOUNT": DEFAULT_DCA_AMOUNT,
            "BUY_ENABLED": True
        }
        
    target_map[symbol_key]["LAST_BUY_DATE"] = date_str
    
    # Serialize
    new_json = json.dumps(target_map)
    
    # Push to GitHub
    # Requires GIST_TOKEN (which we also used as GH_PAT_FOR_VARS)
    token = os.environ.get("GIST_TOKEN") 
    if not token:
        print("⚠️ No GIST_TOKEN found. Cannot update repository variable LAST_BUY_DATE.")
        return

    try:
        # We use 'gh api' to update the variable which is cleaner than 'gh variable set'
        # Endpoint: PATCH /repos/{owner}/{repo}/actions/variables/{name}
        # But we need owner/repo.
        # Simpler: Use 'gh variable set' if 'gh' is installed and auth'd.
        # In GHA, we can perform: echo "$TOKEN" | gh auth login --with-token
        
        # But wait, the environment might not have 'gh' authenticated with the token yet.
        # The workflow step usually does not auth 'gh' by default unless using actions/checkout with token?
        # Actually, we can just use requests against the API if we have the token.
        
        # We need the repo name. GHA provides GITHUB_REPOSITORY.
        repo = os.environ.get("GITHUB_REPOSITORY") # "owner/repo"
        if not repo:
             print("⚠️ GITHUB_REPOSITORY env var missing. Cannot update variable.")
             return
             
        url = f"https://api.github.com/repos/{repo}/actions/variables/DCA_TARGET_MAP"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        data = {"name": "DCA_TARGET_MAP", "value": new_json}
        
        r = requests.patch(url, headers=headers, json=data, timeout=10)
        if r.status_code == 204:
            print("✅ Successfully updated DCA_TARGET_MAP on GitHub.")
        else:
            print(f"⚠️ Failed to update variable: {r.status_code} {r.text}")
            
    except Exception as e:
        print(f"⚠️ Error updating variable: {e}")

def execute_trade(symbol, amount_thb, map_key=None, target_map=None):
    print(f"🚀 Executing DCA Buy for {symbol} ({amount_thb} THB)...")
    
    try:
        # 1. Place Bid
        order_payload = {
            "sym": symbol,
            "amt": amount_thb, 
            "rat": 0, 
            "typ": "market"
        }
        
        result = bitkub_request('POST', '/api/v3/market/place-bid', order_payload)
        
        if result.get('error') != 0:
            raise Exception(f"API Error Code: {result.get('error')}")

        order_id = result.get('result', {}).get('id')
        print(f"   Placed Order ID: {order_id}. Waiting for match...")
        
        # 2. Wait
        time.sleep(3) 

        # 3. Fetch Details
        info_params = f"sym={symbol}&id={order_id}&sd=buy"
        path_query = f"/api/v3/market/order-info?{info_params}"
        ts = str(get_server_time())
        sig_msg = f"{ts}GET{path_query}"
        
        sig = ""
        if API_SECRET:
             sig = hmac.new(API_SECRET.encode('utf-8'), sig_msg.encode('utf-8'), hashlib.sha256).hexdigest()
        
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-BTK-APIKEY': API_KEY,
            'X-BTK-TIMESTAMP': ts,
            'X-BTK-SIGN': sig
        }
        
        r_info = requests.get(BASE_URL + path_query, headers=headers, timeout=10)
        r_info.raise_for_status()
        order_data = r_info.json().get('result', {})
        
        spent_thb = float(order_data.get('filled', 0))
        if spent_thb == 0: spent_thb = float(order_data.get('total', 0))

        history = order_data.get('history', [])
        received_amt = sum(float(t['amount'])/float(t['rate']) for t in history if float(t.get('rate',0)) > 0)
        
        rate = (spent_thb / received_amt) if received_amt > 0 else 0
        ts_exec = int(order_data.get('ts', time.time()))
        dt_str = datetime.fromtimestamp(ts_exec).strftime('%Y-%m-%d %H:%M:%S')

        # 4. Log to Gist
        base_sym = symbol.split('_')[0]
        update_gist_log({
            "ts": ts_exec,
            "amount_thb": spent_thb,
            "price": rate,
            "amount_btc": received_amt, # Generic field name, but holds crypto amount
            "usd_rate": 0, 
            "order_id": order_id
        }, symbol=base_sym) # Pass "BTC" or "LINK"

        # 5. Notify
        msg = (
            f"✅ **DCA Buy Executed!**\n"
            f"🔹 **Pair:** {symbol}\n"
            f"💰 **Spent:** {spent_thb:.2f} THB\n"
            f"📥 **Received:** {received_amt:.8f} {base_sym}\n"
            f"🏷️ **Rate:** {rate:,.2f} THB\n"
            f"🕒 **Time:** {dt_str}\n"
            f"🆔 **Order ID:** {order_id}"
        )
        send_discord_alert(msg, is_error=False)

        # 6. Update LAST_BUY_DATE in DCA_TARGET_MAP
        if map_key and target_map:
            today_str = datetime.now(TZ_BKK).strftime("%Y-%m-%d")
            print(f"🔄 Updating LAST_BUY_DATE for {map_key} to {today_str}...")
            save_last_buy_date(target_map, map_key, today_str)

    except Exception as e:
        err = f"❌ **DCA Failed ({symbol})**: {str(e)}"
        print(err)
        send_discord_alert(err, is_error=True)

def main():
    print(f"--- Starting Bitkub DCA Logic ---")
    
    # Parse Target Map
    try:
        target_map = json.loads(DCA_TARGET_MAP_JSON)
    except:
        print("⚠️ Failed to parse DCA_TARGET_MAP JSON. Using empty map.")
        target_map = {}

    print(f"Target Map Keys: {list(target_map.keys())}")

    # Determine symbols to process
    symbols_to_process = []
    for k in target_map.keys():
        if isinstance(target_map[k], dict):
             # Check if explicitly disabled, if enabled or missing key -> include
             if target_map[k].get("BUY_ENABLED", True):
                 symbols_to_process.append(k)
             else:
                 print(f"🚫 {k} is DISABLED in config. Skipping.")
        else:
             # Legacy string format -> Assume enabled
            symbols_to_process.append(k)
    
    # Clean list
    symbols_to_process = [s.strip() for s in symbols_to_process if s.strip()]

    print(f"Symbols to Process (Enabled): {symbols_to_process}")
    
    # Check explicit "FORCE_RUN" flag (for manual dispatch testing)
    force_run = os.environ.get("FORCE_RUN", "false").lower() == "true"

    for symbol in symbols_to_process:
        print(f"\nPROCESSING {symbol}...")
        
        config = get_config_for_symbol(symbol, target_map)
        
        # BUY_ENABLED check is redundant if we filtered above, but good for safety
        if not config["BUY_ENABLED"]:
            print(f"⛔ Trade Disabled for {symbol}. Skipping.")
            continue
            
        target_time = config["TIME"]
        trade_amount = config["AMOUNT"]
        
        if force_run:
            print("⚠️ Force Run enabled. Skipping time check.")
            execute_trade(symbol, trade_amount, map_key=config["KEY"], target_map=target_map)
        elif is_time_to_trade(target_time):
            # Check LAST_BUY_DATE
            today_str = datetime.now(TZ_BKK).strftime("%Y-%m-%d")
            last_buy = config.get("LAST_BUY_DATE")
            
            if last_buy == today_str:
                print(f"🛑 Already bought {symbol} today ({today_str}). Skipping.")
            else:
                print("✅ Time match & Not bought today! Executing trade.")
                execute_trade(symbol, trade_amount, map_key=config["KEY"], target_map=target_map)
        else:
            print(f"⏳ Not time yet (Target: {target_time}). Skipping.")

if __name__ == "__main__":
    main()
