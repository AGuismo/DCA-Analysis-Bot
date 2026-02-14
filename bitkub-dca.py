import os
import time
import json
import hmac
import hashlib
import requests
import sys
from datetime import datetime

# --- Configuration ---
API_KEY = os.environ.get("BITKUB_API_KEY")
API_SECRET = os.environ.get("BITKUB_API_SECRET")
# Amount of THB to spend
DCA_AMOUNT = float(os.environ.get("DCA_AMOUNT_THB", "350"))
# Trading Pair (e.g., BTC_THB)
SYMBOL = os.environ.get("SYMBOL_THB", "BTC_THB").upper()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

BASE_URL = "https://api.bitkub.com"

def get_server_time():
    """Fetch server timestamp to ensure sync."""
    try:
        r = requests.get(f"{BASE_URL}/api/v3/servertime")
        return int(r.text)
    except:
        return int(time.time() * 1000)

def send_discord_alert(message, is_error=False):
    if not DISCORD_WEBHOOK_URL:
        print(message)
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
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
        print(f"Dicord sent: {message}")
    except Exception as e:
        print(f"Failed to send Discord: {e}")

def bitkub_request(method, endpoint, payload=None):
    if not API_KEY or not API_SECRET:
        raise ValueError("Missing BITKUB_API_KEY or BITKUB_API_SECRET")

    # 1. Sync Time
    ts = str(get_server_time())
    
    # 2. Prepare Payload
    # Use compact separators to match signature expectation
    payload_str = json.dumps(payload, separators=(',', ':')) if payload else ''
    
    # 3. Create Signature
    # Message: timestamp + method + endpoint + payload
    sig_message = f"{ts}{method}{endpoint}{payload_str}"
    
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        sig_message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # 4. Headers
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-BTK-APIKEY': API_KEY,
        'X-BTK-TIMESTAMP': ts,
        'X-BTK-SIGN': signature
    }
    
    # 5. Execute
    url = BASE_URL + endpoint
    print(f"Sending {method} to {url}...")
    try:
        response = requests.request(method, url, headers=headers, data=payload_str)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        # Bitkub returns errors in JSON usually
        try:
            err_json = response.json()
            raise Exception(f"Bitkub API Error: {err_json.get('error', e)}")
        except:
            raise e

def main():
    print(f"--- Starting DCA for {SYMBOL} ---")
    print(f"Amount: {DCA_AMOUNT} THB")

    try:
        # Check balance first (Optional, but good practice)
        # balances = bitkub_request("POST", "/api/v3/market/balances")
        # thb_balance = balances['result']['THB']['available']
        # if thb_balance < DCA_AMOUNT:
        #    raise Exception(f"Insufficient THB Balance: {thb_balance}")

        # Construct Order
        # MARKET BUY: rat=0, typ=market
        order_payload = {
            "sym": SYMBOL,
            "amt": DCA_AMOUNT, # Spend this much THB
            "rat": 0,          # Market price
            "typ": "market"
        }

        # Execute
        result = bitkub_request('POST', '/api/v3/market/place-bid', order_payload)
        
        # Parse Result
        if result.get('error') != 0:
            raise Exception(f"API returned error code: {result.get('error')}")

        res_data = result.get('result', {})
        
        # Extract details (Bitkub API response structure)
        spent_thb = res_data.get('spent', DCA_AMOUNT)
        received_amt = res_data.get('rec', '???')
        rate = res_data.get('rat', 'Market Price') # Often 0 for market orders in response
        order_id = res_data.get('id')
        ts = res_data.get('ts', int(time.time()))
        
        # Format time
        dt_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

        msg = (
            f"✅ **DCA Buy Executed!**\n"
            f"🔹 **Pair:** {SYMBOL}\n"
            f"💰 **Spent:** {spent_thb} THB\n"
            f"📥 **Received:** {received_amt} {SYMBOL.split('_')[0]}\n"
            f"🏷️ **Rate:** {rate}\n"
            f"🕒 **Time:** {dt_str}\n"
            f"🆔 **Order ID:** {order_id}"
        )
        send_discord_alert(msg, is_error=False)

    except Exception as e:
        err_msg = f"❌ **DCA Failed**: {str(e)}"
        send_discord_alert(err_msg, is_error=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
