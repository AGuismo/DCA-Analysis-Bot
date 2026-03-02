"""
Portfolio Balance Checker
Fetches balances for all coins in DCA_TARGET_MAP and sends Discord notification
"""
import os
import json
import requests
import time
from datetime import datetime, timedelta

from bitkub_client import bitkub_request, get_thb_usd_rate, get_historical_thb_usd_rate

# Configuration
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
DCA_TARGET_MAP_JSON = os.environ.get("DCA_TARGET_MAP", "{}")
SHORT_REPORT = os.environ.get("SHORT_REPORT", "true").lower() == "true"

# Timezone Configuration
TIMEZONE_NAME = os.environ.get("TIMEZONE", "Asia/Bangkok")
try:
    from zoneinfo import ZoneInfo
    SELECTED_TZ = ZoneInfo(TIMEZONE_NAME)
except ImportError:
    from datetime import timezone
    SELECTED_TZ = timezone(timedelta(hours=7))


def _gha_mask(value: str) -> None:
    """Emit a GitHub Actions masking command so the value is redacted in run logs."""
    if os.environ.get("GITHUB_ACTIONS") == "true" and value:
        print(f"::add-mask::{value}", flush=True)


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

def aggregate_buy_orders(coins, start_ts, end_ts):
    """Fetch and aggregate all BUY orders for given coins within a time range.

    Args:
        coins: List of coin symbols to fetch orders for.
        start_ts: Unix timestamp for the start of the window (inclusive).
        end_ts: Unix timestamp for the end of the window (exclusive).
    """
    all_orders = {}
    _fx_cache: dict = {}  # date_str -> rate; avoids one HTTP call per order

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
                
            if order.get('side') == 'buy' and start_ts <= order_time < end_ts:
                # For buy orders, 'amount' is the THB amount spent
                amount_thb = float(order.get('amount', 0))
                rate_thb = float(order.get('rate', 0))
                
                # Calculate crypto amount: THB amount / rate
                amount_crypto = amount_thb / rate_thb if rate_thb > 0 else 0
                
                # Get historical USD rate for this trade date (cached to avoid per-order HTTP calls)
                trade_date = datetime.fromtimestamp(order_time, tz=SELECTED_TZ).strftime('%Y-%m-%d')
                if trade_date not in _fx_cache:
                    _fx_cache[trade_date] = get_historical_thb_usd_rate(trade_date)
                historical_fx = _fx_cache[trade_date]
                
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
            print(f"✓ Found {len(buy_orders)} buy orders for {coin}")
    
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


def fetch_daily_ohlcv(symbol, from_ts, to_ts):
    """Fetch daily OHLCV candles from Bitkub TradingView API.

    Returns a dict mapping date strings (YYYY-MM-DD in SELECTED_TZ) to candle
    dicts with keys: open, high, low, close.
    """
    url = (
        f"https://api.bitkub.com/tradingview/history"
        f"?symbol={symbol}&resolution=D&from={from_ts}&to={to_ts}"
    )
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            print(f"⚠️ TradingView API returned {r.status_code} for {symbol}")
            return {}

        data = r.json()
        if data.get('s') != 'ok':
            print(f"⚠️ TradingView API status not OK for {symbol}: {data.get('s')}")
            return {}

        timestamps = data.get('t', [])
        required_keys = ('o', 'h', 'l', 'c')
        if not all(k in data for k in required_keys):
            print(f"⚠️ TradingView daily response missing OHLC keys for {symbol}")
            return {}

        candles = {}
        for i in range(len(timestamps)):
            dt = datetime.fromtimestamp(timestamps[i], tz=SELECTED_TZ)
            date_str = dt.strftime('%Y-%m-%d')
            candles[date_str] = {
                'open': float(data['o'][i]),
                'high': float(data['h'][i]),
                'low': float(data['l'][i]),
                'close': float(data['c'][i]),
            }
        return candles
    except Exception as e:
        print(f"⚠️ Failed to fetch daily candles for {symbol}: {e}")
        return {}


