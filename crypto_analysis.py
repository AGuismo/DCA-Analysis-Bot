import ccxt
import pandas as pd
import requests
import os
import sys
import re
import json
import google.generativeai as genai
from datetime import datetime, timedelta, timezone

# --- Config ---
EXCHANGE_ID = os.environ.get("EXCHANGE_ID", "binance")
# Support comma-separated list OR JSON array
SYMBOLS_ENV = os.environ.get("SYMBOL", '["BTC/USDT"]')

try:
    # Try parsing as JSON first
    SYMBOLS = json.loads(SYMBOLS_ENV)
    if not isinstance(SYMBOLS, list):
        # If valid JSON but not a list (e.g. string), force list
        SYMBOLS = [str(SYMBOLS)]
except json.JSONDecodeError:
    # Fallback to comma-separated string
    SYMBOLS = [s.strip() for s in SYMBOLS_ENV.split(',')]

TIMEFRAME = "15m"
LOCAL_TZ = os.environ.get("TIMEZONE", "Asia/Bangkok")
PERIODS = [14, 30, 45, 60]  # Focused on short-term market evolution
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DCA_TARGET_MAP_ENV = os.environ.get("DCA_TARGET_MAP", "{}")
try:
    EXISTING_MAP = json.loads(DCA_TARGET_MAP_ENV)
except:
    EXISTING_MAP = {}


# --- Fetch helper ---
def fetch_ohlcv_last_n_days(exchange, symbol, timeframe, days):
    # Add a small buffer to ensure we cover the range fully
    since = int((datetime.now(timezone.utc) - timedelta(days=days + 1)).timestamp() * 1000)
    all_rows = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1500)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        since = last_ts + 1
        # stop if we're mostly caught up
        if last_ts >= int(datetime.now(timezone.utc).timestamp() * 1000) - 3600 * 1000:
            break
    return all_rows

def analyze_period(df, days, local_tz):
    # Filter for the specific lookback period based on the max timestamp in df
    # (assuming df end is "now", so we subtract days from the end time)
    end_time = df["ts"].max()
    start_time = end_time - pd.Timedelta(days=days)
    period_df = df[df["ts"] >= start_time].copy()
    
    # Analysis 1: Most common daily-low time
    idx = period_df.groupby("local_date")["low"].idxmin()
    daily_lows = period_df.loc[idx, ["local_date", "local_time", "low"]]

    most_common = (
        daily_lows["local_time"]
        .value_counts()
        .sort_index()
        .rename("days_won")
        .reset_index()
        .rename(columns={"index": "time"})
    )
    most_common["share"] = most_common["days_won"] / most_common["days_won"].sum()
    top_common = most_common.sort_values("days_won", ascending=False).head(5)

    # Analysis 2: Lowest average Low by time-of-day
    # (Existing arithmetic mean of 'low')
    avg_low_by_time = (
        period_df.groupby("local_time")["low"]
        .mean()
        .reset_index()
        .rename(columns={"local_time": "time", "low": "avg_low"})
        .sort_values("avg_low", ascending=True)
    )
    # Fix: Define top_avg here so it can be returned
    top_avg = avg_low_by_time.head(5)

    # Analysis 3: Advanced DCA Metrics
    # A. Calculate Daily Average (Mean of O,H,L,C for the day)
    period_df["candle_avg"] = period_df[["open", "high", "low", "close"]].mean(axis=1)
    
    # map daily average back to each row
    daily_means = period_df.groupby("local_date")["candle_avg"].transform("mean")
    period_df["diff_from_daily_avg"] = (period_df["close"] - daily_means) / daily_means * 100

    # B. Calculate "Proximity to Daily Low" (The "Regret" Metric)
    # How much did we overpay vs the absolute bottom of that specific day?
    daily_min_low = period_df.groupby("local_date")["low"].transform("min")
    period_df["miss_pct"] = (period_df["close"] - daily_min_low) / daily_min_low * 100
    
    # NEW: Win Rate (Consistency Metric)
    # "Win" = Price is within 0.5% (50bps) of the absolute daily low
    period_df["is_snipe"] = period_df["miss_pct"] < 0.5

    # C. Group by time
    dca_group = period_df.groupby("local_time")

    # Harmonic Mean function for DCA price
    def harmonic_mean(series):
        return len(series) / (1 / series).sum()

    dca_stats = dca_group.agg(
        dca_price=("close", harmonic_mean),
        median_miss=("miss_pct", "median"),
        win_rate=("is_snipe", "mean")
    ).reset_index().rename(columns={"local_time": "time"})
    
    dca_stats["win_rate"] = dca_stats["win_rate"] * 100
    
    # Sort by lowest "miss" from the daily bottom (Median is more robust against crash wicks)
    top_dca = dca_stats.sort_values("median_miss", ascending=True).head(5)

    return top_common, top_avg, top_dca, period_df["ts"].min(), period_df["ts"].max()

