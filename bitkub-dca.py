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
        # Construct Order
        # MARKET BUY: rat=0, typ=market
        order_payload = {
            "sym": SYMBOL,
            "amt": DCA_AMOUNT, # Spend this much THB
            "rat": 0,          # Market price
            "typ": "market"
        }


        # Execute Order
        result = bitkub_request('POST', '/api/v3/market/place-bid', order_payload)
        
        # Parse Result
        if result.get('error') != 0:
            raise Exception(f"API returned error code: {result.get('error')}")

        initial_res = result.get('result', {})
        order_id = initial_res.get('id')
        
        # --- FETCH ACTUAL FILL DETAILS ---
        # The place-bid response doesn't show actual amount received for market orders
        # We must "wait and check" the order info.
        time.sleep(3) # Give the engine a moment to match, 3s is safer
        
        print(f"Fetching info for Order ID: {order_id}...")
        
        info_payload = {
            "sym": SYMBOL,
            "id": order_id,
            "sd": "buy" # side is required
        }
        
        # Bitkub uses a different endpoint for checking specific order status
        # For V3, it's often GET /api/v3/market/order-info?sym=...&id=...&sd=...
        # But this script is set up for POST primarily. Let's try POST to order-info 
        # (Bitkub supports POST for this endpoint too usually, or we adapt)
        
        # NOTE: Bitkub API V3 /market/order-info takes query params for GET
        # Let's adjust bitkub_request to support GET params properly?
        # Actually simplest is just to use the POST payload if supported, 
        # BUT standard Bitkub doc says GET.
        
        # Let's try to fetch order info via POST which is safer with signature in body
        # API: /api/v3/market/order-info
        # V3 says GET, but typically needs query params. Some V3 support POST.
        # If POST failed with 405 (Method Not Allowed), it means GET is mandatory.
        # But our bitkub_request function is set up for POST (json body).
        
        # We need to construct a GET request with query params and signature.
        # Let's do it manually here to avoid rewriting the whole bitkub_request function right now.
        
        timestamp_for_info = str(get_server_time())
        query_params = f"sym={SYMBOL}&id={order_id}&sd=buy"
        
        # Signature for GET: timestamp + method + endpoint + ? + query_params
        # Bitkub V3 GET signature is typically: timestamp + method + path + query (without ?)
        path_with_query = f"/api/v3/market/order-info?{query_params}"
        
        sig_msg = f"{timestamp_for_info}GET{path_with_query}"
        
        sig_info = hmac.new(
            API_SECRET.encode('utf-8'),
            sig_msg.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        headers_info = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-BTK-APIKEY': API_KEY,
            'X-BTK-TIMESTAMP': timestamp_for_info,
            'X-BTK-SIGN': sig_info
        }
        
        url_info = BASE_URL + path_with_query
        print(f"Sending GET to {url_info}...")
        
        order_info_res_raw = requests.get(url_info, headers=headers_info)
        order_info_res_raw.raise_for_status()
        order_info_res = order_info_res_raw.json()
        
        order_data = order_info_res.get('result', {})
        
        # --- PARSE EXECUTION DETAILS CAREFULLY ---
        # Bitkub "order-info" for Market Buy:
        # 'first' = Initial THB amount requested
        # 'filled' = Total THB amount successfully matched (SPENT)
        # 'history' = Array of trades. Each has 'amount' (Crypto received) and 'rate'.
        
        spent_thb = float(order_data.get('filled', 0))
        if spent_thb == 0:
             # Fallback if 'filled' is 0 (unlikely for market buy unless failed immediately)
             spent_thb = float(order_data.get('total', 0))

        # Calculate received Amount from history
        history = order_data.get('history', [])
        received_amt = 0.0
        
        if history:
            for trade in history:
                trade_amt = float(trade.get('amount', 0))
                trade_rate = float(trade.get('rate', 0))
                # For BUY orders, Bitkub returns 'amount' in THB (Quote currency) for Market Bids
                # We need to calculate Crypto received: THB / Rate
                if trade_rate > 0:
                    received_amt += trade_amt / trade_rate
                else:
                    # Fallback or invalid rate
                    pass
        else:
             # Fallback: sometimes 'amount' in top level might be crypto if type is limit sell, 
             # but for market buy it is THB.
             # If no history, we might have failed to match yet.
             pass

        # Calculate average rate
        if received_amt > 0:
            rate = spent_thb / received_amt
        else:
            rate = 0

        # Timestamp from THIS response (execution time)
        ts = int(order_info_res.get('result', {}).get('ts', time.time()))
        
        # Format time
        dt_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

        # Log to Gist (Optional)
        try:
            log_trade_to_gist(SYMBOL, spent_thb, received_amt, rate, order_id)
        except Exception as gist_err:
            print(f"Gist logging warning: {gist_err}")

        msg = (
            f"✅ **DCA Buy Executed!**\n"
            f"🔹 **Pair:** {SYMBOL}\n"
            f"💰 **Spent:** {spent_thb:.2f} THB\n"
            f"📥 **Received:** {received_amt:.8f} {SYMBOL.split('_')[0]}\n"
            f"🏷️ **Rate:** {rate:,.2f} THB\n"
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
