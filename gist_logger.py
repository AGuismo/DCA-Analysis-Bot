import os
import sys
import json
import requests
from datetime import datetime

def log_trade_to_gist(symbol, spent_thb, received_amt, rate, order_id):
    """
    Appends a trade record to a CSV file hosted in a Secret Gist.
    """
    GIST_ID = os.environ.get("GIST_ID")
    GH_TOKEN = os.environ.get("GH_TOKEN")
    FILENAME = os.environ.get("GIST_FILENAME", "portfolio.csv")

    if not GIST_ID or not GH_TOKEN:
        print("⚠️ GIST_ID or GH_TOKEN missing. Skipping portfolio logging.")
        return

    print(f"--- Logging to Gist ({FILENAME}) ---")
    
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    # 1. Get current Gist content
    try:
        r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=headers)
        r.raise_for_status()
        gist_data = r.json()
        
        file_data = gist_data['files'].get(FILENAME)
        
        if not file_data:
            # File doesn't exist, create header
            content = "date,symbol,spent_thb,received_amt,rate,order_id\n"
        else:
            content = file_data['content']
            # Ensure it ends with a newline if not empty
            if content and not content.endswith('\n'):
                content += "\n"

        # 2. Append new trade
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_line = f"{timestamp},{symbol},{spent_thb},{received_amt},{rate},{order_id}"
        
        updated_content = content + new_line
        
        # 3. Update Gist
        payload = {
            "files": {
                FILENAME: {
                    "content": updated_content
                }
            }
        }
        
        r = requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=headers, json=payload)
        r.raise_for_status()
        print(f"✅ Trade logged to Gist successfully: {new_line}")

    except Exception as e:
        print(f"❌ Failed to log to Gist: {e}")

if __name__ == "__main__":
    # Test run
    log_trade_to_gist("TEST_BTC", 100, 0.001, 3000000, "test_id_123")