def get_ai_summary(full_report, current_symbol):
    if not GEMINI_API_KEY:
        return "No GEMINI_API_KEY found. Skipping AI analysis.", None, None

    try:
        genai.configure(api_key=GEMINI_API_KEY)

        prompt = f"""
        You are a crypto trading analyst. Analyze the following DCA report for {current_symbol}.
        
        KEY METRIC EXPLANATION:
        - "median_miss": The median percentage difference between the close price at that time and the absolute lowest price of that same day. 
          Example: 0.150000 means "Buying at this time is typically only 0.15% away from the perfect daily bottom."
        - "win_rate": The percentage of days where a buy at this time was within 0.5% (a "snipe") of the absolute daily bottom.
          High win_rate = High consistency.
        
        TASK:
        1. Identify the single best time to buy.
        2. APPLY THIS DECISION LOGIC:
           - RECENCY BIAS: If a time slot has a significantly better 'win_rate' (>10% higher) in the 14-day data compared to the 30/60-day data, FAVOR the 14-day time (Market is shifting).
           - STABILITY: If the 14-day data is noisy (low win rates across the board), sticking to the 30-day or 60-day winner is safer.
           - CONSISTENCY: A time that appears in the top 5 across MULTIPLE timeframes is a strong candidate.
        3. OUTPUT A RECOMMENDED TIME.
        
        FORMAT YOUR RESPONSE EXACTLY LIKE THIS (Do not include any other text before or after):
        RECOMMENDED_TIME: HH:MM
        REASON: [Short explanation suitable for Discord notification, max 3 sentences. Mention which timeframe influenced the decision.]

        EXAMPLE RESPONSE:
        RECOMMENDED_TIME: 14:30
        REASON: This time consistently catches the daily low with a 70% win rate and minimal median miss across both 14 and 30 day periods.
        
        Report:
        {full_report}
        """
        
        # Try a list of models in order of preference (Fast -> Fallback)
        # This task needs structured extraction, not deep reasoning.
        # Avoid reasoning-first models (e.g. gemini-3-*) as they add ~60s+ per call.
        candidates = [
            'gemini-2.5-flash',        # Fast and capable (preferred)
            'gemini-2.5-flash-lite',   # Optimized for speed/volume
            'gemini-2.5-pro',          # High-capability fallback
        ]

        result_text = None
        last_error = None

        for model_name in candidates:
            try:
                print(f"Trying AI model: {model_name}...")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                result_text = response.text.strip()
                break # Stop after the first successful model
            except Exception as e:
                last_error = e
                # creating a short error string to print
                err_str = str(e).split('\n')[0] 
                print(f"  -> Failed: {err_str}...")
        
        if result_text:
            # Try to extract the time
            match = re.search(r"RECOMMENDED_TIME:\s*(\d{2}:\d{2})", result_text)
            extracted_time = match.group(1) if match else None
            return result_text, extracted_time, model_name
        else:
            return f"AI Analysis failed after trying all candidates. Last error: {last_error}", None, None
    except Exception as e:
        return f"AI Analysis failed: {e}", None, None

def send_to_discord(report_content):
    if not DISCORD_WEBHOOK_URL:
        print("No DISCORD_WEBHOOK_URL found. Skipping Discord notification.")
        return

    # Discord embeds have a 4096 char limit for description
    # Split into chunks of ~3900 chars to stay safe
    chunks = [report_content[i:i+3900] for i in range(0, len(report_content), 3900)]
    
    for i, chunk in enumerate(chunks):
        payload = {
            "embeds": [{
                "description": f"```\n{chunk}\n```",
                "color": 3447003  # Blue color (same as portfolio balance)
            }]
        }
        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload)
            r.raise_for_status()
            print(f"Sent chunk {i+1}/{len(chunks)} to Discord")
        except Exception as e:
            print(f"Failed to send to Discord: {e}")