def _median(values):
    """Return the median of a list of numbers (no numpy/pandas dependency)."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def analyze_dca_performance(order_history, start_ts, end_ts):
    """Compare actual DCA buy prices against daily OHLCV for timing analysis.

    For every trade in *order_history*, fetches the daily candle from Bitkub and
    computes:
      - miss_pct: how far above the daily low the buy was (%).
      - vs_avg_pct: how far above/below the daily OHLC average (%).
      - range_pos: position within the day's high-low range (0 %% = low, 100 %% = high).
      - timing_cost_thb: extra THB spent compared with buying at the daily low.

    Returns a dict  {coin: [trade_stat, ...]}  or None when no data is available.
    """
    if not order_history:
        return None

    analysis = {}

    for coin, orders in order_history.items():
        symbol = f"{coin}_THB"
        # Fetch daily candles covering the full reporting window (with 1-day buffer)
        candles = fetch_daily_ohlcv(symbol, start_ts - 86400, end_ts + 86400)

        if not candles:
            print(f"⚠️ No candle data for {coin}, skipping DCA analysis")
            continue

        trade_stats = []
        for order in orders:
            trade_date = datetime.fromtimestamp(
                order['timestamp'], tz=SELECTED_TZ
            ).strftime('%Y-%m-%d')
            candle = candles.get(trade_date)

            if not candle or candle['low'] <= 0:
                continue

            day_low = candle['low']
            day_high = candle['high']
            day_range = day_high - day_low
            day_avg = (candle['open'] + day_high + day_low + candle['close']) / 4
            rate = order['rate_thb']

            miss_pct = (rate - day_low) / day_low * 100
            vs_avg_pct = (rate - day_avg) / day_avg * 100
            range_pos = ((rate - day_low) / day_range * 100) if day_range > 0 else 50.0
            timing_cost_thb = order['amount_crypto'] * (rate - day_low)

            trade_stats.append({
                'date': trade_date,
                'rate_thb': rate,
                'amount_thb': order['amount_thb'],
                'amount_crypto': order['amount_crypto'],
                'day_low': day_low,
                'day_high': day_high,
                'day_avg': day_avg,
                'miss_pct': miss_pct,
                'vs_avg_pct': vs_avg_pct,
                'range_pos': range_pos,
                'timing_cost_thb': timing_cost_thb,
                'fx_rate': order['fx_rate'],
            })

        if trade_stats:
            analysis[coin] = trade_stats

    return analysis if analysis else None


def format_dca_analysis(analysis, report_label, fx_rate):
    """Build Discord-ready report lines for the DCA timing analysis."""
    lines = []
    lines.append(f"**🎯 DCA TIMING ANALYSIS ({report_label})**")
    lines.append(
        "_How well did your buy timing perform vs each day's price action?_\n"
    )

    all_miss = []
    all_vs_avg = []
    total_timing_cost_thb = 0.0
    total_timing_cost_usd = 0.0
    total_spent_thb = 0.0
    total_spent_usd = 0.0

    for coin in sorted(analysis.keys()):
        trades = analysis[coin]

        miss_values = [t['miss_pct'] for t in trades]
        vs_avg_values = [t['vs_avg_pct'] for t in trades]
        range_positions = [t['range_pos'] for t in trades]

        median_miss = _median(miss_values)
        median_vs_avg = _median(vs_avg_values)
        mean_range_pos = sum(range_positions) / len(range_positions)
        coin_timing_cost = sum(t['timing_cost_thb'] for t in trades)
        coin_total_spent = sum(t['amount_thb'] for t in trades)
        coin_timing_cost_usd = sum(
            t['timing_cost_thb'] * t['fx_rate'] for t in trades
        )
        coin_spent_usd = sum(t['amount_thb'] * t['fx_rate'] for t in trades)

        # Best / worst trades by miss %
        best = min(trades, key=lambda t: t['miss_pct'])
        worst = max(trades, key=lambda t: t['miss_pct'])

        # Snipe rate: buy was within 0.5 % of the daily low
        snipes = sum(1 for t in trades if t['miss_pct'] < 0.5)
        snipe_rate = snipes / len(trades) * 100

        # Beat daily OHLC average
        beats_avg = sum(1 for t in trades if t['vs_avg_pct'] < 0)
        beats_avg_rate = beats_avg / len(trades) * 100

        # GHA mask sensitive totals
        _gha_mask(f"{coin_timing_cost:,.2f}")
        _gha_mask(f"{coin_timing_cost_usd:,.2f}")
        _gha_mask(f"{coin_total_spent:,.2f}")

        lines.append(
            f"**{coin}** ({len(trades)} trade{'s' if len(trades) != 1 else ''} analyzed)"
        )
        lines.append(f"  Median Miss from Daily Low: **{median_miss:.2f}%**")
        lines.append(
            f"  Avg Range Position: **{mean_range_pos:.0f}%** _(0%=low, 100%=high)_"
        )
        lines.append(
            f"  Snipe Rate (<0.5% miss): **{snipe_rate:.0f}%** ({snipes}/{len(trades)})"
        )
        lines.append(
            f"  Beat Daily Avg: **{beats_avg_rate:.0f}%** ({beats_avg}/{len(trades)})"
        )

        vs_avg_marker = " 🟢" if median_vs_avg < 0 else ""
        lines.append(
            f"  Median vs Daily Avg: **{median_vs_avg:+.2f}%**{vs_avg_marker}"
        )

        best_dt = datetime.strptime(best['date'], '%Y-%m-%d').strftime('%b %d')
        worst_dt = datetime.strptime(worst['date'], '%Y-%m-%d').strftime('%b %d')
        lines.append(f"  Best:  {best_dt} — {best['miss_pct']:.2f}% from low ✨")
        lines.append(f"  Worst: {worst_dt} — {worst['miss_pct']:.2f}% from low")

        lines.append(
            f"  Timing Cost: ฿{coin_timing_cost:,.2f}"
            f" (${coin_timing_cost_usd:,.2f})"
            f" of ฿{coin_total_spent:,.2f} (${coin_spent_usd:,.2f}) total spent"
        )
        lines.append("")

        all_miss.extend(miss_values)
        all_vs_avg.extend(vs_avg_values)
        total_timing_cost_thb += coin_timing_cost
        total_timing_cost_usd += coin_timing_cost_usd
        total_spent_thb += coin_total_spent
        total_spent_usd += coin_spent_usd

    # --- Overall summary ---
    if all_miss:
        overall_median_miss = _median(all_miss)
        overall_median_vs_avg = _median(all_vs_avg)
        efficiency = (
            100 - (total_timing_cost_thb / total_spent_thb * 100)
            if total_spent_thb > 0
            else 100
        )

        _gha_mask(f"{total_timing_cost_thb:,.2f}")
        _gha_mask(f"{total_timing_cost_usd:,.2f}")
        _gha_mask(f"{total_spent_thb:,.2f}")
        _gha_mask(f"{total_spent_usd:,.2f}")

        lines.append("**💡 OVERALL**")
        lines.append(
            f"  Portfolio Median Miss: **{overall_median_miss:.2f}%**"
        )
        lines.append(
            f"  Portfolio Median vs Avg: **{overall_median_vs_avg:+.2f}%**"
        )
        lines.append(
            f"  Total Timing Cost: ฿{total_timing_cost_thb:,.2f}"
            f" (${total_timing_cost_usd:,.2f})"
            f" of ฿{total_spent_thb:,.2f} (${total_spent_usd:,.2f}) total spent"
            f" _(historical FX rates)_"
        )
        lines.append(f"  Timing Efficiency: **{efficiency:.2f}%**")

    return lines


def send_discord_notification(message):
    """Send Discord webhook notification, splitting intelligently if needed."""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ No Discord webhook URL configured")
        return
    
    MAX_LENGTH = 4000  # Safe limit for Discord embeds
    
    # Check if message fits in one embed
    if len(message) <= MAX_LENGTH:
        payload = {
            "embeds": [{
                "title": "💼 Portfolio Balance Report",
                "description": message,
                "color": 3447003,
                "timestamp": datetime.now(SELECTED_TZ).isoformat(),
                "footer": {"text": "DCA Portfolio Tracker"}
            }]
        }
        
        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            r.raise_for_status()
            print("✅ Discord notification sent")
        except Exception as e:
            print(f"❌ Failed to send Discord notification: {e}")
        return
    
    # Message is too long - split intelligently
    separator = "════════════════════════════════════════"
    
    if separator not in message:
        # Fallback: just chunk the message
        chunks = [message[i:i+MAX_LENGTH] for i in range(0, len(message), MAX_LENGTH)]
        for i, chunk in enumerate(chunks):
            payload = {"embeds": [{"description": chunk, "color": 3447003}]}
            try:
                if i > 0:
                    time.sleep(0.5)
                requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10).raise_for_status()
                print(f"✅ Discord chunk {i+1}/{len(chunks)} sent")
            except Exception as e:
                print(f"❌ Failed to send chunk {i+1}: {e}")
        return
    
    # Split at separator
    parts = message.split(separator, 1)
    part1 = parts[0].strip()
    part2_full = parts[1].strip() if len(parts) > 1 else ""
    
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
        requests.post(DISCORD_WEBHOOK_URL, json=payload1, timeout=10).raise_for_status()
        print("✅ Discord notification (Part 1) sent")
    except Exception as e:
        print(f"❌ Failed to send Part 1: {e}")
        return
    
    if not part2_full:
        return
    
    # Handle Part 2
    if len(part2_full) <= MAX_LENGTH:
        # Part 2 fits in one message
        time.sleep(0.5)
        payload2 = {
            "embeds": [{
                "description": part2_full,
                "color": 3447003,
                "footer": {"text": "DCA Portfolio Tracker"}
            }]
        }
        try:
            requests.post(DISCORD_WEBHOOK_URL, json=payload2, timeout=10).raise_for_status()
            print("✅ Discord notification (Part 2) sent")
        except Exception as e:
            print(f"❌ Failed to send Part 2: {e}")
        return
    
    # Part 2 is too long - split by coin
    lines = part2_full.split('\n')
    header_lines = []
    coin_sections = []
    current_coin_lines = []
    
    for line in lines:
        # Check if this is a coin header (e.g., "**BTC** (19 trades)")
        # Exclude the main TRADE HISTORY header
        if (line.strip().startswith('**') and '(' in line and 'trade' in line.lower() 
            and 'HISTORY' not in line and 'Last' not in line):
            # Save previous section
            if current_coin_lines:
                coin_sections.append('\n'.join(current_coin_lines))
            # Start new section
            current_coin_lines = [line]
        elif current_coin_lines:
            current_coin_lines.append(line)
        else:
            header_lines.append(line)
    
    # Save last section
    if current_coin_lines:
        coin_sections.append('\n'.join(current_coin_lines))
    
    # Combine header with first coin section
    if coin_sections:
        # Add header to first coin section
        if header_lines:
            header_text = '\n'.join(header_lines)
            coin_sections[0] = header_text + '\n' + coin_sections[0]
    elif header_lines:
        # No coin sections but we have a header - send it alone
        time.sleep(0.5)
        header_text = '\n'.join(header_lines)
        payload = {"embeds": [{"description": header_text, "color": 3447003}]}
        try:
            requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10).raise_for_status()
            print("✅ Discord notification (Part 2 header) sent")
        except Exception as e:
            print(f"❌ Failed to send Part 2 header: {e}")
        return
    
    # Send each coin section
    for i, section in enumerate(coin_sections):
        time.sleep(0.5)
        
        # If single section is still too long, chunk it
        if len(section) > MAX_LENGTH:
            section_lines = section.split('\n')
            chunks = []
            current_chunk = []
            current_length = 0
            header_line = section_lines[0] if section_lines else ""
            
            for sline in section_lines:
                line_length = len(sline) + 1
                if current_length + line_length > MAX_LENGTH - 100 and current_chunk:
                    chunks.append('\n'.join(current_chunk))
                    current_chunk = [header_line + " (cont.)", sline]
                    current_length = len(header_line) + line_length + 8
                else:
                    current_chunk.append(sline)
                    current_length += line_length
            
            if current_chunk:
                chunks.append('\n'.join(current_chunk))
            
            for j, chunk in enumerate(chunks):
                if j > 0:
                    time.sleep(0.5)
                payload = {"embeds": [{"description": chunk, "color": 3447003}]}
                try:
                    requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10).raise_for_status()
                    print(f"✅ Discord notification (Coin {i+1} part {j+1}/{len(chunks)}) sent")
                except Exception as e:
                    print(f"❌ Failed to send coin {i+1} part {j+1}: {e}")
        else:
            # Section fits in one message
            payload = {"embeds": [{"description": section, "color": 3447003}]}
            try:
                requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10).raise_for_status()
                print(f"✅ Discord notification (Coin {i+1}) sent")
            except Exception as e:
                print(f"❌ Failed to send coin {i+1}: {e}")

def main():
    print("--- Portfolio Balance Check ---")
    
    # Parse DCA target map to get coins
    try:
        target_map = json.loads(DCA_TARGET_MAP_JSON)
    except Exception:
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
    if fx_rate == 0:
        fx_error_msg = (
            "⚠️ **FX Rate Fetch Failed**\n"
            "USD values in this report are unavailable.\n"
            "All currency exchange API sources failed."
        )
        send_discord_notification(fx_error_msg)

    # Fetch order history for all coins (only if full report)
    order_history = {}
    if not SHORT_REPORT:
        now = datetime.now(SELECTED_TZ)

        # Compute the 5th-to-5th monthly reporting window (07:00 BKK)
        end_dt = now.replace(day=5, hour=7, minute=0, second=0, microsecond=0)
        if now < end_dt:
            # Haven't reached this month's 5th yet — report the previous window
            if end_dt.month == 1:
                end_dt = end_dt.replace(year=end_dt.year - 1, month=12)
            else:
                end_dt = end_dt.replace(month=end_dt.month - 1)

        # Start = 5th of the month before end, at 07:00 BKK
        if end_dt.month == 1:
            start_dt = end_dt.replace(year=end_dt.year - 1, month=12)
        else:
            start_dt = end_dt.replace(month=end_dt.month - 1)

        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())
        report_label = f"{start_dt.strftime('%b %d')} → {end_dt.strftime('%b %d, %Y')}"

        print(f"\n📜 Fetching monthly order history: {report_label}")
        order_history = aggregate_buy_orders(coins, start_ts=start_ts, end_ts=end_ts)
    
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

        # Mask sensitive values before they appear in any log line
        _gha_mask(f"{balance:.8f}")
        _gha_mask(f"{price_thb:,.2f}")

        if price_thb > 0:
            print(f"✓ {coin}: ฿{price_thb:,.2f}")
        else:
            print(f"⚠️ No price data for {coin}")
        
        # Calculate values
        value_thb = balance * price_thb
        value_usd = value_thb * fx_rate if fx_rate > 0 else 0
        _gha_mask(f"{value_thb:,.2f}")
        _gha_mask(f"{value_usd:,.2f}")
        
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
    part1_lines = ["**📊 CURRENT HOLDINGS**\n"]
    part1_lines.extend(report_lines)
    _gha_mask(f"{total_value_thb:,.2f}")
    _gha_mask(f"{total_value_usd:,.2f}")
    part1_lines.append(
        f"\n**💰 Total Portfolio Value**\n"
        f"฿{total_value_thb:,.2f}\n"
        f"${total_value_usd:,.2f}\n"
    )
    
    # Build Part 2: Trade History (last 7.5 days) - only if full report
    part2_lines = []
    
    if not SHORT_REPORT and order_history:
        part2_lines.append("\n" + "═" * 40)
        part2_lines.append(f"**📈 TRADE HISTORY ({report_label})**\n")
        
        for coin in sorted(order_history.keys()):
            orders = order_history[coin]
            
            if not orders:
                continue

            total_crypto = sum(o['amount_crypto'] for o in orders)
            total_thb = sum(o['amount_thb'] for o in orders)
            total_usd = sum(o['amount_thb'] * o['fx_rate'] for o in orders)

            part2_lines.append(
                f"\n**{coin}** ({len(orders)} trade{'' if len(orders) == 1 else 's'})"
                f" — Crypto amount: `{total_crypto:.8f}` — Spent: ฿{total_thb:,.2f} (${total_usd:,.2f})"
            )
            
            for order in orders:
                # Mask per-order sensitive values
                _gha_mask(str(order['order_id'])[:10])
                _gha_mask(f"{order['amount_crypto']:.8f}")
                _gha_mask(f"{order['amount_thb']:,.2f}")
                _gha_mask(f"{order['rate_thb']:,.2f}")
                # Format date with timezone
                order_dt = datetime.fromtimestamp(order['timestamp'], tz=SELECTED_TZ)
                date_str = order_dt.strftime('%Y-%m-%d %H:%M')
                # Get timezone abbreviation (e.g., ICT) or offset (e.g., +07:00)
                tz_abbr = order_dt.strftime('%Z')  # e.g., ICT
                if not tz_abbr or tz_abbr.startswith('UTC'):
                    # Fallback to offset if no abbreviation
                    tz_offset = order_dt.strftime('%z')  # e.g., +0700
                    tz_str = f"{tz_offset[0]}{int(tz_offset[1:3])}"  # e.g., +7
                else:
                    tz_str = tz_abbr
                
                # Use historical USD rate from the day of trade
                historical_fx = order['fx_rate']
                usd_value = order['amount_thb'] * historical_fx
                usd_rate = order['rate_thb'] * historical_fx
                
                part2_lines.append(
                    f"• {date_str} {tz_str} - {order['amount_crypto']:.8f} {coin} - Order ID: {order['order_id'][:10]} - Price: ฿{order['rate_thb']:,.2f} (${usd_rate:,.2f}) - Spent: ฿{order['amount_thb']:,.2f} (${usd_value:,.2f})"
                )
    elif not SHORT_REPORT:
        # No trades but full report requested
        part2_lines.append("\n" + "═" * 40)
        part2_lines.append(f"\n**📈 TRADE HISTORY ({report_label})**\n")
        part2_lines.append(f"\n_No trades in this period_")
    
    # Combine Parts 1 + 2 into the main message (preserves existing split logic)
    message = "\n".join(part1_lines + part2_lines)

    print("\n" + message)
    send_discord_notification(message)

    # Part 3: DCA Timing Analysis — sent as a separate message to avoid
    # confusing the existing ═══ separator-based split logic in send_discord_notification
    if not SHORT_REPORT and order_history:
        try:
            print("\n📊 Running DCA timing analysis...")
            dca_analysis = analyze_dca_performance(
                order_history, start_ts, end_ts
            )
            if dca_analysis:
                part3_lines = format_dca_analysis(
                    dca_analysis, report_label, fx_rate
                )
                send_discord_notification("\n".join(part3_lines))
            else:
                print("⚠️ No candle data matched trades — skipping timing analysis")
        except Exception as e:
            print(f"⚠️ DCA timing analysis failed: {e}")
            send_discord_notification(f"⚠️ _DCA timing analysis unavailable: {e}_")

    print("\n✅ Portfolio balance check complete")

if __name__ == "__main__":
    main()
