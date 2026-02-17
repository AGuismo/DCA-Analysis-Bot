import os
import requests
from datetime import datetime, timedelta

# Gist Logging Configuration
GIST_ID = os.environ.get("GIST_ID")
GIST_TOKEN = os.environ.get("GIST_TOKEN")

# Timezone Configuration
TIMEZONE_NAME = os.environ.get("TIMEZONE", "Asia/Bangkok")
try:
    from zoneinfo import ZoneInfo
    SELECTED_TZ = ZoneInfo(TIMEZONE_NAME)
except ImportError:
    # Fallback for Python < 3.9 or missing tzdata
    from datetime import timezone
    SELECTED_TZ = timezone(timedelta(hours=7))
    print(f"⚠️ zoneinfo not available. Using UTC+7 offset as fallback for {TIMEZONE_NAME}") 

def get_thb_usd_rate():
    # Try primary source (Frankfurter)
    try:
        url = "https://api.frankfurter.app/latest?from=THB&to=USD"
        r = requests.get(url, timeout=5)
        # Frankfurt might return empty if closed market but usually returns last rate.
        data = r.json()
        if 'rates' in data and 'USD' in data['rates']:
            return float(data['rates']['USD'])
    except Exception as e:
        print(f"Primary FX source failed: {e}")

    # Try secondary source (Open Exchange Rate API)
    try:
        url = "https://open.er-api.com/v6/latest/THB"
        r = requests.get(url, timeout=5)
        data = r.json()
        if 'rates' in data and 'USD' in data['rates']:
             return float(data['rates']['USD'])
    except Exception as e:
         print(f"Secondary FX source failed: {e}")

    # All sources failed - return 0
    print("❌ ERROR: All FX rate sources failed. USD values will be unavailable.")
    return 0.0

def update_gist_log(trade_data, symbol="BTC"):
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
        
        # Use single file strategy
        files_map = gist_obj['files']
        if len(files_map) > 0:
            filename = list(files_map.keys())[0]
            current_content = files_map[filename]['content']
        else:
            filename = "trade_log.md"
            current_content = ""
            
        # 2. Format new row
        ts = datetime.fromtimestamp(trade_data['ts'], tz=SELECTED_TZ)
        datetime_str = ts.strftime("%Y-%m-%d %H:%M %Z")
        
        # Calculate USD Value of the purchase directly from THB amount
        if fx_rate > 0:
            usd_value = trade_data['amount_thb'] * fx_rate
        else:
            # Fallback if FX API failed: use the passed Bitcoin USD rate to estimate
            # (BTC Amount * BTC Price USD)
            usd_value = trade_data.get('amount_btc', 0) * trade_data.get('usd_rate', 0)

        # Check if header exists, if not add it
        header_line = "| Date                 | THB Spent | USD Spent | Price (THB)    | Price (USD)    | Crypto             | Saved |\n"
        
        if "Date" not in current_content:
            current_content = header_line + current_content
            
        # Append symbol to crypto amount for clarity (e.g. 0.0001 BTC)
        crypto_val = f"{trade_data['amount_btc']:.8f} {symbol}"
        
        # Calculate USD price per crypto unit
        usd_price = (usd_value / trade_data['amount_btc']) if trade_data['amount_btc'] > 0 else 0
        
        # Format row with fixed column widths
        row = f"| {datetime_str:20} | {trade_data['amount_thb']:>9.2f} | ${usd_value:>8.2f} | {trade_data['price']:>14,.2f} | ${usd_price:>13,.2f} | {crypto_val:18} | {'false':5} |"
        
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
        print(f"✅ Gist log updated for {symbol} in {filename}.")
        
    except Exception as e:
        print(f"❌ Failed to update Gist: {e}")

if __name__ == "__main__":
    # Test execution
    print("Testing Gist Logger...")
    # Pre-fetch rate to show user what is being used
    current_rate = get_thb_usd_rate()
    print(f"Current FX Rate (THB -> USD): {current_rate}")

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
