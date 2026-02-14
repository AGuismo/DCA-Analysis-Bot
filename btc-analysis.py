import ccxt
import pandas as pd
import requests
import os
import sys
from datetime import datetime, timedelta, timezone

# --- Config ---
EXCHANGE_ID = os.environ.get("EXCHANGE_ID", "binance")
SYMBOL = "BTC/USDT"
TIMEFRAME = "15m"
LOCAL_TZ = os.environ.get("TIMEZONE", "Asia/Bangkok")
PERIODS = [14, 30, 45, 60]  # Focused on short-term market evolution
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

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

def get_ai_summary(full_report):
    if not GEMINI_API_KEY:
        return "No GEMINI_API_KEY found. Skipping AI analysis."

    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)

        prompt = f"""
        You are a crypto trading analyst. Analyze the following DCA report for BTC/USDT.
        
        KEY METRIC EXPLANATION:
        - "median_miss": The median percentage difference between the close price at that time and the absolute lowest price of that same day. 
          Example: 0.150000 means "Buying at this time is typically only 0.15% away from the perfect daily bottom."
        - "win_rate": The percentage of days where a buy at this time was within 0.5% (a "snipe") of the absolute daily bottom.
          High win_rate = High consistency.
        
        Identify the single best time to buy based on the data.
        Prioritize 'median_miss' (efficiency) and 'win_rate' (reliability).
        Keep it short (max 10 sentences).
        
        Report:
        {full_report}
        """
        
        # Try a list of models in order of preference (Best -> Fastest/Standard)
        # Updates frequently, so falling back is good practice for scripts.
        candidates = [
            'gemini-2.5-flash',
            'gemini-2.0-flash',
            'gemini-2.5-pro',
            'gemini-1.5-flash',
            'gemini-1.5-pro'
        ]

        result_text = None
        last_error = None

        for model_name in candidates:
            try:
                print(f"Trying AI model: {model_name}...")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                result_text = response.text.strip()
                break # Success
            except Exception as e:
                last_error = e
                # creating a short error string to print
                err_str = str(e).split('\n')[0] 
                print(f"  -> Failed: {err_str}...")
        
        if result_text:
            return result_text
        else:
            return f"AI Analysis failed after trying all candidates. Last error: {last_error}"
    except Exception as e:
        return f"AI Analysis failed: {e}"

def send_to_discord(report_content):
    if not DISCORD_WEBHOOK_URL:
        print("No DISCORD_WEBHOOK_URL found. Skipping Discord notification.")
        return

    # Discord has a 2000 char limit. Simple truncation strategy or splitting.
    # For this report, we'll try to keep it concise, or send chunks.
    # A simple approach: Send chunks of 1900 chars
    
    chunks = [report_content[i:i+1900] for i in range(0, len(report_content), 1900)]
    
    for i, chunk in enumerate(chunks):
        payload = {
            "content": f"```\n{chunk}\n```" if i == 0 else f"```\n{chunk}\n```"
        }
        try:
            r = requests.post(DISCORD_WEBHOOK_URL, json=payload)
            r.raise_for_status()
            print(f"Sent chunk {i+1}/{len(chunks)} to Discord")
        except Exception as e:
            print(f"Failed to send to Discord: {e}")

def main():
    report_lines = []
    
    def log(s):
        print(s)
        report_lines.append(s)

    log(f"Fetching max required data ({max(PERIODS)} days)...")
    exchange = getattr(ccxt, EXCHANGE_ID)({"enableRateLimit": True})
    
    # Fetch enough data for the largest period
    rows = fetch_ohlcv_last_n_days(exchange, SYMBOL, TIMEFRAME, max(PERIODS))

    # Process into main DataFrame
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop_duplicates(subset=["ts"]).sort_values("ts")

    # Pre-calculate local times
    df["local_ts"] = df["ts"].dt.tz_convert(LOCAL_TZ)
    df["local_date"] = df["local_ts"].dt.date
    df["local_time"] = df["local_ts"].dt.strftime("%H:%M")

    log(f"Timezone: {LOCAL_TZ}")

    for days in PERIODS:
        log(f"\n{'='*40}")
        log(f" ANALYSIS FOR LAST {days} DAYS")
        log(f"{'='*40}")
        
        try:
            top_common, top_avg, top_dca, start, end = analyze_period(df, days, LOCAL_TZ)
            
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
    
    if GEMINI_API_KEY:
        log("\n" + "="*40)
        log("🤖 AI ANALYSIS (Gemini)")
        log("="*40)
        ai_summary = get_ai_summary(full_report)
        log(ai_summary)
        # Update full_report with new logs
        full_report = "\n".join(report_lines)

    send_to_discord(full_report)

if __name__ == "__main__":
    main()