def main():
    exchange = getattr(ccxt, EXCHANGE_ID)({"enableRateLimit": True})
    results_map = {}

    for symbol in SYMBOLS:
        print(f"\nExample: PROCESSING {symbol}...")
        report_lines = []
        
        def log(s):
            print(s)
            report_lines.append(s)

        log(f"Fetching max required data ({max(PERIODS)} days) for {symbol}...")
        
        try:
            # Fetch enough data for the largest period
            rows = fetch_ohlcv_last_n_days(exchange, symbol, TIMEFRAME, max(PERIODS))

            # Process into main DataFrame
            df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
            df = df.drop_duplicates(subset=["ts"]).sort_values("ts")

            # Pre-calculate local times
            df["local_ts"] = df["ts"].dt.tz_convert(LOCAL_TZ)
            df["local_date"] = df["local_ts"].dt.date
            df["local_time"] = df["local_ts"].dt.strftime("%H:%M")

            log(f"Timezone: {LOCAL_TZ}")

            best_overall_time = None
            
            for days in PERIODS:
                log(f"\n{'='*40}")
                log(f" ANALYSIS FOR LAST {days} DAYS ({symbol})")
                log(f"{'='*40}")
                
                try:
                    top_common, top_avg, top_dca, start, end = analyze_period(df, days, LOCAL_TZ)
                    
                    # Capture the best time from the 30-day period
                    if days == 30 and not top_dca.empty:
                        best_overall_time = top_dca.iloc[0]['time']
                        log(f"🏆 CHAMPION TIME (30 Days): {best_overall_time}")
                    
                    log(f"Range: {start} -> {end}")
                    
                    log("\n(1) Most frequent DAILY-LOW time:")
                    log(top_common.to_string(index=False))

                    log("\n(2) Best DCA Time (Lowest Median Miss from Daily Low):")
                    # Show price and the average discount relative to daily mean
                    log(top_dca.to_string(index=False))
                    log("* 'median_miss': Median % overpayment vs day's absolute low.")
                    log("* 'win_rate': % of days where the buy was within 0.5% of the absolute low.")
                    
                except Exception as e:
                    log(f"Could not analyze {days} days: {e}")

            # After loop, send to discord
            full_report = "\n".join(report_lines)
            
            final_time = best_overall_time
            source_method = "Quantitative (30d Median Miss)"

            if GEMINI_API_KEY:
                log("\n" + "="*40)
                log("🤖 AI ANALYSIS & RECOMMENDATION")
                log("="*40)
                ai_summary, ai_time, used_model = get_ai_summary(full_report, symbol)
                
                if used_model:
                    log(f"🧠 Model Used: {used_model}")
                    
                log(ai_summary)
                
                if ai_time:
                    log(f"\n✨ AI Recommendation Identified: {ai_time}")
                    if ai_time != final_time:
                        log(f"🔄 Switching target from {final_time} (Math) to {ai_time} (AI)")
                        final_time = ai_time
                        source_method = f"🤖 AI Recommendation"
                    else:
                        log("✅ AI agrees with Quantitative Analysis.")
                        source_method = f"🤝 Consensus (AI + Math)"
                else:
                    log("⚠️ Could not extract valid time from AI. Sticking to math-based time.")

            # Update full_report with new logs
            log(f"\n🎯 FINAL DECISION for {symbol}: {final_time}")
            log(f"ℹ️ SOURCE: {source_method}")
            full_report = "\n".join(report_lines)

            # Send individual report per symbol
            send_to_discord(full_report)
            
            # Map symbol to final time
            # Convert exchange symbol (BTC/USDT) to matching env key format if needed (BTC_THB)
            # Strategy: We assume the user provides specific pairs. 
            # If input is BTC/USDT, we map it to BTC_THB for the trader if that's the convention,
            # OR we just store it as is and let the trader handle the mapping.
            # Ideally the trader looks up by its own SYMBOL env var. 
            # Start simpl: Use the input symbol as the key.
            if final_time:
                log(f"\n🎯 FINAL DECISION for {symbol}: {final_time}")
                
                # Update Strategy:
                # 1. Try to find an existing entry for this symbol (BTC/USDT or BTC_THB)
                # 2. If it's a dict, update ["TIME"].
                # 3. If it's a string, update the string value.
                # 4. If missing, create a new dict entry with default settings.

                # Normalize to THB key if possible (e.g., BTC/USDT -> BTC_THB)
                base = symbol.split('/')[0]
                thb_key = f"{base}_THB"
                
                # Determine which key to update
                target_key = symbol # Default
                if thb_key in EXISTING_MAP:
                    target_key = thb_key
                elif symbol in EXISTING_MAP:
                    target_key = symbol
                else:
                    # New Entry: Prefer THB key for standard
                    target_key = thb_key

                # Update Logic
                if target_key in EXISTING_MAP and isinstance(EXISTING_MAP[target_key], dict):
                    EXISTING_MAP[target_key]["TIME"] = final_time
                    log(f"✅ Updated existing config for '{target_key}' -> TIME: {final_time}")
                elif target_key in EXISTING_MAP:
                     # It's a string (legacy format)
                     EXISTING_MAP[target_key] = final_time
                     log(f"✅ Updated legacy string for '{target_key}' -> {final_time}")
                else:
                    # Create new Dictionary Entry
                    EXISTING_MAP[target_key] = {
                        "TIME": final_time,
                        "AMOUNT": 800, # Default init
                        "BUY_ENABLED": True
                    }
                    log(f"✨ Created new config for '{target_key}' -> {final_time}")


        except Exception as e:
             log(f"CRITICAL FAILURE processing {symbol}: {e}")
             send_to_discord(f"❌ Analysis Failed for {symbol}: {e}")

    # Export the merged map for GitHub Actions
    if EXISTING_MAP:
        # We export the MODIFIED existing map, not just the new results
        # Ensure json format matches what GH expects
        json_map = json.dumps(EXISTING_MAP)
        
        # If running locally without GITHUB_OUTPUT
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"best_time_map={json_map}\n")
        else:
            print(f"DEBUG: (Not in GHA) best_time_map={json_map}")

if __name__ == "__main__":
    main()
