"""
Portfolio Balance Checker
Fetches balances for all coins in DCA_TARGET_MAP and sends Discord notification
"""
import os
import json
import hmac
import hashlib
import requests
import time
from datetime import datetime, timedelta

# Configuration
API_KEY = os.environ.get("BITKUB_API_KEY")
API_SECRET = os.environ.get("BITKUB_API_SECRET")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
DCA_TARGET_MAP_JSON = os.environ.get("DCA_TARGET_MAP", "{}")
BASE_URL = "https://api.bitkub.com"

# Timezone Configuration
TIMEZONE_NAME = os.environ.get("TIMEZONE", "Asia/Bangkok")
try:
    from zoneinfo import ZoneInfo
    SELECTED_TZ = ZoneInfo(TIMEZONE_NAME)
except ImportError:
    from datetime import timezone
    SELECTED_TZ = timezone(timedelta(hours=7))

def get_server_time():
    """Fetch server timestamp to ensure sync."""
    try:
        r = requests.get(f"{BASE_URL}/api/v3/servertime", timeout=5)
        return int(r.text)
    except:
        return int(time.time())

def bitkub_request(method, endpoint, payload=None, params=None):
    """Make authenticated request to Bitkub API."""
    if not API_KEY or not API_SECRET:
        raise ValueError("Missing BITKUB_API_KEY or BITKUB_API_SECRET")

    ts = str(get_server_time())
    
    # For GET requests with query params, build query string
    query_string = ''
    if method == 'GET' and params:
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        sig_path = f"{endpoint}?{query_string}" if query_string else endpoint
    else:
        sig_path = endpoint
    
    # Build payload for POST requests
    payload_str = json.dumps(payload, separators=(',', ':')) if payload else ''
    
    # Signature message
    sig_message = f"{ts}{method}{sig_path}{payload_str}"
    
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        sig_message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-BTK-APIKEY': API_KEY,
        'X-BTK-TIMESTAMP': ts,
        'X-BTK-SIGN': signature
    }
    
    url = BASE_URL + sig_path
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, timeout=10)
        else:
            response = requests.request(method, url, headers=headers, data=payload_str, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        try:
            err_json = response.json()
            return err_json
        except:
            raise e

def get_thb_usd_rate():
    """Get THB to USD exchange rate from multiple sources."""
    # Try primary source (Frankfurter)
    try:
        url = "https://api.frankfurter.app/latest?from=THB&to=USD"
        r = requests.get(url, timeout=5)
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

def get_historical_thb_usd_rate(date_str):
    """Get historical THB to USD rate for a specific date (YYYY-MM-DD)."""
    # Try Frankfurter for historical rates
    try:
        url = f"https://api.frankfurter.app/{date_str}?from=THB&to=USD"
        r = requests.get(url, timeout=5)
        data = r.json()
        if 'rates' in data and 'USD' in data['rates']:
            return float(data['rates']['USD'])
    except Exception as e:
        pass
    
    # Fallback to current rate if historical fails
    return get_thb_usd_rate()

def get_balances():
    """Fetch wallet balances from Bitkub."""
    result = bitkub_request('POST', '/api/v3/market/balances', {})
    
    if result.get('error') != 0:
        raise Exception(f"Bitkub API Error: {result.get('error')}")
    
    return result.get('result', {})

def get_order_history(symbol, limit=100):
    """Fetch order history for a specific symbol using GET endpoint."""
    params = {
        "sym": symbol,
        "lmt": str(limit)
    }
    result = bitkub_request('GET', '/api/v3/market/my-order-history', params=params)
    
    if result.get('error') != 0:
        print(f"⚠️ Failed to fetch order history for {symbol}: Error {result.get('error')}")
        return []
    
    return result.get('result', [])

def aggregate_buy_orders(coins, days=7.5):
    """Fetch and aggregate all BUY orders for given coins from last N days."""
    cutoff_time = int(time.time()) - int(days * 86400)
    all_orders = {}
    
    for coin in coins:
        # Order history uses coin_THB format (different from ticker API)
        symbol = f"{coin.upper()}_THB"
        orders = get_order_history(symbol, limit=200)
        
        if not orders:
            continue
        
        # Filter for filled buy orders within date range
        buy_orders = []
        for order in orders:
            order_time = order.get('ts', 0)
            # Convert milliseconds to seconds if needed
            if order_time > 10000000000:
                order_time = order_time // 1000
                
            if order.get('side') == 'buy' and order_time >= cutoff_time:
                # For buy orders, 'amount' is the THB amount spent
                amount_thb = float(order.get('amount', 0))
                rate_thb = float(order.get('rate', 0))
                
                # Calculate crypto amount: THB amount / rate
                amount_crypto = amount_thb / rate_thb if rate_thb > 0 else 0
                
                # Get historical USD rate for this trade date
                trade_date = datetime.fromtimestamp(order_time, tz=SELECTED_TZ).strftime('%Y-%m-%d')
                historical_fx = get_historical_thb_usd_rate(trade_date)
                
                buy_orders.append({
                    'order_id': order.get('order_id', 'N/A'),
                    'amount_crypto': amount_crypto,
                    'amount_thb': amount_thb,
                    'rate_thb': rate_thb,
                    'timestamp': order_time,
                    'fx_rate': historical_fx  # Store the historical FX rate
                })
        
        if buy_orders:
            # Sort by timestamp (newest first)
            buy_orders.sort(key=lambda x: x['timestamp'], reverse=True)
            all_orders[coin.upper()] = buy_orders
            print(f"✓ Found {len(buy_orders)} buy orders for {coin} (last {days} days)")
    
    return all_orders

def get_bitkub_prices(coin_list):
    """Fetch current prices from Bitkub TradingView API (same as app uses)."""
    prices = {}
    
    for coin in coin_list:
        try:
            symbol = f"{coin.upper()}_THB"
            # Get current day's data
            to_ts = int(time.time())
            from_ts = to_ts - 86400  # 24 hours ago
            
            url = f"https://api.bitkub.com/tradingview/history?symbol={symbol}&resolution=1&from={from_ts}&to={to_ts}"
            r = requests.get(url, timeout=5)
            
            if r.status_code == 200:
                data = r.json()
                
                # Get most recent close price
                if data.get('s') == 'ok' and 'c' in data and data['c']:
                    latest_price = data['c'][-1]  # Last close price
                    prices[coin.upper()] = float(latest_price)
        except Exception as e:
            print(f"⚠️ Failed to fetch {coin} price: {e}")
            continue
    
    print(f"✅ Fetched Bitkub prices for {len(prices)} coins")
    return prices

def send_discord_notification(message):
    """Send Discord webhook notification, splitting if needed."""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ No Discord webhook URL configured")
        return
    
    # Discord embed description limit is 4096 chars
    # If message is too long, split into multiple embeds or use content field
    
    if len(message) <= 4000:
        # Single embed
        payload = {
            "embeds": [{
                "title": "💼 Portfolio Balance Report",
                "description": message,
                "color": 3447003,  # Blue
                "timestamp": datetime.now(SELECTED_TZ).isoformat(),
                "footer": {
                    "text": "DCA Portfolio Tracker"
                }
            }]
        }
        
        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            r.raise_for_status()
            print("✅ Discord notification sent")
        except Exception as e:
            print(f"❌ Failed to send Discord notification: {e}")
    else:
        # Split into multiple messages
        # Find the separator between Part 1 and Part 2
        separator = "════════════════════════════════════════"
        
        if separator in message:
            parts = message.split(separator, 1)
            part1 = parts[0].strip()
            part2 = (separator + parts[1]).strip() if len(parts) > 1 else ""
            
            # Send Part 1
            payload1 = {
                "embeds": [{
                    "title": "💼 Portfolio Balance Report",
                    "description": part1,
                    "color": 3447003,
                    "timestamp": datetime.now(SELECTED_TZ).isoformat()
                }]
            }
            
            try:
                r = requests.post(DISCORD_WEBHOOK_URL, json=payload1, timeout=10)
                r.raise_for_status()
                print("✅ Discord notification (Part 1) sent")
                
                # Send Part 2 if exists
                if part2:
                    time.sleep(0.5)  # Small delay between messages
                    payload2 = {
                        "embeds": [{
                            "description": part2,
                            "color": 3447003,
                            "footer": {
                                "text": "DCA Portfolio Tracker"
                            }
                        }]
                    }
                    r = requests.post(DISCORD_WEBHOOK_URL, json=payload2, timeout=10)
                    r.raise_for_status()
                    print("✅ Discord notification (Part 2) sent")
            except Exception as e:
                print(f"❌ Failed to send Discord notification: {e}")

def main():
    print("--- Portfolio Balance Check ---")
    
    # Parse DCA target map to get coins
    try:
        target_map = json.loads(DCA_TARGET_MAP_JSON)
    except:
        print("⚠️ Failed to parse DCA_TARGET_MAP. Using empty map.")
        target_map = {}
    
    if not target_map:
        print("❌ No coins configured in DCA_TARGET_MAP")
        return
    
    # Extract base symbols from map keys
    # Handle formats: "THB_BTC" (Bitkub native), "BTC_THB", "BTC/USDT"
    coins = []
    for key in target_map.keys():
        if '_' in key:
            parts = key.split('_')
            # If first part is THB, the coin is the second part (THB_BTC -> BTC)
            # Otherwise, coin is the first part (BTC_THB -> BTC)
            if parts[0] == 'THB' and len(parts) > 1:
                base = parts[1]
            else:
                base = parts[0]
        elif '/' in key:
            base = key.split('/')[0]
        else:
            base = key
        
        if base and base not in coins and base != 'THB':
            coins.append(base)
    
    print(f"📋 DCA Target Map Keys: {list(target_map.keys())}")
    print(f"🔍 Extracted coins to check: {coins}")
    
    # Fetch balances
    try:
        balances = get_balances()
    except Exception as e:
        error_msg = f"❌ Failed to fetch balances: {str(e)}"
        print(error_msg)
        send_discord_notification(error_msg)
        return
    
    # Fetch current prices from Bitkub (same API the app uses)
    bitkub_prices = get_bitkub_prices(coins)
    
    # Get FX rate
    fx_rate = get_thb_usd_rate()
    
    # Fetch order history for all coins
    print("\n📜 Fetching order history (last 7.5 days)...")
    order_history = aggregate_buy_orders(coins, days=7.5)
    
    # Build report
    report_lines = []
    total_value_thb = 0
    total_value_usd = 0
    
    for coin in sorted(coins):
        # Get balance (available balance, not including locked)
        balance_data = balances.get(coin, {})
        
        # Handle both formats: {"BTC": 0.123} or {"BTC": {"available": 0.123, "reserved": 0.001}}
        if isinstance(balance_data, dict):
            balance = float(balance_data.get('available', 0))
        else:
            balance = float(balance_data)
        
        if balance == 0:
            # Skip coins with zero balance
            continue
        
        # Get current price from Bitkub
        price_thb = bitkub_prices.get(coin.upper(), 0)
        
        if price_thb > 0:
            print(f"✓ {coin}: ฿{price_thb:,.2f}")
        else:
            print(f"⚠️ No price data for {coin}")
        
        # Calculate values
        value_thb = balance * price_thb
        value_usd = value_thb * fx_rate
        
        total_value_thb += value_thb
        total_value_usd += value_usd
        
        # Format line
        line = (
            f"**{coin}**\n"
            f"  Amount: `{balance:.8f}`\n"
            f"  Price: ฿{price_thb:,.2f}\n"
            f"  Value: ฿{value_thb:,.2f} (${value_usd:,.2f})\n"
        )
        report_lines.append(line)
    
    if not report_lines:
        msg = "📊 No balances found for configured coins."
        print(msg)
        send_discord_notification(msg)
        return
    
    # Build Part 1: Current Portfolio
    part1_lines = ["**📊 PART 1: CURRENT HOLDINGS**\n"]
    part1_lines.extend(report_lines)
    part1_lines.append("\n" + "─" * 40)
    part1_lines.append(
        f"\n**💰 Total Portfolio Value**\n"
        f"  ฿{total_value_thb:,.2f}\n"
        f"  ${total_value_usd:,.2f}\n"
    )
    
    # Build Part 2: Trade History (last 7 days)
    part2_lines = []
    
    if order_history:
        part2_lines.append("\n" + "═" * 40)
        part2_lines.append("\n**📈 PART 2: TRADE HISTORY (Last 7.5 Days)**\n")
        
        for coin in sorted(order_history.keys()):
            orders = order_history[coin]
            
            if not orders:
                continue
            
            part2_lines.append(f"\n**{coin}** ({len(orders)} trade{'' if len(orders) == 1 else 's'})")
            
            for order in orders:
                # Format date
                order_dt = datetime.fromtimestamp(order['timestamp'], tz=SELECTED_TZ)
                date_str = order_dt.strftime('%Y-%m-%d %H:%M')
                
                # Use historical USD rate from the day of trade
                historical_fx = order['fx_rate']
                usd_value = order['amount_thb'] * historical_fx
                usd_rate = order['rate_thb'] * historical_fx
                
                part2_lines.append(
                    f"  • `{date_str}` - `{order['amount_crypto']:.8f} {coin}` [ID: {order['order_id']}]\n"
                    f"    Price: ฿{order['rate_thb']:,.2f} (${usd_rate:,.2f})\n"
                    f"    Spent: ฿{order['amount_thb']:,.2f} (${usd_value:,.2f})"
                )
    else:
        part2_lines.append("\n" + "═" * 40)
        part2_lines.append("\n**📈 PART 2: TRADE HISTORY (Last 7.5 Days)**\n")
        part2_lines.append("\n_No trades in the last 7.5 days_")
    
    # Combine both parts
    message = "\n".join(part1_lines + part2_lines)
    
    print("\n" + message)
    send_discord_notification(message)
    print("\n✅ Portfolio balance check complete")

if __name__ == "__main__":
    main()
