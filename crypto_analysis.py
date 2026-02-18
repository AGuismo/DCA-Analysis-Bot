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
SHORT_REPORT = os.environ.get("SHORT_REPORT", "true").lower() == "true"
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
        You are a crypto DCA timing analyst. Your job is to choose ONE daily buy time (HH:MM) for {current_symbol} from the report below.

        METRICS (from the report):
        - median_miss: Median % overpayment vs the day’s absolute low. LOWER is better. This is the PRIMARY objective.
        - win_rate: % of days where buying at that time was within 0.5% of the absolute low. HIGHER is better. This is SECONDARY (stability).

        IMPORTANT:
        - median_miss is robust; win_rate depends on the 0.5% threshold and can be noisy.
        - Do NOT invent numbers. Only use values in the report.
        - Only choose times that appear in the report’s “Best DCA Time” tables.

        TASK:
        1) Pick ONE RECOMMENDED_TIME using the decision rules below.
        2) Give a short reason (max 3 sentences) mentioning which timeframe(s) drove the decision.

        DECISION RULES (follow in order):
        A) Recency Shift Check (14-day override)
        - Identify the best 14-day candidate by PRIMARY objective (lowest median_miss).
        - Only override longer timeframes with the 14-day candidate if BOTH are true:
        1) The 14-day candidate win_rate is >= 10 percentage points higher than the best 30-day candidate win_rate, AND
        2) The 14-day candidate median_miss is not worse than the best 30-day candidate by more than 0.20 percentage points.
        - If these conditions are NOT met, ignore the 14-day winner (treat as noise).

        B) Base Selection (30/60-day weighted, median_miss-first)
        - Compute the base choice by comparing the 30-day and 60-day best candidates (lowest median_miss in each timeframe).
        - Prefer the 60-day best candidate unless the 30-day best median_miss is better by >= 0.15 percentage points (recent improvement).

        C) Consistency Bonus (only as tie-break)
        - If multiple candidates are within 0.10 percentage points median_miss of the current choice in the chosen base timeframe:
        - Pick the one that appears in the Top 5 across the most timeframes (30/45/60).
        - If still tied, pick the higher win_rate in the 60-day table.
        - If still tied, pick the earlier time (HH:MM).

        OUTPUT FORMAT (exactly, no extra text):
        RECOMMENDED_TIME: HH:MM
        REASON: <max 5 sentences, cite which rules/timeframes caused the decision>

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
                "description": chunk,
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
        summary_lines = []  # For short report
        
        def log(s, summary_only=False):
            """Log to console and report. If summary_only=True, only add to summary."""
            if summary_only:
                # Summary content - always printed, added to summary lines
                print(s)
                summary_lines.append(s)
                if not SHORT_REPORT:
                    report_lines.append(s)  # In full mode, summary is part of report
            else:
                # Detailed content - always added to report (for AI), conditionally printed
                if not SHORT_REPORT:
                    print(s)
                report_lines.append(s)  # Always build full report for AI analysis

        print(f"Fetching max required data ({max(PERIODS)} days) for {symbol}...")
        if not SHORT_REPORT:
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

            if not SHORT_REPORT:
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

            # After loop, prepare for AI analysis (always use full detailed report)
            final_time = best_overall_time
            source_method = "Quantitative (30d Median Miss)"

            if GEMINI_API_KEY:
                log("\n" + "="*40, summary_only=True)
                log("🤖 AI ANALYSIS & RECOMMENDATION", summary_only=True)
                log("="*40, summary_only=True)
                
                # For AI, we need the full detailed report
                detailed_report = "\n".join(report_lines)
                ai_summary, ai_time, used_model = get_ai_summary(detailed_report, symbol)
                
                if used_model:
                    log(f"🧠 Model Used: {used_model}", summary_only=True)
                    
                log(ai_summary, summary_only=True)
                
                if ai_time:
                    log(f"\n✨ AI Recommendation Identified: {ai_time}", summary_only=True)
                    if ai_time != final_time:
                        log(f"🔄 Switching target from {final_time} (Math) to {ai_time} (AI)", summary_only=True)
                        final_time = ai_time
                        source_method = f"🤖 AI Recommendation"
                    else:
                        log("✅ AI agrees with Quantitative Analysis.", summary_only=True)
                        source_method = f"🤝 Consensus (AI + Math)"
                else:
                    log("⚠️ Could not extract valid time from AI. Sticking to math-based time.", summary_only=True)

            # Build final report for Discord
            log(f"\n🎯 FINAL DECISION for {symbol}: {final_time}", summary_only=True)
            log(f"ℹ️ SOURCE: {source_method}", summary_only=True)
            
            # Determine what to send to Discord
            if SHORT_REPORT:
                full_report = "\n".join(summary_lines)
            else:
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
             error_msg = f"CRITICAL FAILURE processing {symbol}: {e}"
             print(error_msg)
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
