import os
import requests
from datetime import datetime

# Gist Logging Configuration
GIST_ID = os.environ.get("GIST_ID")
GIST_TOKEN = os.environ.get("GIST_TOKEN") 

def get_thb_usd_rate():
    try:
        # Fetch current USD/THB rate (free from various APIs, using frankfurter here for simplicity and freedom)
        # 1 USD = X THB. So 1 THB = 1/X USD.
        # Frankfurt API is free. https://api.frankfurter.app/latest?from=THB&to=USD
        url = "https://api.frankfurter.app/latest?from=THB&to=USD"
        r = requests.get(url, timeout=5)
        data = r.json()
        return data['rates']['USD']
    except Exception as e:
        print(f"Failed to fetch THB/USD rate: {e}")
        # Fallback approximation if API fails (e.g. 0.028 approx for 35 THB/USD)
        # checking error is safer than returning 0 resulting in 0 logs
        return 0.0

def update_gist_log(trade_data):
    if not GIST_ID or not GIST_TOKEN:
        print("GIST_ID or GIST_TOKEN not set. Skipping log.")
        return

    try:
        # Fetch THB/USD rate at the time of logging
        fx_rate = get_thb_usd_rate()
        
        headers = {
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        # 1. Get current content
        r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=headers)
        r.raise_for_status()
        gist_obj = r.json()
        
        filename = list(gist_obj['files'].keys())[0]
        current_content = gist_obj['files'][filename]['content']
        
        # 2. Format new row
        # | Date | Time | THB Spent | USD Value | Buy Price (THB) | BTC Received | Order ID |
        ts = datetime.fromtimestamp(trade_data['ts'])
        date_str = ts.strftime("%Y-%m-%d")
        time_str = ts.strftime("%H:%M")
        
        # Calculate USD Value of the purchase directly from THB amount
        if fx_rate > 0:
            usd_value = trade_data['amount_thb'] * fx_rate
        else:
            # Fallback if FX API failed: use the passed Bitcoin USD rate to estimate
            # (BTC Amount * BTC Price USD)
            usd_value = trade_data.get('amount_btc', 0) * trade_data.get('usd_rate', 0)

        # Check if header exists, if not add it
        if "Date" not in current_content:
            header = "| Date | Time | THB Spent | USD Value | Buy Price (THB) | BTC Recv | Order ID | Logged |\n"
            current_content = header + current_content
            
        row = f"| {date_str} | {time_str} | {trade_data['amount_thb']:.2f} | ${usd_value:.2f} | {trade_data['price']:.2f} | {trade_data['amount_btc']:.8f} | {trade_data['order_id']} | false |"
        
        # Ensure newline
        if not current_content.endswith('\n'):
            current_content += '\n'
        
        new_content = current_content + row
        
        # 3. Update
        payload = {
            "files": {
                filename: {"content": new_content}
            }
        }
        requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=headers, json=payload)
        print("✅ Gist log updated.")
        
    except Exception as e:
        print(f"❌ Failed to update Gist: {e}")

if __name__ == "__main__":
    # Test execution
    print("Testing Gist Logger...")
    if not GIST_ID or not GIST_TOKEN:
        print("⚠️  Please set GIST_ID and GIST_TOKEN environment variables to test.")
        print("Example: export GIST_ID='...' && export GIST_TOKEN='...' && python gist_logger.py")
    else:
        dummy_data = {
            "ts": datetime.now().timestamp(),
            "amount_thb": 100.0,
            "price": 1000000.0,
            "amount_btc": 0.0001,
            "usd_rate": 0, # Ignored unless API fails
            "order_id": "TEST_ORDER_123"
        }
        print(f"Payload: {dummy_data}")
        update_gist_log(dummy_data)
